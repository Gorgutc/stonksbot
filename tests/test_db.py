import sqlite3

import pytest

from stonksbot.db import bootstrap_database


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


def test_orders_type_rejects_non_limit_orders() -> None:
    connection = sqlite3.connect(":memory:")
    bootstrap_database(connection, now_ms=0)
    connection.execute(
        """
        INSERT INTO instrument_reference (
          instrument_uid, ticker, instrument_kind, is_tradable, whitelist_status,
          source, source_version, as_of
        )
        VALUES ('uid-sber', 'SBER', 'share', 1, 'approved', 'test', 1, 0)
        """
    )

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


def test_positions_qty_rejects_short_shaped_state() -> None:
    connection = sqlite3.connect(":memory:")
    bootstrap_database(connection, now_ms=0)
    connection.execute(
        """
        INSERT INTO instrument_reference (
          instrument_uid, ticker, instrument_kind, is_tradable, whitelist_status,
          source, source_version, as_of
        )
        VALUES ('uid-sber', 'SBER', 'share', 1, 'approved', 'test', 1, 0)
        """
    )

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
