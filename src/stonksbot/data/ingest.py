"""MOEX ISS -> versioned store bridge for index candles (read-only leg).

Converts fetched ``MoexIssCandle`` rows into ``CandleSnapshot`` rows keyed by
``instrument_uid`` (resolved from the seeded ``instrument_reference`` row by
TICKER, per db-schema §3.1) and persists them through the insert-only
``store_candle_snapshot`` path, so every load gets a fresh ``source_version``
and latest-as-of reads stay honest (data-layer §3.2).

No-lookahead guards baked in (backtest-honesty law):
- ``is_complete`` is REQUIRED per bar and computed as ``ts < complete_before_ts``
  — the current session's possibly-in-progress ISS bar is never marked complete
  (index-leg completeness rule, PROVISIONAL — owner decision 2.4 in
  ``docs/ops/pre-live-owner-decisions.md``). ``CandleSnapshot``'s ``True``
  default is never relied on.
- ``as_of`` is the ingestion wall-clock (epoch-ms UTC), never backdated, so the
  ``as_of <= decision_ts`` read gate cannot fabricate as-of-decision knowledge.

Missing-bar detection (single-source index leg — data-layer §3.4/§5.2): when a
``TradingCalendar`` is supplied, every expected finalized session with no bar
records an idempotent open ``missing_bar`` conflict. Escalation to persistent
conflicts / skip signals stays with the cycle driver. Note the documented
limitation: the calendar is itself derived from IMOEX candles, so IMOEX
missing-bar detection against it is partially self-referential; MCFTR is fully
checkable against the IMOEX calendar.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from stonksbot.data.calendar import TradingCalendar
from stonksbot.data.moex_iss import MoexIssCandle
from stonksbot.data.store import (
    CandleSnapshot,
    record_data_conflict,
    store_candle_snapshot,
)


@dataclass(frozen=True)
class IndexIngestResult:
    instrument_uid: str
    source_version: int
    stored: int
    complete: int
    incomplete: int
    missing_bar_conflict_ids: tuple[int, ...]


def resolve_index_uid(connection: sqlite3.Connection, *, ticker: str) -> str:
    """Resolve an index ticker to its seeded instrument_uid (fail-closed).

    Raises when the row is absent (seed via ``registry.seed_index_reference``
    first — reference refresh precedes candle ingest, data-layer §3.1) or when
    the ticker is ambiguous.
    """
    normalized = ticker.strip().upper()
    if not normalized:
        raise ValueError("ticker must be non-empty")
    rows = connection.execute(
        """
        SELECT instrument_uid
        FROM instrument_reference
        WHERE ticker = ?
          AND instrument_kind = 'index'
        """,
        (normalized,),
    ).fetchall()
    if not rows:
        raise ValueError(
            f"no index instrument_reference row for {normalized}; seed the registry first"
        )
    if len(rows) > 1:
        raise ValueError(f"ambiguous index ticker {normalized}: {len(rows)} reference rows")
    return str(rows[0][0])


def next_source_version(
    connection: sqlite3.Connection,
    *,
    instrument_uid: str,
    interval: str = "1day",
) -> int:
    """Per-ingest-run version: 1 + MAX(source_version) over the instrument's series.

    PROVISIONAL allocation scheme (owner decision 2.3); satisfies the contract's
    "new load = a new source_version, never an in-place UPDATE" (data-layer §3.2).
    """
    row = connection.execute(
        """
        SELECT COALESCE(MAX(source_version), 0)
        FROM candles
        WHERE instrument_uid = ?
          AND interval = ?
        """,
        (instrument_uid, interval),
    ).fetchone()
    return int(row[0]) + 1


def candle_snapshot_from_iss(
    candle: MoexIssCandle,
    *,
    instrument_uid: str,
    source_version: int,
    as_of: int,
    is_complete: bool,
) -> CandleSnapshot:
    """Verbatim MoexIssCandle -> CandleSnapshot mapping; completeness is explicit."""
    if candle.source != "moex_iss":
        raise ValueError(f"unexpected candle source: {candle.source!r}")
    if candle.interval != "1day":
        raise ValueError(f"unexpected candle interval: {candle.interval!r}")
    return CandleSnapshot(
        instrument_uid=instrument_uid,
        ts=candle.ts,
        source_version=source_version,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
        source="moex_iss",
        as_of=as_of,
        interval="1day",
        is_complete=is_complete,
        is_stale=False,
        adjusted=False,  # indices: no split adjustment applies
    )


def ingest_index_candles(
    connection: sqlite3.Connection,
    *,
    ticker: str,
    candles: Sequence[MoexIssCandle],
    as_of: int,
    complete_before_ts: int,
    calendar: TradingCalendar | None = None,
) -> IndexIngestResult:
    """Persist fetched index candles as one new source_version (fail-closed).

    ``complete_before_ts`` (epoch-ms UTC) is the finality cutoff the caller
    computes (e.g. UTC midnight of the current MOEX session date at fetch
    time): bars with ``ts >= complete_before_ts`` are stored ``is_complete=0``
    and stay invisible to entry-safe reads. An empty fetch raises — a blank ISS
    response must never look like a successful ingest.
    """
    normalized = ticker.strip().upper()
    if not normalized:
        raise ValueError("ticker must be non-empty")
    if not candles:
        raise ValueError(f"refusing to ingest zero candles for {normalized}")
    if not isinstance(complete_before_ts, int) or isinstance(complete_before_ts, bool):
        raise TypeError("complete_before_ts must be an int (epoch-ms UTC)")

    seen_ts: set[int] = set()
    for candle in candles:
        if candle.market != "index":
            raise ValueError(f"expected market='index', got {candle.market!r} for {normalized}")
        if candle.secid.strip().upper() != normalized:
            raise ValueError(f"candle secid {candle.secid!r} does not match ticker {normalized}")
        if candle.ts in seen_ts:
            raise ValueError(f"duplicate candle ts {candle.ts} for {normalized}")
        seen_ts.add(candle.ts)

    instrument_uid = resolve_index_uid(connection, ticker=normalized)
    source_version = next_source_version(connection, instrument_uid=instrument_uid)

    complete = 0
    for candle in candles:
        is_complete = candle.ts < complete_before_ts
        complete += int(is_complete)
        store_candle_snapshot(
            connection,
            candle_snapshot_from_iss(
                candle,
                instrument_uid=instrument_uid,
                source_version=source_version,
                as_of=as_of,
                is_complete=is_complete,
            ),
        )

    conflict_ids: list[int] = []
    if calendar is not None:
        first_day = min(seen_ts)
        last_day = max(seen_ts)
        expected = [
            day
            for day in calendar.trading_days_in_range(first_day, last_day)
            if day < complete_before_ts
        ]
        for day in expected:
            if day in seen_ts:
                continue
            conflict_ids.append(
                record_data_conflict(
                    connection,
                    instrument_uid=instrument_uid,
                    ts=day,
                    kind="missing_bar",
                    detail={"ticker": normalized, "expected_by": "trading_calendar"},
                    as_of=as_of,
                )
            )

    return IndexIngestResult(
        instrument_uid=instrument_uid,
        source_version=source_version,
        stored=len(candles),
        complete=complete,
        incomplete=len(candles) - complete,
        missing_bar_conflict_ids=tuple(conflict_ids),
    )


__all__ = [
    "IndexIngestResult",
    "candle_snapshot_from_iss",
    "ingest_index_candles",
    "next_source_version",
    "resolve_index_uid",
]
