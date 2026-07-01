from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from stonksbot.data.calendar import build_trading_calendar
from stonksbot.session_policy import (
    final_close_threshold_minutes,
    msk_day_label,
    resolve_daily_run,
)

MSK = ZoneInfo("Europe/Moscow")
SHIFT_DATE = date(2026, 3, 23)


def _label(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def _msk(year: int, month: int, day: int, hour: int, minute: int) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=MSK).timestamp() * 1000)


# 2026-06-26 = Friday, 27/28 = weekend, 29 = Monday.
FRI = _label(2026, 6, 26)
SAT = _label(2026, 6, 27)
MON = _label(2026, 6, 29)


def test_trading_day_after_close_runs() -> None:
    calendar = build_trading_calendar([FRI, MON])

    decision = resolve_daily_run(
        _msk(2026, 6, 29, 19, 5), calendar=calendar, calendar_coverage_through=MON
    )

    assert decision.run is True
    assert decision.reason == "trading_day"
    assert decision.session_day == MON


def test_same_day_absence_defaults_to_loud_calendar_stale() -> None:
    calendar = build_trading_calendar([FRI, MON])

    decision = resolve_daily_run(
        _msk(2026, 6, 27, 19, 5), calendar=calendar, calendar_coverage_through=SAT
    )

    assert decision.run is False
    assert decision.reason == "calendar_stale"
    assert decision.session_day == SAT


def test_same_day_absence_policy_can_pin_quiet_holiday_skip() -> None:
    calendar = build_trading_calendar([FRI, MON])

    decision = resolve_daily_run(
        _msk(2026, 6, 27, 19, 5),
        calendar=calendar,
        calendar_coverage_through=SAT,
        same_day_absence="non_trading_day",
    )

    assert decision.run is False
    assert decision.reason == "non_trading_day"


def test_stale_coverage_is_a_data_problem_not_a_holiday() -> None:
    calendar = build_trading_calendar([FRI])

    decision = resolve_daily_run(
        _msk(2026, 6, 29, 19, 5), calendar=calendar, calendar_coverage_through=FRI
    )

    assert decision.run is False
    assert decision.reason == "calendar_stale"
    assert decision.session_day == MON


def test_before_final_close_waits_post_shift() -> None:
    calendar = build_trading_calendar([FRI, MON])

    decision = resolve_daily_run(
        _msk(2026, 6, 29, 18, 59), calendar=calendar, calendar_coverage_through=MON
    )

    assert decision.run is False
    assert decision.reason == "before_final_close"


def test_pre_shift_threshold_allows_1855_run() -> None:
    # 2026-03-20 = Friday, before the 2026-03-23 auction shift: threshold 18:50.
    pre_shift_day = _label(2026, 3, 20)
    calendar = build_trading_calendar([pre_shift_day])

    decision = resolve_daily_run(
        _msk(2026, 3, 20, 18, 55),
        calendar=calendar,
        calendar_coverage_through=pre_shift_day,
    )

    assert decision.run is True
    assert decision.reason == "trading_day"


def test_msk_midnight_maps_to_msk_date_not_utc_date() -> None:
    # Monday 00:30 MSK is still Sunday 21:30 UTC; the label must be Monday's.
    now_ms = _msk(2026, 6, 29, 0, 30)

    assert msk_day_label(now_ms) == MON
    decision = resolve_daily_run(
        now_ms,
        calendar=build_trading_calendar([FRI, MON]),
        calendar_coverage_through=MON,
    )
    assert decision.session_day == MON
    assert decision.reason == "before_final_close"


def test_wake_after_sleep_still_runs_same_trading_day() -> None:
    calendar = build_trading_calendar([FRI, MON])

    decision = resolve_daily_run(
        _msk(2026, 6, 29, 22, 40), calendar=calendar, calendar_coverage_through=MON
    )

    assert decision.run is True


def test_resolve_daily_run_never_raises_for_odd_instants() -> None:
    calendar = build_trading_calendar([FRI, MON])
    far_future = _msk(2027, 1, 15, 19, 5)

    for now_ms in (1, FRI, _msk(2026, 6, 26, 12, 0), far_future):
        decision = resolve_daily_run(
            now_ms, calendar=calendar, calendar_coverage_through=MON
        )
        assert decision.run in (True, False)
    stale = resolve_daily_run(far_future, calendar=calendar, calendar_coverage_through=MON)
    assert stale.reason == "calendar_stale"


def test_final_close_threshold_minutes_matches_contract() -> None:
    assert (
        final_close_threshold_minutes(
            date(2026, 3, 20), close_definition="auction_close", moex_auction_shift_date=SHIFT_DATE
        )
        == 18 * 60 + 50
    )
    assert (
        final_close_threshold_minutes(
            date(2026, 3, 23), close_definition="auction_close", moex_auction_shift_date=SHIFT_DATE
        )
        == 19 * 60
    )
    assert (
        final_close_threshold_minutes(
            date(2026, 6, 29),
            close_definition="d1_candle_after_evening",
            moex_auction_shift_date=SHIFT_DATE,
        )
        == 23 * 60 + 50
    )
    with pytest.raises(ValueError, match="close_definition"):
        final_close_threshold_minutes(
            date(2026, 6, 29),
            close_definition="typo",  # type: ignore[arg-type]
            moex_auction_shift_date=SHIFT_DATE,
        )


def test_msk_day_label_rejects_non_int() -> None:
    with pytest.raises(TypeError):
        msk_day_label(True)
    with pytest.raises(TypeError):
        msk_day_label(1.5)  # type: ignore[arg-type]
