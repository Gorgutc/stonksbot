import sqlite3

import pytest

from stonksbot.data.moex_iss import Quotation
from stonksbot.data.store import (
    CandleSnapshot,
    DividendSnapshot,
    build_universe_status_map,
    read_dividends_known_as_of,
    read_latest_candles,
    record_persistent_data_conflict,
    resolve_data_conflict,
    store_candle_snapshot,
    store_dividend_snapshot,
)
from stonksbot.db import bootstrap_database


def _connection_with_share() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    bootstrap_database(connection, now_ms=0)
    connection.execute(
        """
        INSERT INTO instrument_reference (
          instrument_uid, ticker, instrument_kind, is_tradable, lot,
          min_price_increment_units, min_price_increment_nano, whitelist_status,
          source, source_version, as_of
        )
        VALUES ('uid-sber', 'SBER', 'share', 1, 10, 0, 10000000, 'approved', 'moex_iss', 1, 0)
        """
    )
    return connection


def _candle(
    ts: int,
    source_version: int,
    *,
    is_complete: bool = True,
    is_stale: bool = False,
    close_units: int = 100,
    as_of: int | None = None,
) -> CandleSnapshot:
    return CandleSnapshot(
        instrument_uid="uid-sber",
        ts=ts,
        source_version=source_version,
        open=Quotation(100, 0),
        high=Quotation(101, 0),
        low=Quotation(99, 0),
        close=Quotation(close_units, 0),
        volume=1000,
        source="moex_iss",
        as_of=ts if as_of is None else as_of,
        is_complete=is_complete,
        is_stale=is_stale,
    )


def test_latest_wins_but_incomplete_latest_blocks_entry_reads() -> None:
    connection = _connection_with_share()

    store_candle_snapshot(connection, _candle(10, 1, close_units=101))
    store_candle_snapshot(connection, _candle(10, 2, is_complete=False, close_units=102))

    assert connection.execute("SELECT COUNT(*) FROM candles").fetchone() == (2,)
    assert read_latest_candles(connection, instrument_uid="uid-sber", decision_ts=10) == []

    store_candle_snapshot(connection, _candle(20, 1, close_units=101))
    store_candle_snapshot(connection, _candle(20, 2, close_units=102))

    candles = read_latest_candles(connection, instrument_uid="uid-sber", decision_ts=20)
    assert [(candle.ts, candle.source_version, candle.close.units) for candle in candles] == [
        (20, 2, 102)
    ]


def test_duplicate_same_source_version_does_not_overwrite() -> None:
    connection = _connection_with_share()

    store_candle_snapshot(connection, _candle(10, 1))

    with pytest.raises(sqlite3.IntegrityError):
        store_candle_snapshot(connection, _candle(10, 1, close_units=101))

    assert connection.execute("SELECT close_units FROM candles").fetchone() == (100,)


def test_candle_latest_version_is_limited_by_decision_as_of() -> None:
    connection = _connection_with_share()

    store_candle_snapshot(connection, _candle(10, 1, close_units=101, as_of=10))
    store_candle_snapshot(connection, _candle(10, 2, close_units=102, as_of=20))

    assert [
        (candle.source_version, candle.close.units)
        for candle in read_latest_candles(connection, instrument_uid="uid-sber", decision_ts=15)
    ] == [(1, 101)]
    assert [
        (candle.source_version, candle.close.units)
        for candle in read_latest_candles(connection, instrument_uid="uid-sber", decision_ts=25)
    ] == [(2, 102)]


def test_stale_latest_version_blocks_entry_without_fallback() -> None:
    connection = _connection_with_share()

    store_candle_snapshot(connection, _candle(10, 1, close_units=101))
    store_candle_snapshot(connection, _candle(10, 2, is_stale=True, close_units=102))

    assert read_latest_candles(connection, instrument_uid="uid-sber", decision_ts=10) == []
    assert [
        (candle.source_version, candle.close.units)
        for candle in read_latest_candles(
            connection,
            instrument_uid="uid-sber",
            decision_ts=10,
            entry_safe=False,
        )
    ] == [(2, 102)]


