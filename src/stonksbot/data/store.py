from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Literal

from stonksbot.data.moex_iss import Quotation


DataSource = Literal["moex_iss", "tinvest"]
ConflictKind = Literal["close_divergence", "missing_bar", "duplicate_bar"]
UniverseStatus = Literal["approved", "managed_only", "watch_only", "blocked", "pending"]

VALID_DATA_SOURCES = {"moex_iss", "tinvest"}
UNIVERSE_STATUS_ORDER: tuple[UniverseStatus, ...] = (
    "approved",
    "managed_only",
    "watch_only",
    "blocked",
    "pending",
)


@dataclass(frozen=True)
class CandleSnapshot:
    instrument_uid: str
    ts: int
    source_version: int
    open: Quotation
    high: Quotation
    low: Quotation
    close: Quotation
    volume: int
    source: DataSource
    as_of: int
    interval: Literal["1day"] = "1day"
    is_complete: bool = True
    is_stale: bool = False
    adjusted: bool = False


@dataclass(frozen=True)
class StoredCandle:
    instrument_uid: str
    interval: str
    ts: int
    source_version: int
    open: Quotation
    high: Quotation
    low: Quotation
    close: Quotation
    volume: int
    is_complete: bool
    is_stale: bool
    adjusted: bool
    source: str
    as_of: int


@dataclass(frozen=True)
class DividendSnapshot:
    instrument_uid: str
    last_buy_date: int
    gross: Quotation
    currency: str
    source: DataSource
    source_version: int
    as_of: int
    ex_date: int | None = None
    record_date: int | None = None
    payment_date: int | None = None
    declared_date: int | None = None
    dividend_type: str | None = None
    close_price: Quotation | None = None


@dataclass(frozen=True)
class StoredDividend:
    instrument_uid: str
    last_buy_date: int
    ex_date: int | None
    record_date: int | None
    payment_date: int | None
    declared_date: int | None
    gross: Quotation
    dividend_type: str | None
    close_price: Quotation | None
    currency: str
    source: str
    source_version: int
    as_of: int


def store_candle_snapshot(connection: sqlite3.Connection, candle: CandleSnapshot) -> None:
    _validate_source(candle.source)
    connection.execute(
        """
        INSERT INTO candles (
          instrument_uid, interval, ts, source_version,
          open_units, open_nano, high_units, high_nano,
          low_units, low_nano, close_units, close_nano,
          volume, is_complete, is_stale, adjusted, source, as_of
        )
        VALUES (
          :instrument_uid, :interval, :ts, :source_version,
          :open_units, :open_nano, :high_units, :high_nano,
          :low_units, :low_nano, :close_units, :close_nano,
          :volume, :is_complete, :is_stale, :adjusted, :source, :as_of
        )
        """,
        {
            "instrument_uid": candle.instrument_uid,
            "interval": candle.interval,
            "ts": candle.ts,
            "source_version": candle.source_version,
            "open_units": candle.open.units,
            "open_nano": candle.open.nano,
            "high_units": candle.high.units,
            "high_nano": candle.high.nano,
            "low_units": candle.low.units,
            "low_nano": candle.low.nano,
            "close_units": candle.close.units,
            "close_nano": candle.close.nano,
            "volume": candle.volume,
            "is_complete": int(candle.is_complete),
            "is_stale": int(candle.is_stale),
            "adjusted": int(candle.adjusted),
            "source": candle.source,
            "as_of": candle.as_of,
        },
    )


