import sqlite3

import pytest

from stonksbot.db import SCHEMA_VERSION, SchemaError, bootstrap_database


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


def _confirmed_proposal(connection: sqlite3.Connection, *, proposal_id: str = "proposal-ok") -> str:
    selected_signal_id = connection.execute(
        """
        INSERT INTO signals (instrument_uid, ts, decision, reason, created_at)
        VALUES ('uid-sber', 1, 'selected', NULL, 1)
        RETURNING id
        """
    ).fetchone()[0]
    connection.execute(
        """
        INSERT INTO proposals (
          proposal_id, signal_id, created_at, ttl_ms, telegram_user_id, state
        )
        VALUES (?, ?, 1, 60000, 1, 'confirmed')
        """,
        (proposal_id, selected_signal_id),
    )
    return proposal_id


def _open_position(connection: sqlite3.Connection, *, qty: int = 2) -> int:
    return connection.execute(
        """
        INSERT INTO positions (
          instrument_uid, account_id, qty, avg_price_units, avg_price_nano,
          opened_at, source, state
        )
        VALUES ('uid-sber', 'BOT-1', ?, 100, 0, 0, 'bot', 'open')
        RETURNING id
        """,
        (qty,),
    ).fetchone()[0]


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
    assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)


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

    assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
    assert connection.execute("SELECT id, updated_at FROM guard_state").fetchone() == (1, 123)


def test_bootstrap_rejects_prior_schema_version() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE instrument_reference (instrument_uid TEXT PRIMARY KEY);
        CREATE TABLE candles (id INTEGER PRIMARY KEY);
        CREATE TABLE orders (id INTEGER PRIMARY KEY);
        CREATE TABLE positions (id INTEGER PRIMARY KEY);
        """
    )
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")

    with pytest.raises(SchemaError, match="unsupported SQLite schema version"):
        bootstrap_database(connection, now_ms=0)


def test_signals_reason_is_decision_aware_frozen_skip_code() -> None:
    connection = _connection_with_share()

    connection.execute(
        """
        INSERT INTO signals (instrument_uid, ts, decision, reason, created_at)
        VALUES ('uid-sber', 1, 'candidate', NULL, 1)
        """
    )
    connection.execute(
        """
        INSERT INTO signals (instrument_uid, ts, decision, reason, created_at)
        VALUES ('uid-sber', 2, 'skipped', 'data_conflict', 2)
        """
    )
    connection.execute(
        """
        INSERT INTO signals (instrument_uid, ts, decision, reason, created_at)
        VALUES ('uid-sber', 3, 'risk_rejected', NULL, 3)
        """
    )

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO signals (instrument_uid, ts, decision, reason, created_at)
            VALUES ('uid-sber', 4, 'skipped', 'typo_conflict', 4)
            """
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO signals (instrument_uid, ts, decision, reason, created_at)
            VALUES ('uid-sber', 5, 'skipped', NULL, 5)
            """
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO signals (instrument_uid, ts, decision, reason, created_at)
            VALUES ('uid-sber', 6, 'candidate', 'data_conflict', 6)
            """
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO signals (instrument_uid, ts, decision, reason, created_at)
            VALUES ('uid-sber', 7, 'risk_rejected', 'max_positions', 7)
            """
        )


def test_proposals_can_reference_only_selected_signals() -> None:
    connection = _connection_with_share()
    selected_signal_id = connection.execute(
        """
        INSERT INTO signals (instrument_uid, ts, decision, reason, created_at)
        VALUES ('uid-sber', 1, 'selected', NULL, 1)
        RETURNING id
        """
    ).fetchone()[0]
    skipped_signal_id = connection.execute(
        """
        INSERT INTO signals (instrument_uid, ts, decision, reason, created_at)
        VALUES ('uid-sber', 2, 'skipped', 'data_conflict', 2)
        RETURNING id
        """
    ).fetchone()[0]

    connection.execute(
        """
        INSERT INTO proposals (
          proposal_id, signal_id, created_at, ttl_ms, telegram_user_id, state
        )
        VALUES ('proposal-ok', ?, 1, 60000, 1, 'awaiting_confirmation')
        """,
        (selected_signal_id,),
    )

    with pytest.raises(sqlite3.DatabaseError, match="selected signals"):
        connection.execute(
            """
            INSERT INTO proposals (
              proposal_id, signal_id, created_at, ttl_ms, telegram_user_id, state
            )
            VALUES ('proposal-blocked', ?, 2, 60000, 1, 'awaiting_confirmation')
            """,
            (skipped_signal_id,),
        )


def test_proposal_signal_id_update_must_remain_selected() -> None:
    connection = _connection_with_share()
    selected_signal_id = connection.execute(
        """
        INSERT INTO signals (instrument_uid, ts, decision, reason, created_at)
        VALUES ('uid-sber', 1, 'selected', NULL, 1)
        RETURNING id
        """
    ).fetchone()[0]
    skipped_signal_id = connection.execute(
        """
        INSERT INTO signals (instrument_uid, ts, decision, reason, created_at)
        VALUES ('uid-sber', 2, 'skipped', 'data_conflict', 2)
        RETURNING id
        """
    ).fetchone()[0]
    connection.execute(
        """
        INSERT INTO proposals (
          proposal_id, signal_id, created_at, ttl_ms, telegram_user_id, state
        )
        VALUES ('proposal-ok', ?, 1, 60000, 1, 'awaiting_confirmation')
        """,
        (selected_signal_id,),
    )

    with pytest.raises(sqlite3.DatabaseError, match="selected signals"):
        connection.execute(
            "UPDATE proposals SET signal_id = ? WHERE proposal_id = 'proposal-ok'",
            (skipped_signal_id,),
        )


def test_orders_type_rejects_non_limit_orders() -> None:
    connection = _connection_with_share()
    proposal_id = _confirmed_proposal(connection)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO orders (
              order_id, proposal_id, instrument_uid, account_id, side, type, price_units, price_nano,
              lots, state, created_at, updated_at
            )
            VALUES (
              'order-1', ?, 'uid-sber', 'BOT-1', 'buy', 'MARKET', 100, 0,
              1, 'submitted', 0, 0
            )
            """,
            (proposal_id,),
        )


