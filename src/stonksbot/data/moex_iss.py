from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlencode
from urllib.request import urlopen


SOURCE = "moex_iss"
INTERVAL = "1day"
BASE_URL = "https://iss.moex.com"

Market = Literal["index", "shares"]


@dataclass(frozen=True)
class Quotation:
    units: int
    nano: int


@dataclass(frozen=True)
class MoexIssCandle:
    secid: str
    market: Market
    ts: int
    open: Quotation
    high: Quotation
    low: Quotation
    close: Quotation
    volume: int
    source: str = SOURCE
    interval: str = INTERVAL


def build_candles_url(
    secid: str,
    *,
    market: Market,
    from_date: date,
    till_date: date,
) -> str:
    normalized_secid = _normalize_secid(secid)
    _validate_market(market)
    query = urlencode(
        {
            "interval": "24",
            "from": from_date.isoformat(),
            "till": till_date.isoformat(),
        }
    )
    return (
        f"{BASE_URL}/iss/engines/stock/markets/{market}"
        f"/securities/{normalized_secid}/candles.json?{query}"
    )


def loads_iss_json(payload: str) -> dict[str, Any]:
    data = json.loads(payload, parse_float=Decimal)
    if not isinstance(data, dict):
        raise TypeError("MOEX ISS payload must be a JSON object")
    return data


def fetch_daily_candles(
    secid: str,
    *,
    market: Market,
    from_date: date,
    till_date: date,
    read_text: Callable[[str], str] | None = None,
) -> list[MoexIssCandle]:
    url = build_candles_url(secid, market=market, from_date=from_date, till_date=till_date)
    reader = read_text or _read_url_text
    return parse_candles(loads_iss_json(reader(url)), secid=secid, market=market)


def parse_candles(payload: dict[str, Any], *, secid: str, market: Market) -> list[MoexIssCandle]:
    normalized_secid = _normalize_secid(secid)
    _validate_market(market)
    candles = payload.get("candles")
    if not isinstance(candles, dict):
        raise ValueError("MOEX ISS payload is missing candles table")

    columns = candles.get("columns")
    rows = candles.get("data")
    if not isinstance(columns, list) or not all(isinstance(column, str) for column in columns):
        raise ValueError("MOEX ISS candles.columns must be a list of column names")
    if not isinstance(rows, list):
        raise ValueError("MOEX ISS candles.data must be a list")

    parsed: list[MoexIssCandle] = []
    for row in rows:
        if not isinstance(row, list):
            raise ValueError("MOEX ISS candle row must be a list")
        values = _row_to_mapping(columns, row)
        parsed.append(
            MoexIssCandle(
                secid=normalized_secid,
                market=market,
                ts=_parse_epoch_ms(values["begin"]),
                open=_price_to_quotation(values["open"], field="open"),
                high=_price_to_quotation(values["high"], field="high"),
                low=_price_to_quotation(values["low"], field="low"),
                close=_price_to_quotation(values["close"], field="close"),
                volume=_parse_volume(values["volume"]),
            )
        )
    return parsed


def _read_url_text(url: str) -> str:
    with urlopen(url, timeout=20) as response:
        return response.read().decode("utf-8")


def _row_to_mapping(columns: list[str], row: list[Any]) -> dict[str, Any]:
    required = {"begin", "open", "high", "low", "close", "volume"}
    missing = required.difference(columns)
    if missing:
        raise ValueError(f"MOEX ISS candles table missing required columns: {sorted(missing)}")
    if len(row) != len(columns):
        raise ValueError("MOEX ISS candle row length does not match columns")
    return dict(zip(columns, row, strict=True))


def _normalize_secid(secid: str) -> str:
    normalized = secid.strip().upper()
    if not normalized:
        raise ValueError("secid must be non-empty")
    return normalized


def _validate_market(market: Market) -> None:
    if market not in {"index", "shares"}:
        raise ValueError("market must be 'index' or 'shares'")


def _parse_epoch_ms(value: Any) -> int:
    if not isinstance(value, str):
        raise TypeError("MOEX ISS candle begin must be a string")
    normalized = value.replace(" ", "T")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return int(parsed.timestamp() * 1000)


def _price_to_quotation(value: Any, *, field: str) -> Quotation:
    decimal_value = _strict_decimal(value, field=field)
    if decimal_value <= 0:
        raise ValueError(f"{field} must be positive")

    units = int(decimal_value)
    nano_decimal = (decimal_value - Decimal(units)) * Decimal("1000000000")
    if nano_decimal != nano_decimal.to_integral_value():
        raise ValueError(f"{field} has sub-nano precision")
    nano = int(nano_decimal)
    if not 0 <= nano <= 999_999_999:
        raise ValueError(f"{field} nano must be between 0 and 999999999")
    return Quotation(units=units, nano=nano)


def _strict_decimal(value: Any, *, field: str) -> Decimal:
    if isinstance(value, float):
        raise TypeError(f"{field} must not be parsed as float")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        return Decimal(value)
    raise TypeError(f"{field} must be Decimal, int, or string")


def _parse_volume(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("volume must be an integer")
    if value < 0:
        raise ValueError("volume must be non-negative")
    return value


__all__ = [
    "MoexIssCandle",
    "Quotation",
    "build_candles_url",
    "fetch_daily_candles",
    "loads_iss_json",
    "parse_candles",
]