def read_latest_candles(
    connection: sqlite3.Connection,
    *,
    instrument_uid: str,
    decision_ts: int,
    interval: str = "1day",
    entry_safe: bool = True,
    limit: int | None = None,
) -> list[StoredCandle]:
    filters = []
    if entry_safe:
        filters.extend(
            [
                "is_complete = 1",
                "is_stale = 0",
                """
                NOT EXISTS (
                  SELECT 1
                  FROM data_conflicts dc
                  WHERE dc.instrument_uid = w.instrument_uid
                    AND dc.as_of <= ?
                    AND (dc.resolved = 0 OR dc.resolved_as_of > ?)
                )
                """,
            ]
        )
    where_extra = f" AND {' AND '.join(filters)}" if filters else ""
    limit_clause = "" if limit is None else " LIMIT ?"
    params: list[int | str] = [instrument_uid, interval, decision_ts, decision_ts]
    if limit is not None:
        params.append(limit)
    if entry_safe:
        params.extend([decision_ts, decision_ts])

    rows = connection.execute(
        f"""
        WITH latest AS (
          SELECT instrument_uid, interval, ts, MAX(source_version) AS source_version
          FROM candles
          WHERE instrument_uid = ?
            AND interval = ?
            AND ts <= ?
            AND as_of <= ?
          GROUP BY instrument_uid, interval, ts
        ),
        windowed AS (
          SELECT
            c.instrument_uid, c.interval, c.ts, c.source_version,
            c.open_units, c.open_nano, c.high_units, c.high_nano,
            c.low_units, c.low_nano, c.close_units, c.close_nano,
            c.volume, c.is_complete, c.is_stale, c.adjusted, c.source, c.as_of
          FROM candles c
          JOIN latest l
            ON l.instrument_uid = c.instrument_uid
           AND l.interval = c.interval
           AND l.ts = c.ts
           AND l.source_version = c.source_version
          ORDER BY c.ts DESC
          {limit_clause}
        ),
        filtered AS (
          SELECT *
          FROM windowed w
          WHERE 1 = 1{where_extra}
        )
        SELECT
          instrument_uid, interval, ts, source_version,
          open_units, open_nano, high_units, high_nano,
          low_units, low_nano, close_units, close_nano,
          volume, is_complete, is_stale, adjusted, source, as_of
        FROM filtered
        ORDER BY ts
        """,
        params,
    ).fetchall()
    return [_stored_candle_from_row(row) for row in rows]


def mark_stale(
    connection: sqlite3.Connection,
    *,
    instrument_uid: str,
    ts: int,
    source_version: int,
    interval: str = "1day",
) -> None:
    connection.execute(
        """
        UPDATE candles
        SET is_stale = 1
        WHERE instrument_uid = ?
          AND interval = ?
          AND ts = ?
          AND source_version = ?
        """,
        (instrument_uid, interval, ts, source_version),
    )


def record_data_conflict(
    connection: sqlite3.Connection,
    *,
    instrument_uid: str,
    ts: int,
    kind: ConflictKind,
    detail: dict[str, object] | str | None,
    as_of: int,
) -> int:
    # Idempotent per OPEN (resolved = 0) conflict: re-detecting the same (instrument_uid, ts, kind)
    # updates the existing open row (keeping the earliest as_of, the conservative entry-block start)
    # instead of inserting a duplicate. A resolved row is excluded from the partial unique index, so a
    # later recurrence opens a fresh row rather than reopening a closed one.
    connection.execute(
        """
        INSERT INTO data_conflicts (instrument_uid, ts, kind, detail, as_of)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (instrument_uid, ts, kind) WHERE resolved = 0
        DO UPDATE SET as_of = min(data_conflicts.as_of, excluded.as_of)
        """,
        (instrument_uid, ts, kind, _serialize_detail(detail), as_of),
    )
    open_conflict = connection.execute(
        """
        SELECT id
        FROM data_conflicts
        WHERE instrument_uid = ?
          AND ts = ?
          AND kind = ?
          AND resolved = 0
        """,
        (instrument_uid, ts, kind),
    ).fetchone()
    return int(open_conflict[0])


