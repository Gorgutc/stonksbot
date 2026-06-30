from __future__ import annotations

import sqlite3


SCHEMA_VERSION = 1


class SchemaError(RuntimeError):
    """Raised when an existing SQLite database cannot be safely bootstrapped."""


DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS instrument_reference (
  instrument_uid TEXT PRIMARY KEY,
  ticker TEXT NOT NULL,
  instrument_kind TEXT NOT NULL CHECK (instrument_kind IN ('share','index')),
  is_tradable INTEGER NOT NULL CHECK (is_tradable IN (0,1)),
  lot INTEGER CHECK (lot IS NULL OR lot > 0),
  min_price_increment_units INTEGER,
  min_price_increment_nano INTEGER CHECK (
    min_price_increment_nano IS NULL OR min_price_increment_nano BETWEEN 0 AND 999999999
  ),
  currency TEXT,
  trading_status TEXT,
  whitelist_status TEXT CHECK (
    whitelist_status IN ('approved','managed_only','watch_only','blocked','pending')
  ),
  first_1day_candle_date INTEGER,
  avg_turnover_rub INTEGER,
  spread_bps INTEGER,
  data_status TEXT NOT NULL DEFAULT 'ok' CHECK (data_status IN ('ok','data_conflict')),
  identifier_history TEXT,
  source TEXT NOT NULL,
  source_version INTEGER NOT NULL,
  as_of INTEGER NOT NULL,
  CHECK (
    (instrument_kind = 'index' AND is_tradable = 0 AND whitelist_status IS NULL)
    OR (instrument_kind = 'share' AND whitelist_status IS NOT NULL)
  ),
  CHECK (
    (min_price_increment_units IS NULL AND min_price_increment_nano IS NULL)
    OR (
      min_price_increment_units IS NOT NULL
      AND min_price_increment_nano IS NOT NULL
      AND
      min_price_increment_units >= 0
      AND min_price_increment_nano >= 0
      AND (min_price_increment_units > 0 OR min_price_increment_nano > 0)
    )
  )
);

CREATE TABLE IF NOT EXISTS candles (
  instrument_uid TEXT NOT NULL REFERENCES instrument_reference(instrument_uid),
  interval TEXT NOT NULL CHECK (interval IN ('1day')),
  ts INTEGER NOT NULL,
  source_version INTEGER NOT NULL,
  open_units INTEGER NOT NULL CHECK (open_units >= 0),
  open_nano INTEGER NOT NULL CHECK (open_nano BETWEEN 0 AND 999999999),
  high_units INTEGER NOT NULL CHECK (high_units >= 0),
  high_nano INTEGER NOT NULL CHECK (high_nano BETWEEN 0 AND 999999999),
  low_units INTEGER NOT NULL CHECK (low_units >= 0),
  low_nano INTEGER NOT NULL CHECK (low_nano BETWEEN 0 AND 999999999),
  close_units INTEGER NOT NULL CHECK (close_units >= 0),
  close_nano INTEGER NOT NULL CHECK (close_nano BETWEEN 0 AND 999999999),
  volume INTEGER NOT NULL CHECK (volume >= 0),
  is_complete INTEGER NOT NULL CHECK (is_complete IN (0,1)),
  is_stale INTEGER NOT NULL DEFAULT 0 CHECK (is_stale IN (0,1)),
  adjusted INTEGER NOT NULL DEFAULT 0 CHECK (adjusted IN (0,1)),
  source TEXT NOT NULL,
  as_of INTEGER NOT NULL,
  CHECK (open_units > 0 OR open_nano > 0),
  CHECK (high_units > 0 OR high_nano > 0),
  CHECK (low_units > 0 OR low_nano > 0),
  CHECK (close_units > 0 OR close_nano > 0),
  PRIMARY KEY (instrument_uid, interval, ts, source_version)
);

