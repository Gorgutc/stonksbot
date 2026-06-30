from datetime import date
from pathlib import Path

import pytest

from stonksbot.config import ConfigError, load_settings


def test_default_config_is_safe_paper_mode() -> None:
    settings = load_settings(env={})

    assert settings.mode == "paper"
    assert settings.account_id is None
    assert settings.dashboard.bind == "127.0.0.1"
    assert settings.daily_run_time == "19:05"
    assert settings.close_definition == "auction_close"
    assert settings.benchmarks == ["IMOEX", "MCFTR", "cash", "equal_weight"]
    assert settings.index_source == "moex_iss"
    assert settings.moex_auction_shift_date == date(2026, 3, 23)
    assert settings.dividend_gap_block_days == 2
    assert settings.db_switch_point == "sqlite_to_postgres_at_vps_m6"
    assert settings.universe.approved == ["SBER", "T", "GAZP", "ROSN", "TATN", "X5"]
    assert settings.order.type == "LIMIT"


def test_environment_overrides_committed_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
mode = "sandbox"
account_id = "CONFIG-ACCOUNT"
daily_run_time = "19:05"
""".strip(),
        encoding="utf-8",
    )

    settings = load_settings(
        config_path=config_path,
        env={
            "STONKSBOT_MODE": "paper",
            "STONKSBOT_ACCOUNT_ID": "ENV-ACCOUNT",
        },
    )

    assert settings.mode == "paper"
    assert settings.account_id == "ENV-ACCOUNT"


def test_process_environment_is_used_when_env_is_not_injected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for key in (
        "STONKSBOT_ACCOUNT_ID",
        "STONKSBOT_MODE",
        "STONKSBOT_DB_PATH",
        "STONKSBOT_TIMEZONE",
        "STONKSBOT_DAILY_RUN_TIME",
        "STONKSBOT_CLOSE_DEFINITION",
        "TINVEST_TOKEN_SANDBOX",
        "TINVEST_TOKEN_LIVE_CONFIRM",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("STONKSBOT_MODE", "sandbox")
    monkeypatch.setenv("STONKSBOT_ACCOUNT_ID", "BOT-ENV")
    monkeypatch.setenv("TINVEST_TOKEN_SANDBOX", "loaded")

    settings = load_settings(config_path=tmp_path / "missing.toml")

    assert settings.mode == "sandbox"
    assert settings.account_id == "BOT-ENV"
    assert settings.secrets.tinvest_token_sandbox == "loaded"


def test_config_accepts_contract_schedule_and_benchmark_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
mode = "paper"
daily_run_time = "19:05"
benchmarks = ["IMOEX", "MCFTR", "cash", "equal_weight"]
index_source = "moex_iss"
moex_auction_shift_date = 2026-03-23
dividend_gap_block_days = 2
db_switch_point = "sqlite_to_postgres_at_vps_m6"
""".strip(),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config_path, env={})

    assert settings.benchmarks == ["IMOEX", "MCFTR", "cash", "equal_weight"]
    assert settings.index_source == "moex_iss"
    assert settings.moex_auction_shift_date == date(2026, 3, 23)
    assert settings.dividend_gap_block_days == 2
    assert settings.db_switch_point == "sqlite_to_postgres_at_vps_m6"


def test_sandbox_requires_account_id_and_active_token() -> None:
    with pytest.raises(ConfigError, match="account_id"):
        load_settings(env={"STONKSBOT_MODE": "sandbox", "TINVEST_TOKEN_SANDBOX": "loaded"})

    with pytest.raises(ConfigError, match="TINVEST_TOKEN_SANDBOX"):
        load_settings(env={"STONKSBOT_MODE": "sandbox", "STONKSBOT_ACCOUNT_ID": "BOT-1"})

    settings = load_settings(
        env={
            "STONKSBOT_MODE": "sandbox",
            "STONKSBOT_ACCOUNT_ID": "BOT-1",
            "TINVEST_TOKEN_SANDBOX": "loaded",
        }
    )
    assert settings.mode == "sandbox"
    assert settings.account_id == "BOT-1"
    assert settings.secrets.tinvest_token_sandbox == "loaded"


def test_confirm_requires_live_confirm_token_only() -> None:
    with pytest.raises(ConfigError, match="TINVEST_TOKEN_LIVE_CONFIRM"):
        load_settings(
            env={
                "STONKSBOT_MODE": "confirm",
                "STONKSBOT_ACCOUNT_ID": "BOT-1",
                "TINVEST_TOKEN_SANDBOX": "sandbox-only",
            }
        )

    settings = load_settings(
        env={
            "STONKSBOT_MODE": "confirm",
            "STONKSBOT_ACCOUNT_ID": "BOT-1",
            "TINVEST_TOKEN_LIVE_CONFIRM": "live-confirm",
        }
    )

    assert settings.secrets.tinvest_token_live_confirm == "live-confirm"


def test_auction_close_schedule_rejects_lookahead_time() -> None:
    with pytest.raises(ConfigError, match="daily_run_time"):
        load_settings(env={"STONKSBOT_DAILY_RUN_TIME": "18:59"})


def test_daily_run_time_must_be_zero_padded_hhmm() -> None:
    with pytest.raises(ConfigError, match="HH:MM"):
        load_settings(env={"STONKSBOT_DAILY_RUN_TIME": "9:05"})


def test_universe_ticker_cannot_have_multiple_statuses(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
mode = "paper"
[universe]
approved = ["SBER"]
watch_only = ["sber"]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="multiple universe statuses"):
        load_settings(config_path=config_path, env={})


def test_startup_rejects_relaxed_frozen_pilot_risk_limits(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
mode = "paper"
[risk]
max_open_positions = 2
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="max_open_positions"):
        load_settings(config_path=config_path, env={})


def test_env_example_contains_only_placeholders() -> None:
    content = Path(".env.example").read_text(encoding="utf-8")

    assert "TINVEST_TOKEN_SANDBOX=<sandbox-token-placeholder>" in content
    assert "TINVEST_TOKEN_LIVE_CONFIRM=<live-confirm-token-placeholder>" in content
    assert "TELEGRAM_BOT_TOKEN=<telegram-bot-token-placeholder>" in content
    assert "DASHBOARD_AUTH_TOKEN=<random-32+char-placeholder>" in content
    assert "TINVEST_TOKEN_LIVE_AUTO_SMALL" not in content
    assert "t." not in content
