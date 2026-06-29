from stonksbot.account_guard import GuardStatus, evaluate_account_guard
from stonksbot.config import load_settings


def test_paper_mode_without_account_does_not_require_broker_guard() -> None:
    settings = load_settings(env={})

    result = evaluate_account_guard(settings, last_account_id=None)

    assert result.status is GuardStatus.PAPER_NO_ACCOUNT
    assert result.blocks_startup is False


def test_sandbox_missing_account_is_refused_before_live_stub() -> None:
    settings = load_settings(env={"STONKSBOT_MODE": "sandbox"}, validate_startup=False)

    result = evaluate_account_guard(settings, last_account_id=None)

    assert result.status is GuardStatus.REFUSED_MISSING_ACCOUNT_ID
    assert result.blocks_startup is True


def test_account_change_requires_manual_confirmation() -> None:
    settings = load_settings(
        env={
            "STONKSBOT_MODE": "sandbox",
            "STONKSBOT_ACCOUNT_ID": "BOT-NEW",
            "TINVEST_TOKEN_SANDBOX": "loaded",
        }
    )

    result = evaluate_account_guard(
        settings, last_account_id="BOT-OLD", manual_change_confirmed=False
    )

    assert result.status is GuardStatus.REFUSED_ACCOUNT_CHANGED
    assert result.blocks_startup is True


def test_sandbox_and_confirm_fail_closed_until_m4_live_guard_exists() -> None:
    sandbox = load_settings(
        env={
            "STONKSBOT_MODE": "sandbox",
            "STONKSBOT_ACCOUNT_ID": "BOT-1",
            "TINVEST_TOKEN_SANDBOX": "loaded",
        }
    )
    confirm = load_settings(
        env={
            "STONKSBOT_MODE": "confirm",
            "STONKSBOT_ACCOUNT_ID": "BOT-1",
            "TINVEST_TOKEN_LIVE_CONFIRM": "loaded",
        }
    )

    assert evaluate_account_guard(sandbox, last_account_id="BOT-1").status is (
        GuardStatus.ACCOUNT_GUARD_STUB_BLOCKED
    )
    assert evaluate_account_guard(confirm, last_account_id="BOT-1").status is (
        GuardStatus.ACCOUNT_GUARD_STUB_BLOCKED
    )