CREATE TABLE IF NOT EXISTS dividends (
  instrument_uid TEXT NOT NULL REFERENCES instrument_reference(instrument_uid),
  last_buy_date INTEGER NOT NULL,
  ex_date INTEGER,
  record_date INTEGER,
  payment_date INTEGER,
  declared_date INTEGER,
  gross_units INTEGER NOT NULL CHECK (gross_units >= 0),
  gross_nano INTEGER NOT NULL CHECK (gross_nano BETWEEN 0 AND 999999999),
  dividend_type TEXT,
  close_price_units INTEGER CHECK (close_price_units IS NULL OR close_price_units >= 0),
  close_price_nano INTEGER CHECK (
    close_price_nano IS NULL OR close_price_nano BETWEEN 0 AND 999999999
  ),
  currency TEXT NOT NULL,
  source TEXT NOT NULL,
  source_version INTEGER NOT NULL,
  as_of INTEGER NOT NULL,
  CHECK (gross_units > 0 OR gross_nano > 0),
  CHECK (
    (close_price_units IS NULL AND close_price_nano IS NULL)
    OR (
      close_price_units IS NOT NULL
      AND close_price_nano IS NOT NULL
      AND
      close_price_units >= 0
      AND close_price_nano >= 0
      AND (close_price_units > 0 OR close_price_nano > 0)
    )
  ),
  PRIMARY KEY (instrument_uid, last_buy_date, source_version)
);

CREATE TABLE IF NOT EXISTS data_conflicts (
  id INTEGER PRIMARY KEY,
  instrument_uid TEXT NOT NULL REFERENCES instrument_reference(instrument_uid),
  ts INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('close_divergence','missing_bar','duplicate_bar')),
  detail TEXT,
  resolved INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0,1)),
  as_of INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY,
  instrument_uid TEXT NOT NULL REFERENCES instrument_reference(instrument_uid),
  ts INTEGER NOT NULL,
  features TEXT,
  decision TEXT NOT NULL CHECK (decision IN ('candidate','selected','skipped','risk_rejected')),
  reason TEXT,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS proposals (
  proposal_id TEXT PRIMARY KEY,
  signal_id INTEGER NOT NULL REFERENCES signals(id),
  created_at INTEGER NOT NULL,
  ttl_ms INTEGER NOT NULL,
  telegram_user_id INTEGER NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('awaiting_confirmation','confirmed','rejected','expired'))
);

CREATE TABLE IF NOT EXISTS orders (
  order_id TEXT PRIMARY KEY,
  proposal_id TEXT REFERENCES proposals(proposal_id),
  instrument_uid TEXT NOT NULL REFERENCES instrument_reference(instrument_uid),
  account_id TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('buy','sell')),
  type TEXT NOT NULL CHECK (type = 'LIMIT'),
  price_units INTEGER NOT NULL CHECK (price_units >= 0),
  price_nano INTEGER NOT NULL CHECK (price_nano BETWEEN 0 AND 999999999),
  lots INTEGER NOT NULL CHECK (lots > 0),
  state TEXT NOT NULL CHECK (
    state IN ('submitted','partially_filled','filled','cancel_requested','cancelled','reconcile_required')
  ),
  broker_order_id TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  CHECK (price_units > 0 OR price_nano > 0)
);

CREATE TABLE IF NOT EXISTS fills (
  id INTEGER PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES orders(order_id),
  ts INTEGER NOT NULL,
  price_units INTEGER NOT NULL CHECK (price_units >= 0),
  price_nano INTEGER NOT NULL CHECK (price_nano BETWEEN 0 AND 999999999),
  qty INTEGER NOT NULL CHECK (qty > 0),
  commission_units INTEGER NOT NULL CHECK (commission_units >= 0),
  commission_nano INTEGER NOT NULL CHECK (commission_nano BETWEEN 0 AND 999999999),
  source TEXT NOT NULL,
  as_of INTEGER NOT NULL,
  CHECK (price_units > 0 OR price_nano > 0)
);

CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY,
  instrument_uid TEXT NOT NULL REFERENCES instrument_reference(instrument_uid),
  account_id TEXT NOT NULL,
  qty INTEGER NOT NULL CHECK (qty > 0),
  avg_price_units INTEGER NOT NULL CHECK (avg_price_units >= 0),
  avg_price_nano INTEGER NOT NULL CHECK (avg_price_nano BETWEEN 0 AND 999999999),
  opened_at INTEGER NOT NULL,
  closed_at INTEGER,
  source TEXT NOT NULL CHECK (source IN ('bot','manual_adopted','managed_only')),
  exit_rules TEXT,
  state TEXT NOT NULL CHECK (state IN ('open','closed')),
  close_reason TEXT CHECK (close_reason IN ('risk','trend','target_trailing','time','manual')),
  CHECK (
    (state = 'open' AND closed_at IS NULL AND close_reason IS NULL)
    OR (state = 'closed' AND closed_at IS NOT NULL AND close_reason IS NOT NULL)
  ),
  CHECK (avg_price_units > 0 OR avg_price_nano > 0)
);

CREATE TABLE IF NOT EXISTS cash_events (
  id INTEGER PRIMARY KEY,
  account_id TEXT NOT NULL,
  ts INTEGER NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('deposit','withdrawal','commission','tax','dividend')),
  amount_units INTEGER NOT NULL,
  amount_nano INTEGER NOT NULL CHECK (amount_nano BETWEEN -999999999 AND 999999999),
  currency TEXT NOT NULL,
  ref_order_id TEXT REFERENCES orders(order_id),
  ref_position_id INTEGER REFERENCES positions(id),
  note TEXT,
  as_of INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS reconciliations (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  kind TEXT NOT NULL,
  result TEXT NOT NULL CHECK (result IN ('clean','mismatch','blocked')),
  mismatch TEXT,
  as_of INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS control_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  mode TEXT NOT NULL CHECK (mode IN ('running','paused','killed','blocked_reconciliation_mismatch')),
  since_ts INTEGER NOT NULL,
  reason TEXT,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS guard_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  last_account_id TEXT,
  account_id_change_confirmed_at INTEGER,
  account_id_change_confirmed_by TEXT,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_journal (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  account_id TEXT,
  event TEXT NOT NULL,
  proposal_id TEXT REFERENCES proposals(proposal_id),
  order_id TEXT REFERENCES orders(order_id),
  position_id INTEGER REFERENCES positions(id),
  actor TEXT,
  detail TEXT
);

CREATE TRIGGER IF NOT EXISTS audit_journal_no_update BEFORE UPDATE ON audit_journal
  BEGIN SELECT RAISE(ABORT, 'audit_journal is append-only'); END;

CREATE TRIGGER IF NOT EXISTS audit_journal_no_delete BEFORE DELETE ON audit_journal
  BEGIN SELECT RAISE(ABORT, 'audit_journal is append-only'); END;

CREATE INDEX IF NOT EXISTS idx_candles_uid_ts ON candles(instrument_uid, ts);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts);
CREATE INDEX IF NOT EXISTS idx_signals_uid ON signals(instrument_uid);
CREATE INDEX IF NOT EXISTS idx_orders_state ON orders(state);
CREATE INDEX IF NOT EXISTS idx_orders_proposal ON orders(proposal_id);
CREATE INDEX IF NOT EXISTS idx_positions_state ON positions(state);
CREATE INDEX IF NOT EXISTS idx_positions_uid ON positions(instrument_uid);
CREATE INDEX IF NOT EXISTS idx_dividends_uid_lastbuy ON dividends(instrument_uid, last_buy_date);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_journal(ts);
CREATE INDEX IF NOT EXISTS idx_cash_events_ts ON cash_events(ts);
"""


def bootstrap_database(connection: sqlite3.Connection, *, now_ms: int) -> None:
    _reject_unsupported_existing_schema(connection)
    connection.executescript(DDL)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    connection.execute(
        """
        INSERT INTO guard_state (id, updated_at)
        VALUES (1, ?)
        ON CONFLICT(id) DO UPDATE SET updated_at = guard_state.updated_at
        """,
        (now_ms,),
    )
    connection.commit()


def _reject_unsupported_existing_schema(connection: sqlite3.Connection) -> None:
    if not _core_schema_exists(connection):
        return

    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        raise SchemaError(
            f"unsupported SQLite schema version {version}; expected {SCHEMA_VERSION}. "
            "Recreate or migrate the database before bootstrap."
        )


def _core_schema_exists(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name IN ('instrument_reference', 'candles', 'orders', 'positions')
        LIMIT 1
        """
    ).fetchone()
    return row is not None
