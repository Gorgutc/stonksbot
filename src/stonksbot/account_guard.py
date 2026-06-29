from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from stonksbot.config import StonksbotSettings


class GuardStage(StrEnum):
    S1_PRESENT = "S1_present"
    S2_CHANGE_CONFIRMED = "S2_change_confirmed"
    S3_ENUMERATED = "S3_enumerated"
    S4_EXACT_MATCH = "S4_exact_match"
    S5_NAMED_SHOWN = "S5_named_shown"
    S6_SCOPE_OK = "S6_scope_ok"


class GuardStatus(StrEnum):
    OK = "ok"
    REFUSED_MISSING_ACCOUNT_ID = "refused_missing_account_id"
    REFUSED_ACCOUNT_CHANGED = "refused_account_changed"
    ACCOUNT_GUARD_STUB_BLOCKED = "account_guard_stub_blocked"
    REFUSED_NO_EXACT_MATCH = "refused_no_exact_match"
    REFUSED_SCOPE_MISMATCH = "refused_scope_mismatch"
    PAPER_NO_ACCOUNT = "paper_no_account"


@dataclass(frozen=True)
class AccountGuardResult:
    status: GuardStatus
    stage: GuardStage | None
    blocks_startup: bool
    message: str


def evaluate_account_guard(
    settings: StonksbotSettings,
    *,
    last_account_id: str | None,
    manual_change_confirmed: bool = False,
) -> AccountGuardResult:
    if settings.mode == "paper" and not settings.account_id:
        return AccountGuardResult(
            status=GuardStatus.PAPER_NO_ACCOUNT,
            stage=None,
            blocks_startup=False,
            message="paper mode does not require a broker account",
        )

    if settings.mode in {"sandbox", "confirm"} and not settings.account_id:
        return AccountGuardResult(
            status=GuardStatus.REFUSED_MISSING_ACCOUNT_ID,
            stage=GuardStage.S1_PRESENT,
            blocks_startup=True,
            message="account_id is required for sandbox/confirm mode",
        )

    if (
        settings.account_id
        and last_account_id
        and settings.account_id != last_account_id
        and not manual_change_confirmed
    ):
        return AccountGuardResult(
            status=GuardStatus.REFUSED_ACCOUNT_CHANGED,
            stage=GuardStage.S2_CHANGE_CONFIRMED,
            blocks_startup=True,
            message="account_id changed and requires manual confirmation",
        )

    if settings.mode in {"sandbox", "confirm"}:
        return AccountGuardResult(
            status=GuardStatus.ACCOUNT_GUARD_STUB_BLOCKED,
            stage=GuardStage.S3_ENUMERATED,
            blocks_startup=True,
            message=(
                "live account verification (GetAccounts) requires the M4 broker adapter; "
                f"refusing to start in {settings.mode} until S3-S6 are wired"
            ),
        )

    return AccountGuardResult(
        status=GuardStatus.OK,
        stage=GuardStage.S2_CHANGE_CONFIRMED,
        blocks_startup=False,
        message="paper mode account guard checks passed",
    )

