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
``TradingCalendar`` is supplied, every expected finalized session inside the
scan window with no bar records an idempotent open ``missing_bar`` conflict.
The scan window anchors to the REQUESTED fetch window
(``window_from_ts``/``window_till_ts``) when provided — a feed that went quiet
at the head or tail of the window is detected rather than silently shrinking
the scan to the returned bars. Calendar coverage is validated fail-closed
BEFORE any row is written (coverage vs contents, mirroring
``session_policy.resolve_daily_run``). Escalation to persistent conflicts /
skip signals stays with the cycle driver. Documented limitation: the calendar
is itself derived from IMOEX candles, so IMOEX missing-bar detection against
it is partially self-referential; MCFTR is fully checkable against the IMOEX
calendar.
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

_MS_PER_DAY = 86_400_000


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


def _to_day(ts: int, *, name: str) -> int:
    if isinstance(ts, bool) or not isinstance(ts, int):
        raise TypeError(f"{name} must be an int (epoch-ms UTC)")
    return (ts // _MS_PER_DAY) * _MS_PER_DAY


def ingest_index_candles(
    connection: sqlite3.Connection,
    *,
    ticker: str,
    candles: Sequence[MoexIssCandle],
    as_of: int,
    complete_before_ts: int,
    calendar: TradingCalendar | None = None,
    window_from_ts: int | None = None,
    window_till_ts: int | None = None,
    calendar_coverage_through: int | None = None,
) -> IndexIngestResult:
    """Persist fetched index candles as one new source_version (fail-closed).

    ``complete_before_ts`` (epoch-ms UTC) is the finality cutoff the caller
    computes (e.g. UTC midnight of the current MOEX session date at fetch
    time): bars with ``ts >= complete_before_ts`` are stored ``is_complete=0``
    and stay invisible to entry-safe reads. An empty fetch raises — a blank ISS
    response must never look like a successful ingest.

    ``window_from_ts`` / ``window_till_ts`` are the bounds of the REQUESTED
    fetch window; pass them so the missing-bar scan covers sessions the feed
    returned nothing for (head/tail gaps). They default to the returned bar
    span. ``calendar_coverage_through`` is the till-label the calendar build
    actually requested (coverage, as opposed to contents); it defaults to the
    calendar's last known day. Every validation — including calendar coverage
    — runs BEFORE the first row is written, so a raised error never leaves a
    partially-stored, un-scanned load in the caller's transaction.
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
        if candle.ts % _MS_PER_DAY:
            raise ValueError(
                f"candle ts {candle.ts} is not a UTC-midnight day label for {normalized}"
            )
        if candle.ts in seen_ts:
            raise ValueError(f"duplicate candle ts {candle.ts} for {normalized}")
        seen_ts.add(candle.ts)

    instrument_uid = resolve_index_uid(connection, ticker=normalized)

    # Plan the missing-bar scan BEFORE any write (fail-closed on coverage).
    expected_missing: list[int] = []
    if calendar is not None:
        scan_start = (
            _to_day(window_from_ts, name="window_from_ts")
            if window_from_ts is not None
            else min(seen_ts)
        )
        scan_end = (
            _to_day(window_till_ts, name="window_till_ts")
            if window_till_ts is not None
            else max(seen_ts)
        )
        if scan_start > scan_end:
            raise ValueError("window_from_ts must not be after window_till_ts")
        finalized_end = min(scan_end, complete_before_ts - _MS_PER_DAY)
        if finalized_end >= scan_start:
            coverage = (
                _to_day(calendar_coverage_through, name="calendar_coverage_through")
                if calendar_coverage_through is not None
                else calendar.last
            )
            # trading_days_in_range silently returns the INTERSECTION with the
            # calendar contents; a calendar narrower than the finalized scan
            # window would make missing sessions vanish without a conflict row.
            if calendar.first > scan_start or coverage < finalized_end:
                raise ValueError(
                    "trading calendar does not cover the missing-bar scan window; "
                    "extend the calendar fetch window to span the requested candle window"
                )
            expected_missing = [
                day
                for day in calendar.trading_days_in_range(scan_start, finalized_end)
                if day not in seen_ts
            ]

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

    conflict_ids = [
        record_data_conflict(
            connection,
            instrument_uid=instrument_uid,
            ts=day,
            kind="missing_bar",
            detail={"ticker": normalized, "expected_by": "trading_calendar"},
            as_of=as_of,
        )
        for day in expected_missing
    ]

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
