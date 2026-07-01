import json
import sqlite3
from datetime import UTC, date, datetime

import pytest

from stonksbot.data.calendar import (
    TradingCalendar,
    build_trading_calendar,
    load_trading_calendar,
    trading_calendar_from_candles,
)
from stonksbot.data.moex_iss import MoexIssCandle, Quotation
from stonksbot.data.store import (
    DividendSnapshot,
    read_dividends_known_as_of,
    store_dividend_snapshot,
)
from stonksbot.db import bootstrap_database

_MS_PER_DAY = 86_400_000


def _day(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def _index_candle(ts: int) -> MoexIssCandle:
    return MoexIssCandle(
        secid="IMOEX",
        market="index",
        ts=ts,
        open=Quotation(1, 0),
        high=Quotation(1, 0),
        low=Quotation(1, 0),
        close=Quotation(1, 0),
        volume=1,
    )


def _iss_payload(dates: list[str]) -> str:
    return json.dumps(
        {
            "candles": {
                "columns": ["begin", "open", "high", "low", "close", "volume"],
                "data": [[d, 100, 101, 99, 100, 10] for d in dates],
            },
            "candles.cursor": {
                "columns": ["INDEX", "TOTAL", "PAGESIZE"],
                "data": [[0, len(dates), 500]],
            },
        }
    )


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


def test_build_trading_calendar_normalizes_dedupes_and_sorts() -> None:
    intraday = _day(2026, 6, 25) + 13 * 3_600_000  # a mid-session timestamp
    calendar = build_trading_calendar(
        [_day(2026, 6, 26), _day(2026, 6, 25), intraday, _day(2026, 6, 26)]
    )
    assert calendar.days == (_day(2026, 6, 25), _day(2026, 6, 26))
    assert len(calendar) == 2
    assert calendar.first == _day(2026, 6, 25)
    assert calendar.last == _day(2026, 6, 26)


def test_build_trading_calendar_is_fail_closed_on_empty() -> None:
    with pytest.raises(ValueError, match="zero trading days"):
        build_trading_calendar([])


def test_trading_calendar_rejects_unnormalized_unsorted_or_duplicate_days() -> None:
    with pytest.raises(ValueError, match="normalized to UTC midnight"):
        TradingCalendar(days=(_day(2026, 6, 25) + 1,))
    with pytest.raises(ValueError, match="strictly ascending and unique"):
        TradingCalendar(days=(_day(2026, 6, 26), _day(2026, 6, 25)))
    with pytest.raises(ValueError, match="strictly ascending and unique"):
        TradingCalendar(days=(_day(2026, 6, 25), _day(2026, 6, 25)))
    with pytest.raises(ValueError, match="at least one trading day"):
        TradingCalendar(days=())


def test_trading_calendar_rejects_bool_days() -> None:
    with pytest.raises(TypeError, match="epoch-ms UTC"):
        TradingCalendar(days=(True,))


def test_is_trading_day_and_membership_are_day_granular() -> None:
    calendar = build_trading_calendar([_day(2026, 6, 25), _day(2026, 6, 29)])
    assert calendar.is_trading_day(_day(2026, 6, 25))
    assert calendar.is_trading_day(_day(2026, 6, 25) + 9 * 3_600_000)  # same day, mid-session
    assert not calendar.is_trading_day(_day(2026, 6, 26))  # weekend / holiday
    assert _day(2026, 6, 29) in calendar
    assert _day(2026, 6, 27) not in calendar
    assert "not-an-int" not in calendar


def test_next_and_previous_trading_day_skip_the_weekend() -> None:
    # Thu 25, Fri 26, Mon 29 (Sat 27 + Sun 28 absent from the index series).
    calendar = build_trading_calendar([_day(2026, 6, 25), _day(2026, 6, 26), _day(2026, 6, 29)])
    assert calendar.next_trading_day(_day(2026, 6, 26)) == _day(2026, 6, 29)
    assert calendar.next_trading_day(_day(2026, 6, 27)) == _day(2026, 6, 29)  # from a non-session day
    assert calendar.previous_trading_day(_day(2026, 6, 29)) == _day(2026, 6, 26)


def test_next_trading_day_is_strictly_after_and_fail_closed_at_the_edge() -> None:
    calendar = build_trading_calendar([_day(2026, 6, 25), _day(2026, 6, 26)])
    assert calendar.next_trading_day(_day(2026, 6, 25)) == _day(2026, 6, 26)
    with pytest.raises(ValueError, match="does not cover"):
        calendar.next_trading_day(_day(2026, 6, 26))  # nothing known after the last session
    with pytest.raises(ValueError, match="does not cover"):
        calendar.previous_trading_day(_day(2026, 6, 25))  # nothing known before the first session


def test_add_trading_days_forward_backward_and_zero() -> None:
    calendar = build_trading_calendar(
        [_day(2026, 6, 25), _day(2026, 6, 26), _day(2026, 6, 29), _day(2026, 6, 30)]
    )
    assert calendar.add_trading_days(_day(2026, 6, 25), 2) == _day(2026, 6, 29)
    assert calendar.add_trading_days(_day(2026, 6, 30), -2) == _day(2026, 6, 26)
    assert calendar.add_trading_days(_day(2026, 6, 26), 0) == _day(2026, 6, 26)
    with pytest.raises(ValueError, match="requires ts to be a trading day"):
        calendar.add_trading_days(_day(2026, 6, 27), 0)
    with pytest.raises(ValueError, match="does not cover"):
        calendar.add_trading_days(_day(2026, 6, 30), 5)


def test_trading_days_in_range_is_inclusive() -> None:
    calendar = build_trading_calendar(
        [_day(2026, 6, 25), _day(2026, 6, 26), _day(2026, 6, 29), _day(2026, 6, 30)]
    )
    assert calendar.trading_days_in_range(_day(2026, 6, 26), _day(2026, 6, 29)) == (
        _day(2026, 6, 26),
        _day(2026, 6, 29),
    )
    # A weekend window that contains no session returns empty (feeds missing-bar detection).
    assert calendar.trading_days_in_range(_day(2026, 6, 27), _day(2026, 6, 28)) == ()
    with pytest.raises(ValueError, match="must not be after"):
        calendar.trading_days_in_range(_day(2026, 6, 29), _day(2026, 6, 26))


def test_trading_calendar_from_candles_uses_bar_dates() -> None:
    calendar = trading_calendar_from_candles(
        [_index_candle(_day(2026, 6, 29)), _index_candle(_day(2026, 6, 25))]
    )
    assert calendar.days == (_day(2026, 6, 25), _day(2026, 6, 29))


def test_load_trading_calendar_derives_days_from_injected_iss_reader() -> None:
    payload = _iss_payload(["2026-06-25", "2026-06-26", "2026-06-29"])
    calendar = load_trading_calendar(
        from_date=date(2026, 6, 1),
        till_date=date(2026, 6, 30),
        read_text=lambda _url: payload,
    )
    assert calendar.days == (_day(2026, 6, 25), _day(2026, 6, 26), _day(2026, 6, 29))
    assert calendar.next_trading_day(_day(2026, 6, 26)) == _day(2026, 6, 29)


def test_load_trading_calendar_is_fail_closed_on_empty_response() -> None:
    payload = _iss_payload([])
    with pytest.raises(ValueError, match="zero trading days"):
        load_trading_calendar(
            from_date=date(2026, 6, 1),
            till_date=date(2026, 6, 30),
            read_text=lambda _url: payload,
        )


def test_store_dividend_snapshot_derives_ex_date_from_trading_calendar() -> None:
    connection = _connection_with_share()
    # last_buy_date = Fri 26 Jun; ex_date = last_buy_date + 1 trading day = Mon 29 Jun (weekend-aware).
    calendar = build_trading_calendar([_day(2026, 6, 25), _day(2026, 6, 26), _day(2026, 6, 29)])
    store_dividend_snapshot(
        connection,
        DividendSnapshot(
            instrument_uid="uid-sber",
            last_buy_date=_day(2026, 6, 26),
            gross=Quotation(10, 0),
            currency="rub",
            source="moex_iss",
            source_version=1,
            as_of=_day(2026, 6, 27),
        ),
        trading_calendar=calendar,
    )
    dividends = read_dividends_known_as_of(
        connection, instrument_uid="uid-sber", decision_ts=_day(2026, 6, 30)
    )
    assert [dividend.ex_date for dividend in dividends] == [_day(2026, 6, 29)]


def test_backward_arithmetic_from_a_non_session_gap_day() -> None:
    # Sat 27 is a gap day: the session strictly before it is Fri 26, and two sessions before is
    # Thu 25 (mirror of next_trading_day-from-a-gap-day, exercising the strictly-before branch).
    calendar = build_trading_calendar([_day(2026, 6, 25), _day(2026, 6, 26), _day(2026, 6, 29)])
    assert calendar.previous_trading_day(_day(2026, 6, 27)) == _day(2026, 6, 26)
    assert calendar.add_trading_days(_day(2026, 6, 27), -2) == _day(2026, 6, 25)