def test_incomplete_latest_version_can_still_be_read_for_exits() -> None:
    connection = _connection_with_share()

    store_candle_snapshot(connection, _candle(10, 1, is_complete=False, close_units=102))

    assert read_latest_candles(connection, instrument_uid="uid-sber", decision_ts=10) == []
    assert [
        (candle.source_version, candle.close.units)
        for candle in read_latest_candles(
            connection,
            instrument_uid="uid-sber",
            decision_ts=10,
            entry_safe=False,
        )
    ] == [(1, 102)]


def test_limited_candle_reads_return_latest_window_in_chronological_order() -> None:
    connection = _connection_with_share()
    for ts in [10, 20, 30]:
        store_candle_snapshot(connection, _candle(ts, 1, close_units=ts))

    assert [
        candle.ts
        for candle in read_latest_candles(
            connection,
            instrument_uid="uid-sber",
            decision_ts=30,
            limit=2,
        )
    ] == [20, 30]


def test_limited_entry_reads_do_not_backfill_past_degraded_latest_bar() -> None:
    connection = _connection_with_share()
    store_candle_snapshot(connection, _candle(10, 1, close_units=10))
    store_candle_snapshot(connection, _candle(20, 1, close_units=20))
    store_candle_snapshot(connection, _candle(30, 1, is_stale=True, close_units=30))

    assert [
        candle.ts
        for candle in read_latest_candles(
            connection,
            instrument_uid="uid-sber",
            decision_ts=30,
            limit=2,
        )
    ] == [20]


def test_stale_or_conflicted_data_is_excluded_from_entries_not_exits() -> None:
    connection = _connection_with_share()
    store_candle_snapshot(connection, _candle(10, 1, is_stale=True))

    assert read_latest_candles(connection, instrument_uid="uid-sber", decision_ts=10) == []
    assert read_latest_candles(
        connection, instrument_uid="uid-sber", decision_ts=10, entry_safe=False
    )[0].ts == 10

    record_persistent_data_conflict(
        connection,
        instrument_uid="uid-sber",
        ts=10,
        kind="missing_bar",
        detail={"source": "tinvest"},
        as_of=10,
    )

    assert read_latest_candles(connection, instrument_uid="uid-sber", decision_ts=10) == []
    assert read_latest_candles(
        connection, instrument_uid="uid-sber", decision_ts=10, entry_safe=False
    )[0].ts == 10


def test_resolved_data_conflict_remains_historical_entry_block_until_resolution_time() -> None:
    connection = _connection_with_share()
    store_candle_snapshot(connection, _candle(10, 1, close_units=100, as_of=10))

    conflict_id, _signal_id = record_persistent_data_conflict(
        connection,
        instrument_uid="uid-sber",
        ts=10,
        kind="close_divergence",
        detail={"moex_iss": "100.00", "tinvest": "100.60"},
        as_of=20,
    )

    assert read_latest_candles(connection, instrument_uid="uid-sber", decision_ts=15)[0].ts == 10
    assert read_latest_candles(connection, instrument_uid="uid-sber", decision_ts=25) == []

    resolve_data_conflict(connection, conflict_id=conflict_id, as_of=30)
    resolve_data_conflict(connection, conflict_id=conflict_id, as_of=40)

    assert read_latest_candles(connection, instrument_uid="uid-sber", decision_ts=25) == []
    assert read_latest_candles(connection, instrument_uid="uid-sber", decision_ts=35)[0].ts == 10
    assert (
        connection.execute(
            "SELECT resolved, resolved_as_of FROM data_conflicts WHERE id = ?",
            (conflict_id,),
        ).fetchone()
        == (1, 30)
    )


