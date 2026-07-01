import sqlite3

import pytest

from stonksbot.data.registry import (
    materialize_universe_registry,
    moex_synthetic_uid,
    seed_index_reference,
)
from stonksbot.data.store import build_universe_status_map
from stonksbot.db import bootstrap_database

RATIFIED_LISTS = {
    "approved": ["SBER", "T", "GAZP", "ROSN", "TATN", "X5"],
    "watch_only": ["IRAO", "LKOH"],
}


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    bootstrap_database(connection, now_ms=0)
    return connection


def _ratified_statuses() -> dict[str, str]:
    return build_universe_status_map(**RATIFIED_LISTS)


def _audit_count(connection: sqlite3.Connection) -> int:
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM audit_journal WHERE event = 'universe_status_change'"
        ).fetchone()[0]
    )


def test_materialize_creates_ratified_share_rows() -> None:
    connection = _connection()

    result = materialize_universe_registry(
        connection, statuses_by_ticker=_ratified_statuses(), as_of=1_000
    )

    assert len(result.changes) == 8
    assert result.orphans == ()
    rows = connection.execute(
        """
        SELECT instrument_uid, ticker, instrument_kind, is_tradable,
               whitelist_status, source, source_version, as_of
        FROM instrument_reference
        ORDER BY ticker
        """
    ).fetchall()
    assert len(rows) == 8
    by_ticker = {row[1]: row for row in rows}
    assert by_ticker["SBER"][0] == moex_synthetic_uid("share", "SBER") == "moex_iss:share:SBER"
    assert by_ticker["SBER"][4] == "approved"
    assert by_ticker["IRAO"][4] == "watch_only"
    for row in rows:
        assert row[2] == "share"
        assert row[3] == 1
        assert row[5] == "moex_iss"
        assert row[6] == 1
        assert row[7] == 1_000
    assert _audit_count(connection) == 8


def test_materialize_is_idempotent() -> None:
    connection = _connection()
    materialize_universe_registry(
        connection, statuses_by_ticker=_ratified_statuses(), as_of=1_000
    )

    result = materialize_universe_registry(
        connection, statuses_by_ticker=_ratified_statuses(), as_of=2_000
    )

    assert result.changes == ()
    assert _audit_count(connection) == 8
    version = connection.execute(
        "SELECT source_version, as_of FROM instrument_reference WHERE ticker = 'SBER'"
    ).fetchone()
    assert (int(version[0]), int(version[1])) == (1, 1_000)


