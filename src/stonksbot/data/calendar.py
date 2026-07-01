"""MOEX trading-calendar value object + producers (read-only, no-lookahead).

The exact MOEX ISS holiday/calendar endpoint is a ``[verify]`` item in the
session-policy contract (§5), so this module derives the trading calendar from
the already-resolved index source: the dates on which the IMOEX index has a MOEX
ISS D1 candle ARE the MOEX trading days (data-layer contract §1/§3.4, ADR-0005).
This keeps "one calendar source" (session-policy §5) consistent with the index
series the data layer already ingests, depends on no unverified endpoint, and is
inherently no-lookahead: only sessions that actually printed can appear.

Trading-day arithmetic operates on UTC-date-label boundaries — each trading date
is mapped to 00:00 UTC of that date, matching ``moex_iss._parse_epoch_ms``.
Timestamps are epoch-ms UTC (db-schema §1); Europe/Moscow is applied only at the
display / scheduling edges, never here.

Fail-closed by design: an unknown/empty calendar raises rather than silently
answering "no session"; callers treat a raised lookup as skip-entry, never as a
blocked protective exit (data-layer §5.4 / §7).
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date

from stonksbot.data.moex_iss import MoexIssCandle, fetch_daily_candles

_MS_PER_DAY = 86_400_000
DEFAULT_CALENDAR_SECID = "IMOEX"


def _to_utc_day(ts: int) -> int:
    """Floor an epoch-ms UTC timestamp to 00:00 UTC of its calendar day."""
    if isinstance(ts, bool) or not isinstance(ts, int):
        raise TypeError("timestamp must be an int (epoch-ms UTC)")
    return (ts // _MS_PER_DAY) * _MS_PER_DAY


@dataclass(frozen=True)
class TradingCalendar:
    """Immutable, sorted set of MOEX trading days (epoch-ms UTC, day-normalized).

    ``days`` must be strictly ascending, unique, and normalized to UTC midnight;
    the invariant is enforced at construction so every lookup is day-granular.
    Prefer the :func:`build_trading_calendar` / :func:`trading_calendar_from_candles`
    producers, which normalize and de-duplicate raw timestamps first.
    """

    days: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.days:
            raise ValueError("TradingCalendar requires at least one trading day")
        previous: int | None = None
        for day in self.days:
            if isinstance(day, bool) or not isinstance(day, int):
                raise TypeError("trading day must be an int (epoch-ms UTC)")
            if day != _to_utc_day(day):
                raise ValueError("trading days must be normalized to UTC midnight")
            if previous is not None and day <= previous:
                raise ValueError("trading days must be strictly ascending and unique")
            previous = day

    @property
    def first(self) -> int:
        return self.days[0]

    @property
    def last(self) -> int:
        return self.days[-1]

    def __len__(self) -> int:
        return len(self.days)

    def __contains__(self, ts: object) -> bool:
        if isinstance(ts, bool) or not isinstance(ts, int):
            return False
        return self.is_trading_day(ts)

    def is_trading_day(self, ts: int) -> bool:
        """True iff the UTC calendar day of ``ts`` is a MOEX trading day."""
        day = _to_utc_day(ts)
        index = bisect.bisect_left(self.days, day)
        return index < len(self.days) and self.days[index] == day

    def add_trading_days(self, ts: int, sessions: int) -> int:
        """Return the trading day ``sessions`` sessions from ``ts`` (day-granular).

        ``sessions > 0`` -> the Nth trading day strictly after ``ts``.
        ``sessions < 0`` -> the Nth trading day strictly before ``ts``.
        ``sessions == 0`` -> ``ts`` itself, which must be a trading day.

        Raises ``ValueError`` when the calendar does not cover the result
        (fail-closed; a caller treats this as skip-entry, never a blocked exit).
        """
        day = _to_utc_day(ts)
        if sessions == 0:
            if not self.is_trading_day(day):
                raise ValueError("add_trading_days(0) requires ts to be a trading day")
            return day
        if sessions > 0:
            index = bisect.bisect_right(self.days, day) + sessions - 1
        else:
            index = bisect.bisect_left(self.days, day) + sessions
        if not 0 <= index < len(self.days):
            raise ValueError("trading calendar does not cover the requested session offset")
        return self.days[index]

    def next_trading_day(self, ts: int) -> int:
        """First trading day strictly after ``ts`` (``ex_date = last_buy_date + 1``)."""
        return self.add_trading_days(ts, 1)

    def previous_trading_day(self, ts: int) -> int:
        """Last trading day strictly before ``ts``."""
        return self.add_trading_days(ts, -1)

    def trading_days_in_range(self, start_ts: int, end_ts: int) -> tuple[int, ...]:
        """Trading days ``d`` with ``start_ts <= d <= end_ts`` (inclusive, day-normalized).

        Feeds missing-bar / completeness detection: the expected D1 sessions in a
        lookback window.
        """
        start = _to_utc_day(start_ts)
        end = _to_utc_day(end_ts)
        if start > end:
            raise ValueError("start_ts must not be after end_ts")
        lo = bisect.bisect_left(self.days, start)
        hi = bisect.bisect_right(self.days, end)
        return self.days[lo:hi]


def build_trading_calendar(day_timestamps: Iterable[int]) -> TradingCalendar:
    """Build a calendar from raw epoch-ms timestamps (normalized, de-duped, sorted).

    Fail-closed: raises ``ValueError`` on an empty set — a missing calendar must
    never silently become an empty one that answers every query with "no session".
    """
    normalized = sorted({_to_utc_day(ts) for ts in day_timestamps})
    if not normalized:
        raise ValueError("cannot build a trading calendar from zero trading days")
    return TradingCalendar(days=tuple(normalized))


def trading_calendar_from_candles(candles: Iterable[MoexIssCandle]) -> TradingCalendar:
    """Derive the calendar from index D1 candles (each printed bar == a trading day)."""
    return build_trading_calendar(candle.ts for candle in candles)


def load_trading_calendar(
    *,
    from_date: date,
    till_date: date,
    secid: str = DEFAULT_CALENDAR_SECID,
    read_text: Callable[[str], str] | None = None,
) -> TradingCalendar:
    """Fetch IMOEX index D1 candles from MOEX ISS and derive the trading calendar.

    Read-only; ``read_text`` is injected in tests. Fail-closed on an empty result
    (``build_trading_calendar`` raises), so a blank ISS response never yields an
    empty calendar that would misreport every day as non-trading.
    """
    candles = fetch_daily_candles(
        secid,
        market="index",
        from_date=from_date,
        till_date=till_date,
        read_text=read_text,
    )
    return trading_calendar_from_candles(candles)


__all__ = [
    "DEFAULT_CALENDAR_SECID",
    "TradingCalendar",
    "build_trading_calendar",
    "load_trading_calendar",
    "trading_calendar_from_candles",
]
