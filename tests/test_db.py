import sqlite3

import pytest

from stonksbot.db import SchemaError, bootstrap_database


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
        VALUES ('uid-sber', 'SBER', 'share', 1, 10, 0, 10000000, 'approved', 'test', 1, 0)
        """
    )
    return connection


def test_bootstrap_creates_core_tables_and_guard_state() -> None:
    connection = sqlite3.connect(":memory:")

    bootstrap_database(connection, now_ms=123)

    table_names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    }
    assert {
        "instrument_reference",
        "orders",
        "cash_events",
        "audit_journal",
        "control_state",
        "guard_state",
    }.issubset(table_names)
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("SELECT id, updated_at FROM guard_state").fetchone() == (1, 123)
    assert connection.execute("PRAGMA user_version").fetchone() == (1,)


def test_bootstrap_rejects_existing_unversioned_schema() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE instrument_reference (
          instrument_uid TEXT PRIMARY KEY,
          ticker TEXT NOT NULL
        )
        """
    )

    with pytest.raises(SchemaError, match="unsupported SQLite schema version"):
        bootstrap_database(connection, now_ms=0)


def test_bootstrap_accepts_current_schema_reentry() -> None:
    connection = sqlite3.connect(":memory:")

    bootstrap_database(connection, now_ms=123)
    bootstrap_database(connection, now_ms=456)

    assert connection.execute("PRAGMA user_version").fetchone() == (1,)
    assert connection.execute("SELECT id, updated_at FROM guard_state").fetchone() == (1, 123)


def test_orders_type_rejects_non_limit_orders() -> None:
    connection = _connection_with_share()

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO orders (
              order_id, instrument_uid, account_id, side, type, price_units, price_nano,
              lots, state, created_at, updated_at
            )
            VALUES (
              'order-1', 'uid-sber', 'BOT-1', 'buy', 'MARKET', 100, 0,
              1, 'submitted', 0, 0
            )
            """
        )


def test_orders_reject_negative_or_split_sign_limit_prices() -> None:
    connection = _connection_with_share()

    for order_id, price_units, price_nano in [
        ("negative-units", -1, 0),
        ("negative-nano", 0, -1),
        ("split-sign", 100, -1),
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO orders (
                  order_id, instrument_uid, account_id, side, type, price_units, price_nano,
                  lots, state, created_at, updated_at
                )
                VALUES (?, 'uid-sber', 'BOT-1', 'buy', 'LIMIT', ?, ?, 1, 'submitted', 0, 0)
                """,
                (order_id, price_units, price_nano),
            )


def test_candles_reject_negative_prices_and_volume() -> None:
    connection = _connection_with_share()
    base_row = {
        "instrument_uid": "uid-sber",
        "interval": "1day",
        "ts": 1,
        "source_version": 1,
        "open_units": 100,
        "open_nano": 0,
        "high_units": 101,
        "high_nano": 0,
        "low_units": 99,
        "low_nano": 0,
        "close_units": 100,
        "close_nano": 0,
        "volume": 1000,
        "is_complete": 1,
        "source": "test",
        "as_of": 1,
    }

    for field, impossible_value in [
        ("open_units", -1),
        ("high_nano", -1),
        ("low_units", -1),
        ("close_nano", -1),
        ("volume", -1),
    ]:
        row = base_row | {field: impossible_value, "ts": base_row["ts"] + 1}
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO candles (
                  instrument_uid, interval, ts, source_version,
                  open_units, open_nano, high_units, high_nano,
                  low_units, low_nano, close_units, close_nano,
                  volume, is_complete, source, as_of
                )
                VALUES (
                  :instrument_uid, :interval, :ts, :source_version,
                  :open_units, :open_nano, :high_units, :high_nano,
                  :low_units, :low_nano, :close_units, :close_nano,
                  :volume, :is_complete, :source, :as_of
                )
                """,
                row,
            )


def test_reference_data_rejects_impossible_lot_and_min_tick() -> None:
    connection = sqlite3.connect(":memory:")
    bootstrap_database(connection, now_ms=0)

    for ticker, lot, units, nano in [
        ("BADLOT", -1, 0, 10000000),
        ("BADTICKU", 10, -1, 0),
        ("BADTICKN", 10, 0, -1),
        ("BADTICK_MISSING_UNITS", 10, None, 1),
        ("BADTICK_MISSING_NANO", 10, 1, None),
        ("BADTICK_ZERO", 10, 0, 0),
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO instrument_reference (
                  instrument_uid, ticker, instrument_kind, is_tradable, lot,
                  min_price_increment_units, min_price_increment_nano, whitelist_status,
                  source, source_version, as_of
                )
                VALUES (?, ?, 'share', 1, ?, ?, ?, 'approved', 'test', 1, 0)
                """,
                (f"uid-{ticker}", ticker, lot, units, nano),
            )


def test_dividends_reject_incomplete_optional_close_price_pair() -> None:
    connection = _connection_with_share()

    for last_buy_date, close_units, close_nano in [
        (1, None, 1),
        (2, 1, None),
        (3, 0, 0),
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO dividends (
                  instrument_uid, last_buy_date, gross_units, gross_nano,
                  close_price_units, close_price_nano, currency,
                  source, source_version, as_of
                )
                VALUES ('uid-sber', ?, 1, 0, ?, ?, 'rub', 'test', 1, 0)
                """,
                (last_buy_date, close_units, close_nano),
            )


def test_positions_qty_rejects_short_shaped_state() -> None:
    connection = _connection_with_share()

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO positions (
              instrument_uid, account_id, qty, avg_price_units, avg_price_nano,
              opened_at, source, state
            )
            VALUES ('uid-sber', 'BOT-1', -1, 100, 0, 0, 'bot', 'open')
            """
        )


def test_positions_reject_negative_average_price() -> None:
    connection = _connection_with_share()

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO positions (
              instrument_uid, account_id, qty, avg_price_units, avg_price_nano,
              opened_at, source, state
            )
            VALUES ('uid-sber', 'BOT-1', 1, -1, 0, 0, 'bot', 'open')
            """
        )


def test_audit_journal_is_append_only() -> None:
    connection = sqlite3.connect(":memory:")
    bootstrap_database(connection, now_ms=0)
    connection.execute(
        """
        INSERT INTO audit_journal (ts, account_id, event, actor, detail)
        VALUES (1, 'BOT-1', 'test_event', 'system', '{}')
        """
    )

    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute("UPDATE audit_journal SET event = 'rewritten' WHERE id = 1")

    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute("DELETE FROM audit_journal WHERE id = 1")