def resolve_data_conflict(connection: sqlite3.Connection, *, conflict_id: int, as_of: int) -> None:
    row = connection.execute(
        "SELECT instrument_uid FROM data_conflicts WHERE id = ?",
        (conflict_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown data conflict id: {conflict_id}")

    instrument_uid = row[0]
    connection.execute(
        """
        UPDATE data_conflicts
        SET resolved = 1,
            resolved_as_of = COALESCE(resolved_as_of, ?)
        WHERE id = ?
        """,
        (as_of, conflict_id),
    )
    unresolved = connection.execute(
        """
        SELECT 1
        FROM data_conflicts
        WHERE instrument_uid = ?
          AND resolved = 0
        LIMIT 1
        """,
        (instrument_uid,),
    ).fetchone()
    if unresolved is None:
        set_instrument_data_status(connection, instrument_uid=instrument_uid, status="ok")


def set_instrument_data_status(
    connection: sqlite3.Connection,
    *,
    instrument_uid: str,
    status: Literal["ok", "data_conflict"],
) -> None:
    connection.execute(
        """
        UPDATE instrument_reference
        SET data_status = ?
        WHERE instrument_uid = ?
        """,
        (status, instrument_uid),
    )


def record_persistent_data_conflict(
    connection: sqlite3.Connection,
    *,
    instrument_uid: str,
    ts: int,
    kind: ConflictKind,
    detail: dict[str, object] | str | None,
    as_of: int,
) -> tuple[int, int]:
    conflict_id = record_data_conflict(
        connection,
        instrument_uid=instrument_uid,
        ts=ts,
        kind=kind,
        detail=detail,
        as_of=as_of,
    )
    set_instrument_data_status(connection, instrument_uid=instrument_uid, status="data_conflict")
    # Emit at most one skipped/data_conflict signal per decision bar (instrument_uid, ts): a
    # re-detected conflict (or a second conflict kind on the same bar) must not stack duplicate
    # skip signals for an entry decision that is already recorded as skipped.
    connection.execute(
        """
        INSERT INTO signals (instrument_uid, ts, decision, reason, created_at)
        SELECT ?, ?, 'skipped', 'data_conflict', ?
        WHERE NOT EXISTS (
          SELECT 1
          FROM signals
          WHERE instrument_uid = ?
            AND ts = ?
            AND decision = 'skipped'
            AND reason = 'data_conflict'
        )
        """,
        (instrument_uid, ts, as_of, instrument_uid, ts),
    )
    skip_signal = connection.execute(
        """
        SELECT id
        FROM signals
        WHERE instrument_uid = ?
          AND ts = ?
          AND decision = 'skipped'
          AND reason = 'data_conflict'
        ORDER BY id
        LIMIT 1
        """,
        (instrument_uid, ts),
    ).fetchone()
    return conflict_id, int(skip_signal[0])


def store_dividend_snapshot(
    connection: sqlite3.Connection,
    dividend: DividendSnapshot,
    *,
    trading_calendar: list[int] | tuple[int, ...] | None = None,
) -> None:
    _validate_source(dividend.source)
    ex_date = dividend.ex_date
    if ex_date is None:
        if trading_calendar is None:
            raise ValueError("trading_calendar is required when dividend ex_date is missing")
        ex_date = _next_trading_day(dividend.last_buy_date, trading_calendar)

    close_units = dividend.close_price.units if dividend.close_price is not None else None
    close_nano = dividend.close_price.nano if dividend.close_price is not None else None
    connection.execute(
        """
        INSERT INTO dividends (
          instrument_uid, last_buy_date, ex_date, record_date, payment_date, declared_date,
          gross_units, gross_nano, dividend_type, close_price_units, close_price_nano,
          currency, source, source_version, as_of
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            dividend.instrument_uid,
            dividend.last_buy_date,
            ex_date,
            dividend.record_date,
            dividend.payment_date,
            dividend.declared_date,
            dividend.gross.units,
            dividend.gross.nano,
            dividend.dividend_type,
            close_units,
            close_nano,
            dividend.currency,
            dividend.source,
            dividend.source_version,
            dividend.as_of,
        ),
    )


def read_dividends_known_as_of(
    connection: sqlite3.Connection,
    *,
    instrument_uid: str,
    decision_ts: int,
) -> list[StoredDividend]:
    rows = connection.execute(
        """
        WITH latest AS (
          SELECT instrument_uid, last_buy_date, MAX(source_version) AS source_version
          FROM dividends
          WHERE instrument_uid = ?
            AND as_of <= ?
            AND (declared_date IS NULL OR declared_date <= ?)
          GROUP BY instrument_uid, last_buy_date
        )
        SELECT
          d.instrument_uid, d.last_buy_date, d.ex_date, d.record_date, d.payment_date,
          d.declared_date, d.gross_units, d.gross_nano, d.dividend_type,
          d.close_price_units, d.close_price_nano, d.currency, d.source, d.source_version, d.as_of
        FROM dividends d
        JOIN latest l
          ON l.instrument_uid = d.instrument_uid
         AND l.last_buy_date = d.last_buy_date
         AND l.source_version = d.source_version
        ORDER BY d.last_buy_date
        """,
        (instrument_uid, decision_ts, decision_ts),
    ).fetchall()
    return [_stored_dividend_from_row(row) for row in rows]


def build_universe_status_map(
    *,
    approved: list[str] | tuple[str, ...] = (),
    managed_only: list[str] | tuple[str, ...] = (),
    watch_only: list[str] | tuple[str, ...] = (),
    blocked: list[str] | tuple[str, ...] = (),
    pending: list[str] | tuple[str, ...] = (),
) -> dict[str, UniverseStatus]:
    tickers_by_status: dict[UniverseStatus, list[str] | tuple[str, ...]] = {
        "approved": approved,
        "managed_only": managed_only,
        "watch_only": watch_only,
        "blocked": blocked,
        "pending": pending,
    }
    statuses_by_ticker: dict[str, UniverseStatus] = {}
    for status in UNIVERSE_STATUS_ORDER:
        for ticker in tickers_by_status[status]:
            normalized = _normalize_ticker(ticker)
            previous = statuses_by_ticker.get(normalized)
            if previous is not None:
                raise ValueError(
                    f"{normalized} has multiple universe statuses: {previous}, {status}"
                )
            statuses_by_ticker[normalized] = status
    return statuses_by_ticker


def _stored_candle_from_row(row: sqlite3.Row | tuple[object, ...]) -> StoredCandle:
    return StoredCandle(
        instrument_uid=str(row[0]),
        interval=str(row[1]),
        ts=int(row[2]),
        source_version=int(row[3]),
        open=Quotation(int(row[4]), int(row[5])),
        high=Quotation(int(row[6]), int(row[7])),
        low=Quotation(int(row[8]), int(row[9])),
        close=Quotation(int(row[10]), int(row[11])),
        volume=int(row[12]),
        is_complete=bool(row[13]),
        is_stale=bool(row[14]),
        adjusted=bool(row[15]),
        source=str(row[16]),
        as_of=int(row[17]),
    )


def _stored_dividend_from_row(row: sqlite3.Row | tuple[object, ...]) -> StoredDividend:
    close_price = None
    if row[9] is not None and row[10] is not None:
        close_price = Quotation(int(row[9]), int(row[10]))
    return StoredDividend(
        instrument_uid=str(row[0]),
        last_buy_date=int(row[1]),
        ex_date=_optional_int(row[2]),
        record_date=_optional_int(row[3]),
        payment_date=_optional_int(row[4]),
        declared_date=_optional_int(row[5]),
        gross=Quotation(int(row[6]), int(row[7])),
        dividend_type=str(row[8]) if row[8] is not None else None,
        close_price=close_price,
        currency=str(row[11]),
        source=str(row[12]),
        source_version=int(row[13]),
        as_of=int(row[14]),
    )


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None


def _serialize_detail(detail: dict[str, object] | str | None) -> str | None:
    if detail is None or isinstance(detail, str):
        return detail
    return json.dumps(detail, sort_keys=True, separators=(",", ":"))


def _validate_source(source: str) -> None:
    if source not in VALID_DATA_SOURCES:
        raise ValueError(f"source must be one of {sorted(VALID_DATA_SOURCES)}")


def _next_trading_day(last_buy_date: int, trading_calendar: list[int] | tuple[int, ...]) -> int:
    for trading_day in sorted(trading_calendar):
        if trading_day > last_buy_date:
            return trading_day
    raise ValueError("trading_calendar has no session after last_buy_date")


def _normalize_ticker(ticker: str) -> str:
    normalized = ticker.strip().upper()
    if not normalized:
        raise ValueError("universe ticker must be non-empty")
    return normalized


__all__ = [
    "CandleSnapshot",
    "ConflictKind",
    "DataSource",
    "DividendSnapshot",
    "StoredCandle",
    "StoredDividend",
    "UniverseStatus",
    "build_universe_status_map",
    "mark_stale",
    "read_dividends_known_as_of",
    "read_latest_candles",
    "record_data_conflict",
    "record_persistent_data_conflict",
    "resolve_data_conflict",
    "set_instrument_data_status",
    "store_candle_snapshot",
    "store_dividend_snapshot",
]
