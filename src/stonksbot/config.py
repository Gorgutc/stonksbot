from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from stonksbot.data.store import build_universe_status_map


class ConfigError(ValueError):
    """Raised when M0 startup configuration violates a frozen contract."""


Mode = Literal["paper", "sandbox", "confirm"]
CloseDefinition = Literal["auction_close", "d1_candle_after_evening"]
IndexSource = Literal["moex_iss"]


class DashboardSettings(BaseModel):
    bind: str = "127.0.0.1"
    port: int = 8765


class TelegramSettings(BaseModel):
    user_whitelist: list[int] = Field(default_factory=list)
    button_ttl_minutes: int = 45


class UniverseSettings(BaseModel):
    approved: list[str] = Field(default_factory=lambda: ["SBER", "T", "GAZP", "ROSN", "TATN", "X5"])
    watch_only: list[str] = Field(default_factory=lambda: ["IRAO", "LKOH"])
    managed_only: list[str] = Field(default_factory=list)
    blocked: list[str] = Field(default_factory=list)
    pending: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def tickers_have_one_status(self) -> "UniverseSettings":
        build_universe_status_map(
            approved=self.approved,
            watch_only=self.watch_only,
            managed_only=self.managed_only,
            blocked=self.blocked,
            pending=self.pending,
        )
        return self


class EligibilitySettings(BaseModel):
    max_lot_value_pct: int = 30
    max_spread_bps: int = 50
    min_turnover_rub: int = 50_000_000
    min_trading_days: int = 40


class RiskSettings(BaseModel):
    capital_rub: int = 10_000
    max_open_positions: int = 1
    max_position_rub: int = 3_000
    max_position_pct: int = 30
    cash_reserve_pct: int = 50
    daily_hard_stop_rub: int = 100
    max_proposals_per_day: int = 1
    risk_per_trade_rub: int = 50
    hard_stop_pct: float = 4.0
    reentry_cooldown_days: int = 5
    market_regime_index_ma: int = 50
    market_regime_5d_floor_pct: float = -5.0
    allowed_trading_status: str = "NORMAL_TRADING"


class StrategySettings(BaseModel):
    ma_fast: int = 20
    ma_slow: int = 50
    pullback_min_pct: float = 2.0
    pullback_max_pct: float = 6.0
    take_profit_pct: float = 6.0
    trailing_pct: float = 3.0
    trend_support_ma: int = 20
    trend_break_ma: int = 50
    max_holding_days: int | None = None


class OrderSettings(BaseModel):
    type: Literal["LIMIT"] = "LIMIT"
    ttl_minutes: int = 45
    max_entry_premium_pct: float = 0.20


class CostsSettings(BaseModel):
    tariff: Literal["investor", "trader"] = "investor"
    investor_commission_bps: int = 30
    trader_commission_bps: int = 5
    trader_monthly_fee_rub: int = 390
    slippage_bps: int = 10
    min_commission_units: int = 0
    min_commission_nano: int = 10_000_000
    iceberg_surcharge_bps: int = 1


class DataConflictSettings(BaseModel):
    close_divergence_pct: float = 0.5
    recheck_delay_minutes: int = 30


class SecretSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    tinvest_token_sandbox: str | None = Field(default=None, alias="TINVEST_TOKEN_SANDBOX")
    tinvest_token_live_confirm: str | None = Field(default=None, alias="TINVEST_TOKEN_LIVE_CONFIRM")
    telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    dashboard_auth_token: str | None = Field(default=None, alias="DASHBOARD_AUTH_TOKEN")


class StonksbotSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str | None = None
    mode: Mode = "paper"
    db_path: str = "./stonksbot.db"
    timezone: str = "Europe/Moscow"
    daily_run_time: str = "19:05"
    close_definition: CloseDefinition = "auction_close"
    benchmarks: list[str] = Field(default_factory=lambda: ["IMOEX", "MCFTR", "cash", "equal_weight"])
    index_source: IndexSource = "moex_iss"
    moex_auction_shift_date: date = date(2026, 3, 23)
    dividend_gap_block_days: int = 2
    db_switch_point: str = "sqlite_to_postgres_at_vps_m6"
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    universe: UniverseSettings = Field(default_factory=UniverseSettings)
    eligibility: EligibilitySettings = Field(default_factory=EligibilitySettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    strategy: StrategySettings = Field(default_factory=StrategySettings)
    order: OrderSettings = Field(default_factory=OrderSettings)
    costs: CostsSettings = Field(default_factory=CostsSettings)
    data_conflict: DataConflictSettings = Field(default_factory=DataConflictSettings)
    secrets: SecretSettings = Field(default_factory=SecretSettings)

    @field_validator("account_id")
    @classmethod
    def blank_account_id_is_missing(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("daily_run_time")
    @classmethod
    def daily_run_time_is_hhmm(cls, value: str) -> str:
        parts = value.split(":")
        if (
            len(parts) != 2
            or len(parts[0]) != 2
            or len(parts[1]) != 2
            or not parts[0].isdigit()
            or not parts[1].isdigit()
        ):
            raise ValueError("daily_run_time must use HH:MM")
        hour = int(parts[0])
        minute = int(parts[1])
        if hour > 23 or minute > 59:
            raise ValueError("daily_run_time must use HH:MM")
        return value


def load_settings(
    *,
    config_path: str | Path | None = None,
    env: dict[str, str] | None = None,
    validate_startup: bool = True,
) -> StonksbotSettings:
    config_data = _load_toml(Path(config_path or "config/config.toml"))
    env_source = os.environ if env is None else env
    env_data = _settings_from_env(env_source)
    secret_values = _secrets_from_env(env) if env is not None else SecretSettings()
    try:
        settings = StonksbotSettings(**_deep_merge(config_data, env_data), secrets=secret_values)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc

    if validate_startup:
        _validate_startup(settings)
    return settings


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    import tomllib

    return tomllib.loads(path.read_text(encoding="utf-8"))


def _settings_from_env(env: dict[str, str]) -> dict[str, Any]:
    mapping = {
        "STONKSBOT_ACCOUNT_ID": ("account_id",),
        "STONKSBOT_MODE": ("mode",),
        "STONKSBOT_DB_PATH": ("db_path",),
        "STONKSBOT_TIMEZONE": ("timezone",),
        "STONKSBOT_DAILY_RUN_TIME": ("daily_run_time",),
        "STONKSBOT_CLOSE_DEFINITION": ("close_definition",),
        "STONKSBOT_INDEX_SOURCE": ("index_source",),
        "STONKSBOT_DB_SWITCH_POINT": ("db_switch_point",),
    }
    data: dict[str, Any] = {}
    for key, path in mapping.items():
        if key not in env:
            continue
        cursor = data
        for part in path[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[path[-1]] = env[key]
    return data


def _secrets_from_env(env: dict[str, str]) -> SecretSettings:
    return SecretSettings(
        tinvest_token_sandbox=env.get("TINVEST_TOKEN_SANDBOX"),
        tinvest_token_live_confirm=env.get("TINVEST_TOKEN_LIVE_CONFIRM"),
        telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN"),
        dashboard_auth_token=env.get("DASHBOARD_AUTH_TOKEN"),
    )


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _validate_startup(settings: StonksbotSettings) -> None:
    daily_run_time = _hhmm_to_minutes(settings.daily_run_time)
    if settings.close_definition == "auction_close" and daily_run_time < _hhmm_to_minutes("19:00"):
        raise ConfigError("daily_run_time must be >= 19:00 for auction_close")
    if settings.close_definition == "d1_candle_after_evening" and daily_run_time < _hhmm_to_minutes(
        "23:55"
    ):
        raise ConfigError("daily_run_time must wait for evening D1 close")
    if settings.mode in {"sandbox", "confirm"} and not settings.account_id:
        raise ConfigError("account_id is required for sandbox/confirm mode")
    if settings.mode == "sandbox" and not settings.secrets.tinvest_token_sandbox:
        raise ConfigError("TINVEST_TOKEN_SANDBOX is required for sandbox mode")
    if settings.mode == "confirm" and not settings.secrets.tinvest_token_live_confirm:
        raise ConfigError("TINVEST_TOKEN_LIVE_CONFIRM is required for confirm mode")
    _validate_frozen_pilot_risk(settings.risk)


def _validate_frozen_pilot_risk(risk: RiskSettings) -> None:
    if risk.capital_rub > 10_000:
        raise ConfigError("capital_rub must not exceed frozen pilot limit 10000")
    if risk.max_open_positions > 1:
        raise ConfigError("max_open_positions must not exceed frozen pilot limit 1")
    if risk.max_position_rub > 3_000:
        raise ConfigError("max_position_rub must not exceed frozen pilot limit 3000")
    if risk.max_position_pct > 30:
        raise ConfigError("max_position_pct must not exceed frozen pilot limit 30")
    if risk.cash_reserve_pct < 50:
        raise ConfigError("cash_reserve_pct must be at least frozen pilot floor 50")
    if risk.daily_hard_stop_rub > 100:
        raise ConfigError("daily_hard_stop_rub must not exceed frozen pilot limit 100")
    if risk.max_proposals_per_day > 1:
        raise ConfigError("max_proposals_per_day must not exceed frozen pilot limit 1")


def _hhmm_to_minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)