def test_orders_reject_negative_or_split_sign_limit_prices() -> None:
    connection = _connection_with_share()
    proposal_id = _confirmed_proposal(connection)

    for order_id, price_units, price_nano in [
        ("negative-units", -1, 0),
        ("negative-nano", 0, -1),
        ("split-sign", 100, -1),
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO orders (
                  order_id, proposal_id, instrument_uid, account_id, side, type, price_units, price_nano,
                  lots, state, created_at, updated_at
                )
                VALUES (?, ?, 'uid-sber', 'BOT-1', 'buy', 'LIMIT', ?, ?, 1, 'submitted', 0, 0)
                """,
                (order_id, proposal_id, price_units, price_nano),
            )


def test_orders_require_non_empty_unique_idempotency_key() -> None:
    connection = _connection_with_share()
    proposal_id = _confirmed_proposal(connection)

    for order_id in [None, "", "   "]:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO orders (
                  order_id, proposal_id, instrument_uid, account_id, side, type, price_units, price_nano,
                  lots, state, created_at, updated_at
                )
                VALUES (?, ?, 'uid-sber', 'BOT-1', 'buy', 'LIMIT', 100, 0, 1, 'submitted', 0, 0)
                """,
                (order_id, proposal_id),
            )

    connection.execute(
        """
        INSERT INTO orders (
          order_id, proposal_id, instrument_uid, account_id, side, type, price_units, price_nano,
          lots, state, created_at, updated_at
        )
        VALUES ('order-unique', ?, 'uid-sber', 'BOT-1', 'buy', 'LIMIT', 100, 0, 1, 'submitted', 0, 0)
        """,
        (proposal_id,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO orders (
              order_id, proposal_id, instrument_uid, account_id, side, type, price_units, price_nano,
              lots, state, created_at, updated_at
            )
            VALUES ('order-unique', ?, 'uid-sber', 'BOT-1', 'buy', 'LIMIT', 100, 0, 1, 'submitted', 0, 0)
            """,
            (proposal_id,),
        )


def test_buy_orders_require_confirmed_selected_proposal() -> None:
    connection = _connection_with_share()
    selected_signal_id = connection.execute(
        """
        INSERT INTO signals (instrument_uid, ts, decision, reason, created_at)
        VALUES ('uid-sber', 1, 'selected', NULL, 1)
        RETURNING id
        """
    ).fetchone()[0]
    connection.execute(
        """
        INSERT INTO proposals (
          proposal_id, signal_id, created_at, ttl_ms, telegram_user_id, state
        )
        VALUES ('proposal-pending', ?, 1, 60000, 1, 'awaiting_confirmation')
        """,
        (selected_signal_id,),
    )

    for proposal_id in [None, "proposal-pending"]:
        with pytest.raises(sqlite3.DatabaseError, match="confirmed selected proposal"):
            connection.execute(
                """
                INSERT INTO orders (
                  order_id, proposal_id, instrument_uid, account_id, side, type, price_units, price_nano,
                  lots, state, created_at, updated_at
                )
                VALUES ('order-buy-blocked', ?, 'uid-sber', 'BOT-1', 'buy', 'LIMIT', 100, 0, 1, 'submitted', 0, 0)
                """,
                (proposal_id,),
            )


def test_buy_order_proposal_must_match_order_instrument() -> None:
    connection = _connection_with_share()
    connection.execute(
        """
        INSERT INTO instrument_reference (
          instrument_uid, ticker, instrument_kind, is_tradable, lot,
          min_price_increment_units, min_price_increment_nano, whitelist_status,
          source, source_version, as_of
        )
        VALUES ('uid-gazp', 'GAZP', 'share', 1, 10, 0, 10000000, 'approved', 'moex_iss', 1, 0)
        """
    )
    proposal_id = _confirmed_proposal(connection)

    with pytest.raises(sqlite3.DatabaseError, match="confirmed selected proposal"):
        connection.execute(
            """
            INSERT INTO orders (
              order_id, proposal_id, instrument_uid, account_id, side, type, price_units, price_nano,
              lots, state, created_at, updated_at
            )
            VALUES ('order-wrong-instrument', ?, 'uid-gazp', 'BOT-1', 'buy', 'LIMIT', 100, 0, 1, 'submitted', 0, 0)
            """,
            (proposal_id,),
        )


def test_buy_order_instrument_update_must_still_match_proposal() -> None:
    connection = _connection_with_share()
    connection.execute(
        """
        INSERT INTO instrument_reference (
          instrument_uid, ticker, instrument_kind, is_tradable, lot,
          min_price_increment_units, min_price_increment_nano, whitelist_status,
          source, source_version, as_of
        )
        VALUES ('uid-gazp', 'GAZP', 'share', 1, 10, 0, 10000000, 'approved', 'moex_iss', 1, 0)
        """
    )
    proposal_id = _confirmed_proposal(connection)
    connection.execute(
        """
        INSERT INTO orders (
          order_id, proposal_id, instrument_uid, account_id, side, type, price_units, price_nano,
          lots, state, created_at, updated_at
        )
        VALUES ('order-sber', ?, 'uid-sber', 'BOT-1', 'buy', 'LIMIT', 100, 0, 1, 'submitted', 0, 0)
        """,
        (proposal_id,),
    )

    with pytest.raises(sqlite3.DatabaseError, match="confirmed selected proposal"):
        connection.execute(
            "UPDATE orders SET instrument_uid = 'uid-gazp' WHERE order_id = 'order-sber'"
        )


def test_buy_order_locks_confirmed_proposal_and_selected_signal_chain() -> None:
    connection = _connection_with_share()
    proposal_id = _confirmed_proposal(connection)
    signal_id = connection.execute(
        "SELECT signal_id FROM proposals WHERE proposal_id = ?",
        (proposal_id,),
    ).fetchone()[0]
    replacement_signal_id = connection.execute(
        """
        INSERT INTO signals (instrument_uid, ts, decision, reason, created_at)
        VALUES ('uid-sber', 2, 'selected', NULL, 2)
        RETURNING id
        """
    ).fetchone()[0]
    connection.execute(
        """
        INSERT INTO orders (
          order_id, proposal_id, instrument_uid, account_id, side, type, price_units, price_nano,
          lots, state, created_at, updated_at
        )
        VALUES ('order-sber', ?, 'uid-sber', 'BOT-1', 'buy', 'LIMIT', 100, 0, 1, 'submitted', 0, 0)
        """,
        (proposal_id,),
    )

    with pytest.raises(sqlite3.DatabaseError, match="confirmed proposal is locked"):
        connection.execute(
            "UPDATE proposals SET state = 'rejected' WHERE proposal_id = ?",
            (proposal_id,),
        )
    with pytest.raises(sqlite3.DatabaseError, match="proposal signal is locked"):
        connection.execute(
            "UPDATE proposals SET signal_id = ? WHERE proposal_id = ?",
            (replacement_signal_id, proposal_id),
        )
    with pytest.raises(sqlite3.DatabaseError, match="selected signal is locked"):
        connection.execute(
            """
            UPDATE signals
            SET decision = 'skipped', reason = 'data_conflict'
            WHERE id = ?
            """,
            (signal_id,),
        )
    with pytest.raises(sqlite3.DatabaseError, match="signal instrument is locked"):
        connection.execute(
            "UPDATE signals SET instrument_uid = 'uid-missing' WHERE id = ?",
            (signal_id,),
        )


def test_sell_orders_require_open_matching_position() -> None:
    connection = _connection_with_share()
    position_id = _open_position(connection, qty=2)

    connection.execute(
        """
        INSERT INTO orders (
          order_id, position_id, instrument_uid, account_id, side, type, price_units, price_nano,
          lots, state, created_at, updated_at
        )
        VALUES ('order-sell-ok', ?, 'uid-sber', 'BOT-1', 'sell', 'LIMIT', 100, 0, 1, 'submitted', 0, 0)
        """,
        (position_id,),
    )

    with pytest.raises(sqlite3.DatabaseError, match="open matching position"):
        connection.execute(
            """
            INSERT INTO orders (
              order_id, position_id, instrument_uid, account_id, side, type, price_units, price_nano,
              lots, state, created_at, updated_at
            )
            VALUES ('order-sell-too-many', ?, 'uid-sber', 'BOT-1', 'sell',  'LIMIT', 100, 0, 3, 'submitted', 0, 0)
            """,
            (position_id,),
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