def test_persistent_data_conflict_marks_registry_and_emits_skip_signal() -> None:
    connection = _connection_with_share()

    conflict_id, signal_id = record_persistent_data_conflict(
        connection,
        instrument_uid="uid-sber",
        ts=10,
        kind="close_divergence",
        detail={"moex_iss": "100.00", "tinvest": "100.60"},
        as_of=20,
    )

    assert conflict_id > 0
    assert signal_id > 0
    assert (
        connection.execute(
            "SELECT data_status FROM instrument_reference WHERE instrument_uid = 'uid-sber'"
        ).fetchone()
        == ("data_conflict",)
    )
    assert (
        connection.execute(
            "SELECT decision, reason FROM signals WHERE id = ?",
            (signal_id,),
        ).fetchone()
        == ("skipped", "data_conflict")
    )


def test_record_persistent_data_conflict_is_idempotent_per_open_bar() -> None:
    connection = _connection_with_share()

    first_conflict_id, first_signal_id = record_persistent_data_conflict(
        connection,
        instrument_uid="uid-sber",
        ts=10,
        kind="close_divergence",
        detail={"moex_iss": "100.00", "tinvest": "100.60"},
        as_of=20,
    )
    second_conflict_id, second_signal_id = record_persistent_data_conflict(
        connection,
        instrument_uid="uid-sber",
        ts=10,
        kind="close_divergence",
        detail={"moex_iss": "100.00", "tinvest": "100.90"},
        as_of=30,
    )

    assert second_conflict_id == first_conflict_id
    assert second_signal_id == first_signal_id
    assert connection.execute(
        "SELECT COUNT(*) FROM data_conflicts WHERE instrument_uid = 'uid-sber'"
    ).fetchone() == (1,)
    assert connection.execute(
        "SELECT COUNT(*) FROM signals WHERE decision = 'skipped' AND reason = 'data_conflict'"
    ).fetchone() == (1,)
    # the earliest detection time is kept as the conservative entry-block start
    assert connection.execute(
        "SELECT as_of FROM data_conflicts WHERE id = ?",
        (first_conflict_id,),
    ).fetchone() == (20,)


def test_resolved_conflict_recurrence_opens_a_new_open_row() -> None:
    connection = _connection_with_share()

    first_conflict_id, first_signal_id = record_persistent_data_conflict(
        connection,
        instrument_uid="uid-sber",
        ts=10,
        kind="close_divergence",
        detail={"moex_iss": "100.00", "tinvest": "100.60"},
        as_of=20,
    )
    resolve_data_conflict(connection, conflict_id=first_conflict_id, as_of=30)

    second_conflict_id, second_signal_id = record_persistent_data_conflict(
        connection,
        instrument_uid="uid-sber",
        ts=10,
        kind="close_divergence",
        detail={"moex_iss": "100.00", "tinvest": "101.20"},
        as_of=40,
    )

    assert second_conflict_id != first_conflict_id
    assert connection.execute(
        """
        SELECT id, resolved
        FROM data_conflicts
        WHERE instrument_uid = 'uid-sber' AND ts = 10 AND kind = 'close_divergence'
        ORDER BY id
        """
    ).fetchall() == [(first_conflict_id, 1), (second_conflict_id, 0)]
    # the resolved row is not swallowed; the recurrence is a fresh open conflict
    assert connection.execute(
        "SELECT COUNT(*) FROM data_conflicts WHERE instrument_uid = 'uid-sber' AND resolved = 0"
    ).fetchone() == (1,)
    # the per-bar skip signal is reused across the resolve/recur cycle, never duplicated
    assert second_signal_id == first_signal_id
    assert connection.execute(
        "SELECT COUNT(*) FROM signals WHERE decision = 'skipped' AND reason = 'data_conflict'"
    ).fetchone() == (1,)


def test_open_data_conflicts_partial_unique_is_enforced_at_db_level() -> None:
    connection = _connection_with_share()

    connection.execute(
        "INSERT INTO data_conflicts (instrument_uid, ts, kind, as_of) "
        "VALUES ('uid-sber', 10, 'missing_bar', 20)"
    )
    # a second OPEN row with the same (instrument_uid, ts, kind) is rejected by the partial index
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO data_conflicts (instrument_uid, ts, kind, as_of) "
            "VALUES ('uid-sber', 10, 'missing_bar', 30)"
        )
    # a RESOLVED row with the same key is allowed: the index is partial on resolved = 0
    connection.execute(
        "INSERT INTO data_conflicts (instrument_uid, ts, kind, resolved, resolved_as_of, as_of) "
        "VALUES ('uid-sber', 10, 'missing_bar', 1, 40, 20)"
    )
    assert connection.execute(
        "SELECT COUNT(*) FROM data_conflicts "
        "WHERE instrument_uid = 'uid-sber' AND ts = 10 AND kind = 'missing_bar'"
    ).fetchone() == (2,)


