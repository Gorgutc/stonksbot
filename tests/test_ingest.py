import sqlite3
from datetime import UTC, datetime

import pytest

from stonksbot.data.calendar import build_trading_calendar
from stonksbot.data.ingest import (
    candle_snapshot_from_iss,
    ingest_index_candles,
    next_source_version,
    resolve_index_uid,
)
from stonksbot.data.moex_iss import MoexIssCandle, Quotation
from stonksbot.data.registry import seed_index_reference
from stonksbot.data.store import read_latest_candles
from stonksbot.db import bootstrap_database


def _day(day_of_june: int) -> int:
    return int(datetime(2026, 6, day_of_june, tzinfo=UTC).timestamp() * 1000)


def _candle(day_of_june: int, *, close_units: int = 100, secid: str = "IMOEX") -> MoexIssCandle:
    return MoexIssCandle(
        secid=secid,
        market="index",
        ts=_day(day_of_june),
        open=Quotation(100, 0),
        high=Quotation(101, 0),
        low=Quotation(99, 0),
        close=Quotation(close_units, 0),
        volume=10,
    )


def _connection_with_index() -> tuple[sqlite3.Connection, str]:
    connection = sqlite3.connect(":memory:")
    bootstrap_database(connection, now_ms=0)
    uid = seed_index_reference(connection, secid="IMOEX", as_of=0)
    return connection, uid


def test_bridge_maps_fields_verbatim_with_explicit_completeness() -> None:
    candle = _candle(25, close_units=123)

    snapshot = candle_snapshot_from_iss(
        candle,
        instrument_uid="moex_iss:index:IMOEX",
        source_version=3,
        as_of=777,
        is_complete=False,
    )

    assert snapshot.instrument_uid == "moex_iss:index:IMOEX"
    assert snapshot.ts == candle.ts
    assert snapshot.source_version == 3
    assert snapshot.open == candle.open
    assert snapshot.close == Quotation(123, 0)
    assert snapshot.volume == 10
    assert snapshot.source == "moex_iss"
    assert snapshot.as_of == 777
    assert snapshot.is_complete is False
    assert snapshot.is_stale is False
    assert snapshot.adjusted is False


def test_ingest_marks_current_session_incomplete_and_hides_it_from_entry_reads() -> None:
    connection, uid = _connection_with_index()

    result = ingest_index_candles(
        connection,
        ticker="IMOEX",
        candles=[_candle(25), _candle(26), _candle(27)],
        as_of=_day(27) + 1,
        complete_before_ts=_day(27),
    )

    assert (result.stored, result.complete, result.incomplete) == (3, 2, 1)
    assert result.source_version == 1
    visible = read_latest_candles(
        connection, instrument_uid=uid, decision_ts=_day(30), entry_safe=True
    )
    assert [candle.ts for candle in visible] == [_day(25), _day(26)]


def test_reingest_bumps_source_version_and_latest_wins() -> None:
    connection, uid = _connection_with_index()
    ingest_index_candles(
        connection,
        ticker="IMOEX",
        candles=[_candle(25, close_units=100)],
        as_of=_day(25) + 1,
        complete_before_ts=_day(26),
    )

    result = ingest_index_candles(
        connection,
        ticker="IMOEX",
        candles=[_candle(25, close_units=105)],
        as_of=_day(26) + 1,
        complete_before_ts=_day(26),
    )

    assert result.source_version == 2
    assert next_source_version(connection, instrument_uid=uid) == 3
    retained = connection.execute(
        "SELECT COUNT(*) FROM candles WHERE instrument_uid = ? AND ts = ?", (uid, _day(25))
    ).fetchone()[0]
    assert int(retained) == 2
    visible = read_latest_candles(
        connection, instrument_uid=uid, decision_ts=_day(30), entry_safe=True
    )
    assert [candle.close.units for candle in visible] == [105]


def test_as_of_gates_visibility_no_backdated_knowledge() -> None:
    connection, uid = _connection_with_index()
    ingest_index_candles(
        connection,
        ticker="IMOEX",
        candles=[_candle(25)],
        as_of=_day(27),
        complete_before_ts=_day(26),
    )

    before_load = read_latest_candles(
        connection, instrument_uid=uid, decision_ts=_day(26), entry_safe=True
    )
    after_load = read_latest_candles(
        connection, instrument_uid=uid, decision_ts=_day(27), entry_safe=True
    )

    assert before_load == []
    assert [candle.ts for candle in after_load] == [_day(25)]


