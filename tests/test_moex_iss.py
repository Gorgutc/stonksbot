from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest

from stonksbot.data.moex_iss import (
    build_candles_url,
    fetch_daily_candles,
    loads_iss_json,
    parse_candles,
)

CANDLE_COLUMNS = ["begin", "open", "high", "low", "close", "volume"]
CURSOR_COLUMNS = ["INDEX", "TOTAL", "PAGESIZE"]


def candles_payload(rows: list[list[object]]) -> dict[str, object]:
    """ISS candles block as `parse_candles` consumes it (already-decoded values)."""
    return {"candles": {"columns": list(CANDLE_COLUMNS), "data": rows}}


def iss_page_json(
    rows: list[list[object]],
    *,
    cursor: tuple[int, int, int] | None = None,
) -> str:
    """Raw ISS JSON page as an injected `read_text` reader would return it.

    `cursor=None` deliberately omits the `candles.cursor` block (the fail-closed case).
    """
    payload: dict[str, object] = {"candles": {"columns": list(CANDLE_COLUMNS), "data": rows}}
    if cursor is not None:
        index, total, pagesize = cursor
        payload["candles.cursor"] = {
            "columns": list(CURSOR_COLUMNS),
            "data": [[index, total, pagesize]],
        }
    return json.dumps(payload)


def test_build_candles_url_uses_daily_index_endpoint() -> None:
    url = build_candles_url(
        "imoex",
        market="index",
        from_date=date(2026, 6, 1),
        till_date=date(2026, 6, 30),
    )

    assert url.startswith("https://iss.moex.com/iss/engines/stock/markets/index/")
    assert "/securities/IMOEX/candles.json?" in url
    assert "interval=24" in url
    assert "from=2026-06-01" in url
    assert "till=2026-06-30" in url
    assert "start=0" in url
    assert "limit=500" in url


def test_loads_iss_json_preserves_decimal_values() -> None:
    payload = '{"candles":{"columns":["begin","open"],"data":[["2026-06-01",123.45]]}}'

    data = loads_iss_json(payload)

    assert data["candles"]["data"][0][1] == Decimal("123.45")


def test_parse_candles_returns_epoch_ms_and_quotation_pairs() -> None:
    payload = candles_payload(
        [
            [
                "2026-06-27",
                Decimal("3210.12"),
                Decimal("3220.50"),
                Decimal("3201.01"),
                Decimal("3215.99"),
                123456789,
            ]
        ]
    )

    candles = parse_candles(payload, secid="IMOEX", market="index")

    assert len(candles) == 1
    candle = candles[0]
    assert candle.secid == "IMOEX"
    assert candle.market == "index"
    assert candle.source == "moex_iss"
    assert candle.interval == "1day"
    assert candle.ts == int(datetime(2026, 6, 27, tzinfo=UTC).timestamp() * 1000)
    assert candle.open.units == 3210
    assert candle.open.nano == 120_000_000
    assert candle.close.units == 3215
    assert candle.close.nano == 990_000_000
    assert candle.volume == 123456789


def test_parse_candles_rejects_float_prices() -> None:
    payload = candles_payload(
        [["2026-06-27", 3210.12, Decimal("3220.50"), Decimal("3201.01"), Decimal("3215.99"), 1]]
    )

    with pytest.raises(TypeError, match="float"):
        parse_candles(payload, secid="IMOEX", market="index")


def test_parse_candles_rejects_sub_nano_price_precision() -> None:
    payload = candles_payload(
        [
            [
                "2026-06-27",
                Decimal("3210.1234567895"),
                Decimal("3220.50"),
                Decimal("3201.01"),
                Decimal("3215.99"),
                1,
            ]
        ]
    )

    with pytest.raises(ValueError, match="sub-nano"):
        parse_candles(payload, secid="IMOEX", market="index")


def test_fetch_daily_candles_uses_injected_reader_without_tokens() -> None:
    captured_urls: list[str] = []

    def read_text(url: str) -> str:
        captured_urls.append(url)
        return iss_page_json(
            [["2026-06-27", 3210.12, 3220.50, 3201.01, 3215.99, 10]],
            cursor=(0, 1, 500),
        )

    candles = fetch_daily_candles(
        "IMOEX",
        market="index",
        from_date=date(2026, 6, 1),
        till_date=date(2026, 6, 30),
        read_text=read_text,
    )

    assert len(candles) == 1
    assert captured_urls
    assert "token" not in captured_urls[0].lower()
    assert candles[0].source == "moex_iss"


def test_fetch_daily_candles_rejects_non_empty_payload_without_cursor() -> None:
    def read_text(_: str) -> str:
        return iss_page_json(
            [["2026-06-27", 3210.12, 3220.50, 3201.01, 3215.99, 10]],
            cursor=None,
        )

    with pytest.raises(ValueError, match="missing candles.cursor"):
        fetch_daily_candles(
            "IMOEX",
            market="index",
            from_date=date(2026, 6, 1),
            till_date=date(2026, 6, 30),
            read_text=read_text,
        )


def test_fetch_daily_candles_follows_iss_cursor_until_total() -> None:
    captured_starts: list[str] = []

    def read_text(url: str) -> str:
        query = parse_qs(urlparse(url).query)
        start = query["start"][0]
        captured_starts.append(start)
        if start == "0":
            return iss_page_json(
                [
                    ["2026-06-25", 100, 101, 99, 100, 10],
                    ["2026-06-26", 101, 102, 100, 101, 11],
                ],
                cursor=(0, 3, 2),
            )
        if start == "2":
            return iss_page_json(
                [["2026-06-27", 102, 103, 101, 102, 12]],
                cursor=(2, 3, 2),
            )
        raise AssertionError(f"unexpected ISS start offset: {start}")

    candles = fetch_daily_candles(
        "IMOEX",
        market="index",
        from_date=date(2026, 6, 1),
        till_date=date(2026, 6, 30),
        read_text=read_text,
    )

    assert captured_starts == ["0", "2"]
    assert [candle.ts for candle in candles] == [
        int(datetime(2026, 6, 25, tzinfo=UTC).timestamp() * 1000),
        int(datetime(2026, 6, 26, tzinfo=UTC).timestamp() * 1000),
        int(datetime(2026, 6, 27, tzinfo=UTC).timestamp() * 1000),
    ]


def test_fetch_daily_candles_rejects_short_page_before_cursor_total() -> None:
    def read_text(url: str) -> str:
        query = parse_qs(urlparse(url).query)
        assert query["start"] == ["0"]
        return iss_page_json(
            [["2026-06-25", 100, 101, 99, 100, 10]],
            cursor=(0, 3, 2),
        )

    with pytest.raises(ValueError, match="short page"):
        fetch_daily_candles(
            "IMOEX",
            market="index",
            from_date=date(2026, 6, 1),
            till_date=date(2026, 6, 30),
            read_text=read_text,
        )


def test_parse_candles_rejects_duplicate_begin_values() -> None:
    payload = candles_payload(
        [
            ["2026-06-27", Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), 10],
            ["2026-06-27", Decimal("101"), Decimal("102"), Decimal("100"), Decimal("101"), 11],
        ]
    )

    with pytest.raises(ValueError, match="duplicate"):
        parse_candles(payload, secid="IMOEX", market="index")
