"""Pure session-policy decision for the daily cycle (holiday/short-session handling).

Encodes session-policy contract §4.3/§5/§6 as a stateless clock+calendar
function: the future daily-ingest orchestrator (and the M4 scheduler) call
``resolve_daily_run`` at the top of the cycle; a non-trading day produces no
signals, no proposal, no entry — and no misfire. This module governs the ENTRY
/ ingest cycle only and is never consulted by protective-exit paths
(data-layer §5.4; calendar module rule).

Decision layers, in order (each gates a different thing — session-policy
§2/§4/§5/§6): the clock gates *close finality* (§6.2 shift-aware 18:50/19:00
threshold for ``auction_close``), the calendar gates *session existence* (§5),
the DB ``is_complete`` gate downstream governs *signal computation*, and at M4
the live ``NORMAL_TRADING`` status read remains the final authority — a
calendar false-negative can only skip an entry cycle (fail-safe direction),
never admit one.

Short sessions: the IMOEX-derived calendar carries day labels only, no close
times. Early-close (short/pre-holiday) sessions are latency-safe under the
frozen ``auction_close`` + 19:05 pair — their close is final BEFORE the normal
threshold, so gating at the standard threshold can only fire late, never
before the close. Early-close times are deliberately NOT modeled (no verified
source; the dedicated ISS calendar endpoint stays ``[verify]``).

Same-day ambiguity (chicken-and-egg): a day appears in the IMOEX-derived
calendar only after its bar prints. Coverage is therefore tracked separately
from contents: a fetch that never reached today reports ``calendar_stale``
(data problem, loud), never a fake holiday. When coverage claims today but
today's bar is absent, holiday and ISS publication lag are indistinguishable —
the ``same_day_absence`` policy pins the interpretation and defaults to the
fail-closed loud option (PROVISIONAL — owner decision 2.6 in
``docs/ops/pre-live-owner-decisions.md``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from stonksbot.data.calendar import TradingCalendar

_MS_PER_DAY = 86_400_000

CloseDefinition = Literal["auction_close", "d1_candle_after_evening"]
SessionReason = Literal["trading_day", "non_trading_day", "calendar_stale", "before_final_close"]
SameDayAbsencePolicy = Literal["calendar_stale", "non_trading_day"]

# §6.2: the closing auction ended 18:50 MSK before the 2026-03-23 schedule shift,
# 19:00 MSK on/after it. The shift date is config (moex_auction_shift_date), never
# hard-coded in a scheduler. For d1_candle_after_evening the evening D1 close
# (~23:50 MSK) is the finality bound.
_PRE_SHIFT_AUCTION_CLOSE_MIN = 18 * 60 + 50
_POST_SHIFT_AUCTION_CLOSE_MIN = 19 * 60
_EVENING_D1_CLOSE_MIN = 23 * 60 + 50


@dataclass(frozen=True)
class SessionDecision:
    """Outcome of the daily-cycle gate; ``reason`` is a scheduler-decision code.

    These reasons live in logs / audit trail only — they are NOT ``signals.reason``
    skip codes (the frozen skip vocabulary is untouched).
    """

    run: bool
    reason: SessionReason
    session_day: int  # UTC-midnight epoch-ms label of the local (MSK) date decided


def msk_day_label(now_ms: int, *, timezone: str = "Europe/Moscow") -> int:
    """Map an epoch-ms instant to the UTC-midnight label of its local calendar date.

    Matches the TradingCalendar day-label convention (00:00 UTC of the MOEX
    session date). Never compare raw ``now`` against calendar days — an instant
    that is Monday 00:30 MSK is still Sunday in raw UTC.
    """
    if isinstance(now_ms, bool) or not isinstance(now_ms, int):
        raise TypeError("now_ms must be an int (epoch-ms UTC)")
    local = datetime.fromtimestamp(now_ms / 1000, tz=ZoneInfo(timezone))
    label = datetime(local.year, local.month, local.day, tzinfo=UTC)
    return int(label.timestamp() * 1000)


def final_close_threshold_minutes(
    on_day: date,
    *,
    close_definition: CloseDefinition,
    moex_auction_shift_date: date,
) -> int:
    """Minutes-of-day (local MSK) at which the close is final per §6.2."""
    if close_definition == "auction_close":
        if on_day >= moex_auction_shift_date:
            return _POST_SHIFT_AUCTION_CLOSE_MIN
        return _PRE_SHIFT_AUCTION_CLOSE_MIN
    if close_definition == "d1_candle_after_evening":
        return _EVENING_D1_CLOSE_MIN
    raise ValueError(f"unknown close_definition: {close_definition!r}")


def resolve_daily_run(
    now_ms: int,
    *,
    calendar: TradingCalendar,
    calendar_coverage_through: int,
    close_definition: CloseDefinition = "auction_close",
    moex_auction_shift_date: date = date(2026, 3, 23),
    timezone: str = "Europe/Moscow",
    same_day_absence: SameDayAbsencePolicy = "calendar_stale",
) -> SessionDecision:
    """Decide whether the daily entry/ingest cycle may fire now (fail-closed).

    ``calendar_coverage_through`` is the UTC-midnight label of the ``till_date``
    the calendar load actually requested — coverage, as opposed to contents.
    Ordered checks: before_final_close -> calendar_stale (coverage) ->
    absence policy -> trading_day. Never raises for a valid ``now_ms``:
    fail-closed here means a no-run DECISION, not an exception (exits are never
    gated by this function).
    """
    if isinstance(calendar_coverage_through, bool) or not isinstance(
        calendar_coverage_through, int
    ):
        raise TypeError("calendar_coverage_through must be an int (epoch-ms UTC)")

    local = datetime.fromtimestamp(now_ms / 1000, tz=ZoneInfo(timezone))
    day_label = msk_day_label(now_ms, timezone=timezone)
    coverage_label = (calendar_coverage_through // _MS_PER_DAY) * _MS_PER_DAY

    threshold = final_close_threshold_minutes(
        local.date(),
        close_definition=close_definition,
        moex_auction_shift_date=moex_auction_shift_date,
    )
    if local.hour * 60 + local.minute < threshold:
        return SessionDecision(run=False, reason="before_final_close", session_day=day_label)

    if coverage_label < day_label:
        return SessionDecision(run=False, reason="calendar_stale", session_day=day_label)

    if not calendar.is_trading_day(day_label):
        # Coverage claims today, bar absent: holiday vs ISS publication lag is
        # undecidable from the derived calendar alone; the policy pins it.
        return SessionDecision(run=False, reason=same_day_absence, session_day=day_label)

    return SessionDecision(run=True, reason="trading_day", session_day=day_label)


__all__ = [
    "CloseDefinition",
    "SameDayAbsencePolicy",
    "SessionDecision",
    "SessionReason",
    "final_close_threshold_minutes",
    "msk_day_label",
    "resolve_daily_run",
]