def test_missing_expected_session_records_idempotent_open_conflict() -> None:
    connection, uid = _connection_with_index()
    calendar = build_trading_calendar([_day(25), _day(26), _day(29)])
    kwargs = {
        "ticker": "IMOEX",
        "candles": [_candle(25), _candle(29)],
        "complete_before_ts": _day(30),
        "calendar": calendar,
    }

    first = ingest_index_candles(connection, as_of=_day(30), **kwargs)
    second = ingest_index_candles(connection, as_of=_day(30) + 1, **kwargs)

    assert len(first.missing_bar_conflict_ids) == 1
    assert first.missing_bar_conflict_ids == second.missing_bar_conflict_ids
    open_conflicts = connection.execute(
        """
        SELECT ts, kind, resolved FROM data_conflicts WHERE instrument_uid = ?
        """,
        (uid,),
    ).fetchall()
    assert len(open_conflicts) == 1
    assert (int(open_conflicts[0][0]), open_conflicts[0][1], int(open_conflicts[0][2])) == (
        _day(26),
        "missing_bar",
        0,
    )


def test_incomplete_region_is_not_scanned_for_missing_bars() -> None:
    connection, _uid = _connection_with_index()
    calendar = build_trading_calendar([_day(25), _day(26), _day(29)])

    result = ingest_index_candles(
        connection,
        ticker="IMOEX",
        candles=[_candle(25), _candle(29)],
        as_of=_day(29) + 1,
        complete_before_ts=_day(26),  # only day 25 is finalized; 26 is not expected yet
        calendar=calendar,
    )

    assert result.missing_bar_conflict_ids == ()


def test_requested_window_detects_tail_gap() -> None:
    connection, uid = _connection_with_index()
    calendar = build_trading_calendar([_day(25), _day(26), _day(29), _day(30)])
    july1 = int(datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1000)

    # The feed went quiet after June 26 although the caller requested through
    # June 30 — the tail sessions must surface as missing_bar conflicts.
    result = ingest_index_candles(
        connection,
        ticker="IMOEX",
        candles=[_candle(25), _candle(26)],
        as_of=july1 + 1,
        complete_before_ts=july1,
        calendar=calendar,
        window_from_ts=_day(25),
        window_till_ts=_day(30),
    )

    assert len(result.missing_bar_conflict_ids) == 2
    missing_days = {
        int(row[0])
        for row in connection.execute(
            "SELECT ts FROM data_conflicts WHERE instrument_uid = ? AND kind = 'missing_bar'",
            (uid,),
        ).fetchall()
    }
    assert missing_days == {_day(29), _day(30)}


def test_missing_bar_scan_requires_calendar_coverage() -> None:
    connection, _uid = _connection_with_index()
    # Calendar covers only late June; the candle span starts on June 1 — the
    # uncovered finalized region must fail closed, not silently scan nothing.
    calendar = build_trading_calendar([_day(25), _day(26)])
    early = MoexIssCandle(
        secid="IMOEX",
        market="index",
        ts=_day(1),
        open=Quotation(100, 0),
        high=Quotation(101, 0),
        low=Quotation(99, 0),
        close=Quotation(100, 0),
        volume=5,
    )

    with pytest.raises(ValueError, match="does not cover the missing-bar scan window"):
        ingest_index_candles(
            connection,
            ticker="IMOEX",
            candles=[early, _candle(25)],
            as_of=_day(26),
            complete_before_ts=_day(26),
            calendar=calendar,
        )

    # Validation runs BEFORE the store loop: a coverage failure must not leave
    # a partially-stored load in the caller's open transaction.
    stored = connection.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
    assert int(stored) == 0


def test_ingest_fails_closed_on_bad_inputs() -> None:
    connection, _uid = _connection_with_index()

    with pytest.raises(ValueError, match="zero candles"):
        ingest_index_candles(
            connection, ticker="IMOEX", candles=[], as_of=1, complete_before_ts=1
        )
    with pytest.raises(ValueError, match="market"):
        share_candle = MoexIssCandle(
            secid="IMOEX",
            market="shares",
            ts=_day(25),
            open=Quotation(100, 0),
            high=Quotation(101, 0),
            low=Quotation(99, 0),
            close=Quotation(100, 0),
            volume=1,
        )
        ingest_index_candles(
            connection,
            ticker="IMOEX",
            candles=[share_candle],
            as_of=1,
            complete_before_ts=1,
        )
    with pytest.raises(ValueError, match="does not match"):
        ingest_index_candles(
            connection,
            ticker="IMOEX",
            candles=[_candle(25, secid="MCFTR")],
            as_of=1,
            complete_before_ts=1,
        )
    with pytest.raises(ValueError, match="seed the registry"):
        ingest_index_candles(
            connection,
            ticker="MCFTR",
            candles=[_candle(25, secid="MCFTR")],
            as_of=1,
            complete_before_ts=1,
        )
    with pytest.raises(ValueError, match="duplicate candle ts"):
        ingest_index_candles(
            connection,
            ticker="IMOEX",
            candles=[_candle(25), _candle(25)],
            as_of=1,
            complete_before_ts=1,
        )


def test_resolve_index_uid_fails_closed_when_missing() -> None:
    connection = sqlite3.connect(":memory:")
    bootstrap_database(connection, now_ms=0)

    with pytest.raises(ValueError, match="seed the registry"):
        resolve_index_uid(connection, ticker="IMOEX")