def test_redetect_keeps_earliest_as_of_even_when_observed_out_of_order() -> None:
    connection = _connection_with_share()

    first_conflict_id, _signal_id = record_persistent_data_conflict(
        connection,
        instrument_uid="uid-sber",
        ts=10,
        kind="close_divergence",
        detail={"observed": 1},
        as_of=30,
    )
    # a later call carrying an EARLIER as_of must lower the block start, never raise it
    record_persistent_data_conflict(
        connection,
        instrument_uid="uid-sber",
        ts=10,
        kind="close_divergence",
        detail={"observed": 2},
        as_of=20,
    )

    assert connection.execute(
        "SELECT as_of FROM data_conflicts WHERE id = ?",
        (first_conflict_id,),
    ).fetchone() == (20,)


def test_distinct_conflict_kinds_on_same_bar_share_one_skip_signal() -> None:
    connection = _connection_with_share()

    record_persistent_data_conflict(
        connection,
        instrument_uid="uid-sber",
        ts=10,
        kind="missing_bar",
        detail=None,
        as_of=20,
    )
    record_persistent_data_conflict(
        connection,
        instrument_uid="uid-sber",
        ts=10,
        kind="close_divergence",
        detail=None,
        as_of=20,
    )

    # kind is part of the conflict key, so two open rows coexist ...
    assert connection.execute(
        "SELECT COUNT(*) FROM data_conflicts WHERE instrument_uid = 'uid-sber' AND ts = 10 AND resolved = 0"
    ).fetchone() == (2,)
    # ... but the bar is skipped exactly once
    assert connection.execute(
        "SELECT COUNT(*) FROM signals "
        "WHERE instrument_uid = 'uid-sber' AND ts = 10 "
        "AND decision = 'skipped' AND reason = 'data_conflict'"
    ).fetchone() == (1,)


def test_dividends_are_versioned_and_known_as_of_is_decision_time_safe() -> None:
    connection = _connection_with_share()

    store_dividend_snapshot(
        connection,
        DividendSnapshot(
            instrument_uid="uid-sber",
            last_buy_date=10,
            gross=Quotation(10, 0),
            currency="rub",
            source="moex_iss",
            source_version=1,
            as_of=15,
        ),
        trading_calendar=[9, 10, 11, 12],
    )
    store_dividend_snapshot(
        connection,
        DividendSnapshot(
            instrument_uid="uid-sber",
            last_buy_date=10,
            gross=Quotation(11, 0),
            currency="rub",
            source="moex_iss",
            source_version=2,
            as_of=25,
        ),
        trading_calendar=[9, 10, 11, 12],
    )

    dividends = read_dividends_known_as_of(connection, instrument_uid="uid-sber", decision_ts=20)

    assert [(dividend.source_version, dividend.ex_date, dividend.gross.units) for dividend in dividends] == [
        (1, 11, 10)
    ]


def test_dividends_declared_after_decision_time_are_excluded() -> None:
    connection = _connection_with_share()

    store_dividend_snapshot(
        connection,
        DividendSnapshot(
            instrument_uid="uid-sber",
            last_buy_date=10,
            gross=Quotation(10, 0),
            currency="rub",
            source="moex_iss",
            source_version=1,
            as_of=15,
            declared_date=25,
        ),
        trading_calendar=[9, 10, 11, 12],
    )

    assert read_dividends_known_as_of(connection, instrument_uid="uid-sber", decision_ts=20) == []


def test_universe_status_map_rejects_status_collisions_case_insensitively() -> None:
    with pytest.raises(ValueError, match="multiple universe statuses"):
        build_universe_status_map(approved=["SBER"], watch_only=["sber"])