def test_status_transition_updates_in_place_and_journals() -> None:
    connection = _connection()
    materialize_universe_registry(
        connection, statuses_by_ticker=_ratified_statuses(), as_of=1_000
    )
    moved = build_universe_status_map(
        approved=["T", "GAZP", "ROSN", "TATN", "X5"],
        watch_only=["IRAO", "LKOH"],
        blocked=["SBER"],
    )

    result = materialize_universe_registry(connection, statuses_by_ticker=moved, as_of=2_000)

    assert len(result.changes) == 1
    change = result.changes[0]
    assert (change.ticker, change.old_status, change.new_status) == ("SBER", "approved", "blocked")
    row = connection.execute(
        "SELECT whitelist_status, source_version, as_of FROM instrument_reference "
        "WHERE ticker = 'SBER'"
    ).fetchone()
    assert (row[0], int(row[1]), int(row[2])) == ("blocked", 2, 2_000)
    assert _audit_count(connection) == 9
    detail = connection.execute(
        "SELECT detail FROM audit_journal WHERE event = 'universe_status_change' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    assert '"old_status":"approved"' in detail
    assert '"new_status":"blocked"' in detail


def test_materialize_rejects_empty_universe() -> None:
    connection = _connection()

    with pytest.raises(ValueError, match="zero tickers"):
        materialize_universe_registry(connection, statuses_by_ticker={}, as_of=1_000)


def test_orphan_share_is_surfaced_not_touched() -> None:
    connection = _connection()
    connection.execute(
        """
        INSERT INTO instrument_reference (
          instrument_uid, ticker, instrument_kind, is_tradable,
          whitelist_status, source, source_version, as_of
        )
        VALUES ('moex_iss:share:OLDT', 'OLDT', 'share', 1, 'approved', 'moex_iss', 1, 5)
        """
    )

    result = materialize_universe_registry(
        connection, statuses_by_ticker=_ratified_statuses(), as_of=1_000
    )

    assert result.orphans == ("OLDT",)
    row = connection.execute(
        "SELECT whitelist_status, source_version, as_of FROM instrument_reference "
        "WHERE ticker = 'OLDT'"
    ).fetchone()
    assert (row[0], int(row[1]), int(row[2])) == ("approved", 1, 5)


def test_seed_index_row_shape_and_idempotence() -> None:
    connection = _connection()

    uid = seed_index_reference(connection, secid="IMOEX", as_of=1_000)
    again = seed_index_reference(connection, secid="imoex", as_of=2_000)

    assert uid == again == "moex_iss:index:IMOEX"
    rows = connection.execute(
        """
        SELECT ticker, instrument_kind, is_tradable, whitelist_status, lot, source_version, as_of
        FROM instrument_reference
        WHERE instrument_uid = ?
        """,
        (uid,),
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert (row[0], row[1], int(row[2])) == ("IMOEX", "index", 0)
    assert row[3] is None
    assert row[4] is None
    assert (int(row[5]), int(row[6])) == (1, 1_000)


def test_seed_rejects_uid_reuse_for_different_instrument() -> None:
    connection = _connection()
    materialize_universe_registry(
        connection, statuses_by_ticker=_ratified_statuses(), as_of=1_000
    )

    with pytest.raises(ValueError, match="refusing to reuse"):
        seed_index_reference(
            connection, secid="IMOEX", as_of=2_000, instrument_uid="moex_iss:share:SBER"
        )


def test_materialize_rejects_uid_mapped_to_index() -> None:
    connection = _connection()
    index_uid = seed_index_reference(connection, secid="IMOEX", as_of=1_000)

    with pytest.raises(ValueError, match="refusing to repurpose"):
        materialize_universe_registry(
            connection,
            statuses_by_ticker=build_universe_status_map(approved=["SBER"]),
            uids_by_ticker={"SBER": index_uid},
            as_of=2_000,
        )


def test_materialize_rejects_second_uid_for_existing_ticker() -> None:
    connection = _connection()
    materialize_universe_registry(
        connection, statuses_by_ticker=_ratified_statuses(), as_of=1_000
    )

    # A future T-Invest uid for an already-materialized ticker is an identifier
    # transition, not a silent second share row with a stale status.
    with pytest.raises(ValueError, match="re-stitch identifiers explicitly"):
        materialize_universe_registry(
            connection,
            statuses_by_ticker=build_universe_status_map(blocked=["SBER"]),
            uids_by_ticker={"SBER": "tinvest-uid-123"},
            as_of=2_000,
        )
    rows = connection.execute(
        "SELECT COUNT(*) FROM instrument_reference WHERE ticker = 'SBER'"
    ).fetchone()
    assert int(rows[0]) == 1


def test_failed_materialization_writes_nothing() -> None:
    connection = _connection()
    # 'T' pre-exists under a different uid -> pass-1 planning must raise before
    # any row is written or journaled (validate-before-write discipline).
    connection.execute(
        """
        INSERT INTO instrument_reference (
          instrument_uid, ticker, instrument_kind, is_tradable,
          whitelist_status, source, source_version, as_of
        )
        VALUES ('legacy-uid-t', 'T', 'share', 1, 'approved', 'moex_iss', 1, 5)
        """
    )

    with pytest.raises(ValueError, match="re-stitch identifiers explicitly"):
        materialize_universe_registry(
            connection,
            statuses_by_ticker=_ratified_statuses(),
            uids_by_ticker={"T": "tinvest-uid-t"},
            as_of=1_000,
        )

    count = connection.execute(
        "SELECT COUNT(*) FROM instrument_reference"
    ).fetchone()
    assert int(count[0]) == 1  # only the pre-existing row
    assert _audit_count(connection) == 0


def test_schema_rejects_index_row_with_whitelist_status() -> None:
    connection = _connection()

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO instrument_reference (
              instrument_uid, ticker, instrument_kind, is_tradable,
              whitelist_status, source, source_version, as_of
            )
            VALUES ('moex_iss:index:MCFTR', 'MCFTR', 'index', 0, 'approved', 'moex_iss', 1, 0)
            """
        )
