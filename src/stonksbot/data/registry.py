"""Universe registry materialization + index seeding (managed-registry law).

The owner-committed ``[universe]`` config lists are the declarative source of
registry membership (universe-eligibility contract §2.1); this module
materializes them into ``instrument_reference`` rows. The bot gains no
autonomous registry write here: a config-driven status change IS an owner
decision, and every transition is journaled append-only to ``audit_journal``
with an actor (universe-eligibility §6). Rows are never deleted or demoted as
a side effect — a share row absent from every config list is surfaced as an
orphan for owner attention, never touched (frozen managed-registry law).

Identity before the T-Invest adapter exists (PROVISIONAL — owner decision 2.1
in ``docs/ops/pre-live-owner-decisions.md``): rows created from MOEX-ISS-era
config use a namespaced synthetic uid ``moex_iss:{kind}:{SECID}``. The prefix
cannot collide with T-Invest's opaque uids; ``source='moex_iss'`` keeps the
provenance auditable. Index uids are effectively permanent (T-Invest exposes
no index candles — data-layer §3.4); share uids are placeholders to be
re-stitched by ISIN via ``identifier_history`` when the T-Invest reference
refresh lands (data-layer §2).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from stonksbot.data.store import VALID_DATA_SOURCES, DataSource, UniverseStatus

MOEX_UID_PREFIX = "moex_iss"
DEFAULT_INDEX_SECIDS = ("IMOEX", "MCFTR")

InstrumentKind = Literal["share", "index"]


@dataclass(frozen=True)
class RegistryChange:
    """One journaled whitelist_status transition (insert or in-place update)."""

    instrument_uid: str
    ticker: str
    old_status: UniverseStatus | None
    new_status: UniverseStatus


@dataclass(frozen=True)
class RegistryResult:
    changes: tuple[RegistryChange, ...]
    orphans: tuple[str, ...]  # share tickers present in DB but absent from every config list


def moex_synthetic_uid(kind: InstrumentKind, secid: str) -> str:
    """Namespaced synthetic instrument_uid for pre-T-Invest rows (PROVISIONAL, 2.1)."""
    if kind not in ("share", "index"):
        raise ValueError(f"unknown instrument kind: {kind!r}")
    normalized = secid.strip().upper()
    if not normalized:
        raise ValueError("secid must be non-empty")
    return f"{MOEX_UID_PREFIX}:{kind}:{normalized}"


def materialize_universe_registry(
    connection: sqlite3.Connection,
    *,
    statuses_by_ticker: Mapping[str, UniverseStatus],
    as_of: int,
    uids_by_ticker: Mapping[str, str] | None = None,
    source: DataSource = "moex_iss",
    actor: str = "system",
    config_origin: str = "config/config.toml [universe]",
) -> RegistryResult:
    """Materialize the owner-committed universe lists into instrument_reference.

    Idempotent: a re-run with the same config changes no rows and journals
    nothing. A status transition updates the row in place (the reference table
    is mutable-with-provenance — uid-only PK, unlike version-keyed candles),
    bumps ``source_version``, and appends one ``audit_journal`` row. Rows are
    never deleted; shares missing from the config are returned as ``orphans``.

    Two-pass discipline: pass 1 is read-only planning — every validation raise
    happens BEFORE the first write, so a failed materialization can never leave
    half-written rows in the caller's transaction; pass 2 performs the writes
    and journals each transition immediately after its own write.
    """
    _validate_source(source)
    if not statuses_by_ticker:
        raise ValueError("universe config resolved to zero tickers; refusing to materialize")

    # Pass 1 — read-only planning + validation (no writes).
    inserts: list[tuple[str, str, UniverseStatus]] = []
    updates: list[tuple[str, str, UniverseStatus, UniverseStatus, int]] = []
    for ticker in sorted(statuses_by_ticker):
        status = statuses_by_ticker[ticker]
        normalized = ticker.strip().upper()
        if not normalized or normalized != ticker:
            raise ValueError(f"ticker must be pre-normalized (strip/upper): {ticker!r}")
        if uids_by_ticker is not None and normalized in uids_by_ticker:
            uid = uids_by_ticker[normalized]
            if not uid.strip():
                raise ValueError(f"instrument_uid for {normalized} must be non-empty")
        else:
            uid = moex_synthetic_uid("share", normalized)

        # A ticker must never end up under two share uids at once: a supplied
        # uid that differs from an existing row's uid is an identifier
        # transition (re-stitch via identifier_history, data-layer §2), not a
        # silent second row with a stale status.
        clash = connection.execute(
            """
            SELECT instrument_uid
            FROM instrument_reference
            WHERE ticker = ?
              AND instrument_kind = 'share'
              AND instrument_uid <> ?
            """,
            (normalized, uid),
        ).fetchone()
        if clash is not None:
            raise ValueError(
                f"ticker {normalized} already exists under uid {clash[0]}; refusing to "
                f"create a second share row for uid {uid} — re-stitch identifiers explicitly"
            )

        row = connection.execute(
            """
            SELECT ticker, instrument_kind, whitelist_status, source_version
            FROM instrument_reference
            WHERE instrument_uid = ?
            """,
            (uid,),
        ).fetchone()
        if row is None:
            inserts.append((uid, normalized, status))
            continue

        existing_ticker, existing_kind, existing_status, existing_version = (
            str(row[0]),
            str(row[1]),
            row[2],
            int(row[3]),
        )
        if existing_kind != "share" or existing_ticker != normalized:
            raise ValueError(
                f"instrument_uid {uid} already maps to {existing_kind} {existing_ticker}; "
                f"refusing to repurpose it for share {normalized}"
            )
        if existing_status == status:
            continue
        updates.append((uid, normalized, existing_status, status, existing_version))

    # Pass 2 — writes; each transition is journaled right after its own write.
    changes: list[RegistryChange] = []
    for uid, normalized, status in inserts:
        connection.execute(
            """
            INSERT INTO instrument_reference (
              instrument_uid, ticker, instrument_kind, is_tradable,
              whitelist_status, source, source_version, as_of
            )
            VALUES (?, ?, 'share', 1, ?, ?, 1, ?)
            """,
            (uid, normalized, status, source, as_of),
        )
        change = RegistryChange(uid, normalized, None, status)
        _journal_status_change(
            connection, change=change, as_of=as_of, actor=actor, config_origin=config_origin
        )
        changes.append(change)
    for uid, normalized, old_status, status, existing_version in updates:
        connection.execute(
            """
            UPDATE instrument_reference
            SET whitelist_status = ?, source = ?, source_version = ?, as_of = ?
            WHERE instrument_uid = ?
            """,
            (status, source, existing_version + 1, as_of, uid),
        )
        change = RegistryChange(uid, normalized, old_status, status)
        _journal_status_change(
            connection, change=change, as_of=as_of, actor=actor, config_origin=config_origin
        )
        changes.append(change)

    placeholders = ",".join("?" for _ in statuses_by_ticker)
    orphan_rows = connection.execute(
        f"""
        SELECT ticker
        FROM instrument_reference
        WHERE instrument_kind = 'share'
          AND ticker NOT IN ({placeholders})
        ORDER BY ticker
        """,
        sorted(statuses_by_ticker),
    ).fetchall()
    return RegistryResult(
        changes=tuple(changes),
        orphans=tuple(str(row[0]) for row in orphan_rows),
    )


def seed_index_reference(
    connection: sqlite3.Connection,
    *,
    secid: str,
    as_of: int,
    instrument_uid: str | None = None,
    source: DataSource = "moex_iss",
) -> str:
    """Seed one index row (IMOEX/MCFTR): kind='index', not tradable, NULL status.

    Idempotent: re-seeding an existing matching row is a no-op. Benchmarks
    ``cash`` / ``equal_weight`` are synthetic and must never get a DB row
    (db-schema §3.1) — this function takes explicit index secids only.
    """
    _validate_source(source)
    normalized = secid.strip().upper()
    if not normalized:
        raise ValueError("secid must be non-empty")
    uid = instrument_uid if instrument_uid is not None else moex_synthetic_uid("index", normalized)

    row = connection.execute(
        """
        SELECT ticker, instrument_kind
        FROM instrument_reference
        WHERE instrument_uid = ?
        """,
        (uid,),
    ).fetchone()
    if row is not None:
        if str(row[1]) != "index" or str(row[0]) != normalized:
            raise ValueError(
                f"instrument_uid {uid} already maps to {row[1]} {row[0]}; "
                f"refusing to reuse it for index {normalized}"
            )
        return uid

    connection.execute(
        """
        INSERT INTO instrument_reference (
          instrument_uid, ticker, instrument_kind, is_tradable,
          whitelist_status, source, source_version, as_of
        )
        VALUES (?, ?, 'index', 0, NULL, ?, 1, ?)
        """,
        (uid, normalized, source, as_of),
    )
    return uid


def _journal_status_change(
    connection: sqlite3.Connection,
    *,
    change: RegistryChange,
    as_of: int,
    actor: str,
    config_origin: str,
) -> None:
    detail = json.dumps(
        {
            "instrument_uid": change.instrument_uid,
            "ticker": change.ticker,
            "old_status": change.old_status,
            "new_status": change.new_status,
            "config_origin": config_origin,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    connection.execute(
        """
        INSERT INTO audit_journal (ts, event, actor, detail)
        VALUES (?, 'universe_status_change', ?, ?)
        """,
        (as_of, actor, detail),
    )


def _validate_source(source: str) -> None:
    if source not in VALID_DATA_SOURCES:
        raise ValueError(f"source must be one of {sorted(VALID_DATA_SOURCES)}")


__all__ = [
    "DEFAULT_INDEX_SECIDS",
    "MOEX_UID_PREFIX",
    "RegistryChange",
    "RegistryResult",
    "materialize_universe_registry",
    "moex_synthetic_uid",
    "seed_index_reference",
]
