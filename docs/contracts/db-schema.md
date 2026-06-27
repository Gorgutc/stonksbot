# Contract — SQLite schema DDL (TZ §5.1)

> **Status:** M0 contract, **resolved on paper (no DB code yet)**. This is the **build-ready DDL** the M0
> `data/` layer will create verbatim. It bakes the irreversible type/key/enum choices. **`docs/frozen-decisions.md` 🔒
> wins.** Enum vocabularies below are **frozen** — a divergent enum silently weakens an invariant.
> **[verify whq6u1gxe]** = a note pending the research workflow (index source, dividend source).
> Pairs with [config-and-secrets.md](config-and-secrets.md) and [tax-and-dividends.md](tax-and-dividends.md).

---

## 1. Type rules (non-negotiable)
- **Money/price = NEVER float.** Store the T-Invest **Quotation**: `*_units` INTEGER + `*_nano` INTEGER, where
  value = `units + nano/1e9`. Applies to every price, `min_price_increment`, amount, commission, dividend.
  Exact equality is required for reconciliation/idempotency. Convert to `Decimal` for research math only.
  - Rule: `units` and `nano` share sign; `-999_999_999 ≤ nano ≤ 999_999_999`.
- **Timestamps = INTEGER epoch milliseconds UTC.** Never TEXT/native datetime. Display tz only at the edges.
- **`instrument_uid` = TEXT**, the primary identifier (not FIGI).
- **Booleans = INTEGER `0|1`** (SQLite has no bool; Postgres maps to BOOLEAN later).
- **JSON = TEXT** holding JSON (Postgres → JSONB later).
- **Provenance:** data tables (`instrument_reference`, `candles`, `dividends`, `fills`) carry `source`,
  `source_version`, `as_of` (epoch-ms). New loads = a **new `source_version`** (no silent overwrite).
- **Postgres-compat (M6):** INTEGER→BIGINT for epoch-ms/units, `INTEGER PRIMARY KEY`→identity, TEXT-JSON→JSONB.

## 2. Frozen enum vocabularies (must match config + frozen-decisions exactly)
| Field | Allowed values |
| --- | --- |
| `instrument_reference.instrument_kind` | `share`, `index` |
| `instrument_reference.whitelist_status` | `approved`, `managed_only`, `watch_only`, `blocked`, `pending` (NULL for indices) |
| `instrument_reference.data_status` | `ok`, `data_conflict` |
| `signals.decision` | `candidate`, `selected`, `skipped`, `risk_rejected` |
| `signals.reason` (skip codes) | `lot_too_expensive`, `low_liquidity`, `wide_spread`, `not_trading`, `data_missing`, `data_conflict` (frozen-decisions.md, "Strategy, data & backtest honesty" (per-cycle eligibility row)) |
| `proposals.state` | `awaiting_confirmation`, `confirmed`, `rejected`, `expired` |
| `orders.side` | `buy`, `sell` |
| `orders.type` | `LIMIT` (only — market/bestprice are hard-rejected upstream, not storable) |
| `orders.state` | `submitted`, `partially_filled`, `filled`, `cancel_requested`, `cancelled`, `reconcile_required` |
| `positions.source` | `bot`, `manual_adopted`, `managed_only` |
| `positions.state` | `open`, `closed` |
| `positions.close_reason` | `risk`, `trend`, `target_trailing`, `time`, `manual` |
| `cash_events.type` | `deposit`, `withdrawal`, `commission`, `tax`, `dividend` |
| `reconciliations.result` | `clean`, `mismatch`, `blocked` |
| `control_state.mode` | `running`, `paused`, `killed`, `blocked_reconciliation_mismatch` |

## 3. DDL

```sql
PRAGMA foreign_keys = ON;

-- 3.1 Reference data --------------------------------------------------------
CREATE TABLE instrument_reference (
  instrument_uid            TEXT PRIMARY KEY,                    -- T-Invest uid (not FIGI)
  ticker                    TEXT NOT NULL,
  instrument_kind           TEXT NOT NULL CHECK (instrument_kind IN ('share','index')),
  is_tradable               INTEGER NOT NULL CHECK (is_tradable IN (0,1)),  -- indices = 0
  lot                       INTEGER,                             -- shares/lot (NULL for index)
  min_price_increment_units INTEGER,
  min_price_increment_nano  INTEGER CHECK (min_price_increment_nano IS NULL
                              OR min_price_increment_nano BETWEEN -999999999 AND 999999999),
  currency                  TEXT,
  trading_status            TEXT,                                -- last SecurityTradingStatus
  whitelist_status          TEXT CHECK (whitelist_status IN
                              ('approved','managed_only','watch_only','blocked','pending')),
  first_1day_candle_date    INTEGER,                             -- epoch-ms UTC; true history depth
  avg_turnover_rub          INTEGER,                             -- liquidity stat (rounded ₽)
  spread_bps                INTEGER,                             -- for 0.50% filter + ranking
  data_status               TEXT NOT NULL DEFAULT 'ok'
                              CHECK (data_status IN ('ok','data_conflict')),
  identifier_history        TEXT,                                -- JSON [{ticker,uid,from,to}] e.g. TCSG->T
  source                    TEXT NOT NULL,
  source_version            INTEGER NOT NULL,
  as_of                     INTEGER NOT NULL,
  -- indices: non-tradable, no whitelist; shares: tradable flag + whitelist vocabulary
  CHECK ((instrument_kind = 'index' AND is_tradable = 0 AND whitelist_status IS NULL)
      OR (instrument_kind = 'share' AND whitelist_status IS NOT NULL))
);
-- IMOEX (price) and MCFTR (total-return) live here as instrument_kind='index'
-- (is_tradable=0, whitelist_status NULL) so benchmarks + market-regime have a home.
-- Resolved (research whq6u1gxe): index candles come from MOEX ISS -> candles.source='moex_iss'
-- (engine=stock, market=index, interval=24). T-Invest exposes index LAST PRICE only.
-- Benchmark resolution: config `benchmarks` symbols resolve to instrument_reference rows by TICKER with
-- instrument_kind='index' (index_source secids = IMOEX, MCFTR); 'cash' and 'equal_weight' are SYNTHETIC
-- benchmarks with no DB row.

CREATE TABLE candles (
  instrument_uid TEXT NOT NULL REFERENCES instrument_reference(instrument_uid),
  interval       TEXT NOT NULL CHECK (interval IN ('1day')),     -- MVP = D1 only
  ts             INTEGER NOT NULL,                               -- epoch-ms UTC, canonical bar timestamp
  source_version INTEGER NOT NULL,
  open_units  INTEGER NOT NULL, open_nano  INTEGER NOT NULL CHECK (open_nano  BETWEEN -999999999 AND 999999999),
  high_units  INTEGER NOT NULL, high_nano  INTEGER NOT NULL CHECK (high_nano  BETWEEN -999999999 AND 999999999),
  low_units   INTEGER NOT NULL, low_nano   INTEGER NOT NULL CHECK (low_nano   BETWEEN -999999999 AND 999999999),
  close_units INTEGER NOT NULL, close_nano INTEGER NOT NULL CHECK (close_nano BETWEEN -999999999 AND 999999999),
  volume      INTEGER NOT NULL,                                  -- integer shares/lots
  is_complete INTEGER NOT NULL CHECK (is_complete IN (0,1)),     -- =1 ONLY when the bar reflects close_definition's FINAL close (see §4) — no-lookahead gate
  is_stale    INTEGER NOT NULL DEFAULT 0 CHECK (is_stale IN (0,1)),
  adjusted    INTEGER NOT NULL DEFAULT 0 CHECK (adjusted IN (0,1)),  -- split-adjusted
  source      TEXT NOT NULL,                                     -- 'tinvest' | 'moex_iss'
  as_of       INTEGER NOT NULL,
  PRIMARY KEY (instrument_uid, interval, ts, source_version)     -- new load = new version, no overwrite
);

CREATE TABLE dividends (
  instrument_uid TEXT NOT NULL REFERENCES instrument_reference(instrument_uid),
  -- T-Invest GetDividends has NO ex_dividend_date: derive ex_date = last_buy_date + 1 trading day (T+).
  last_buy_date  INTEGER NOT NULL,                               -- epoch-ms UTC (last day to buy WITH the dividend)
  ex_date        INTEGER,                                        -- epoch-ms UTC, DERIVED (last_buy_date + 1 trading day)
  record_date    INTEGER,                                        -- epoch-ms UTC (GetDividends 'to' filters on this)
  payment_date   INTEGER,                                        -- epoch-ms UTC
  declared_date  INTEGER,                                        -- epoch-ms UTC
  gross_units    INTEGER NOT NULL,                               -- GROSS per-share (API field 'dividend_net' is mislabeled GROSS)
  gross_nano     INTEGER NOT NULL CHECK (gross_nano BETWEEN -999999999 AND 999999999),
  dividend_type  TEXT,                                           -- GetDividends dividend_type (ordinary/special/interim — affects gap block + total-return)
  close_price_units INTEGER,                                     -- GetDividends close_price (API reference close), optional
  close_price_nano  INTEGER CHECK (close_price_nano IS NULL OR close_price_nano BETWEEN -999999999 AND 999999999),
  currency       TEXT NOT NULL,
  source         TEXT NOT NULL,                                  -- 'tinvest' (GetDividends); splits/renames -> 'moex_iss'
  source_version INTEGER NOT NULL,
  as_of          INTEGER NOT NULL,
  PRIMARY KEY (instrument_uid, last_buy_date, source_version)
);

CREATE TABLE data_conflicts (
  id             INTEGER PRIMARY KEY,
  instrument_uid TEXT NOT NULL REFERENCES instrument_reference(instrument_uid),
  ts             INTEGER NOT NULL,
  kind           TEXT NOT NULL CHECK (kind IN ('close_divergence','missing_bar','duplicate_bar')),
  detail         TEXT,                                           -- JSON {tinvest, iss, divergence_pct}
  resolved       INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0,1)),
  as_of          INTEGER NOT NULL
);

-- 3.2 Strategy / decision flow ---------------------------------------------
CREATE TABLE signals (
  id             INTEGER PRIMARY KEY,
  instrument_uid TEXT NOT NULL REFERENCES instrument_reference(instrument_uid),
  ts             INTEGER NOT NULL,                               -- epoch-ms UTC: the acted-on D1 close
  features       TEXT,                                           -- JSON snapshot (MA20/MA50, pullback, r/r, spread)
  decision       TEXT NOT NULL CHECK (decision IN
                   ('candidate','selected','skipped','risk_rejected')),
  reason         TEXT,                                           -- skip/reject code: lot_too_expensive|low_liquidity|wide_spread|not_trading|data_missing|data_conflict (frozen vocab §2)
  created_at     INTEGER NOT NULL
);
-- Risk rejection is recorded HERE only (decision='risk_rejected', no order row).
-- decision='selected' -> exactly one proposal is created.

CREATE TABLE proposals (
  proposal_id      TEXT PRIMARY KEY,                             -- uuid
  signal_id        INTEGER NOT NULL REFERENCES signals(id),
  created_at       INTEGER NOT NULL,
  ttl_ms           INTEGER NOT NULL,                             -- wall-clock TTL
  telegram_user_id INTEGER NOT NULL,                            -- whitelisted user bound to this proposal
  state            TEXT NOT NULL CHECK (state IN
                     ('awaiting_confirmation','confirmed','rejected','expired'))
);

CREATE TABLE orders (
  order_id        TEXT PRIMARY KEY,                              -- CLIENT idempotency key (every PostOrder)
  proposal_id     TEXT REFERENCES proposals(proposal_id),       -- NULL for protective exits
  instrument_uid  TEXT NOT NULL REFERENCES instrument_reference(instrument_uid),
  account_id      TEXT NOT NULL,                                 -- guarded bot account; assert == config.account_id at submit AND reconcile [LAW]
  side            TEXT NOT NULL CHECK (side IN ('buy','sell')),
  type            TEXT NOT NULL CHECK (type = 'LIMIT'),         -- LIMIT only [LAW]
  price_units     INTEGER NOT NULL,
  price_nano      INTEGER NOT NULL CHECK (price_nano BETWEEN -999999999 AND 999999999),
  lots            INTEGER NOT NULL CHECK (lots > 0),
  state           TEXT NOT NULL CHECK (state IN
                    ('submitted','partially_filled','filled','cancel_requested','cancelled','reconcile_required')),
  broker_order_id TEXT,
  attempts        INTEGER NOT NULL DEFAULT 0,
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL
);
-- long-only / no-shorts enforced in the risk engine (sell <= held qty); never stored as a short.

CREATE TABLE fills (
  id               INTEGER PRIMARY KEY,
  order_id         TEXT NOT NULL REFERENCES orders(order_id),
  ts               INTEGER NOT NULL,
  price_units      INTEGER NOT NULL,
  price_nano       INTEGER NOT NULL CHECK (price_nano BETWEEN -999999999 AND 999999999),
  qty              INTEGER NOT NULL CHECK (qty > 0),
  commission_units INTEGER NOT NULL,
  commission_nano  INTEGER NOT NULL CHECK (commission_nano BETWEEN -999999999 AND 999999999),
  source           TEXT NOT NULL,
  as_of            INTEGER NOT NULL
);

CREATE TABLE positions (
  id              INTEGER PRIMARY KEY,
  instrument_uid  TEXT NOT NULL REFERENCES instrument_reference(instrument_uid),
  account_id      TEXT NOT NULL,                                 -- must equal the guarded bot account
  qty             INTEGER NOT NULL,
  avg_price_units INTEGER NOT NULL,
  avg_price_nano  INTEGER NOT NULL CHECK (avg_price_nano BETWEEN -999999999 AND 999999999),
  opened_at       INTEGER NOT NULL,
  closed_at       INTEGER,
  source          TEXT NOT NULL CHECK (source IN ('bot','manual_adopted','managed_only')),
  exit_rules      TEXT,                                          -- JSON {hard_stop, take_profit, trail, max_holding_days}
  state           TEXT NOT NULL CHECK (state IN ('open','closed')),
  close_reason    TEXT CHECK (close_reason IN ('risk','trend','target_trailing','time','manual')),
  CHECK ((state = 'open'  AND closed_at IS NULL AND close_reason IS NULL)
      OR (state = 'closed' AND closed_at IS NOT NULL AND close_reason IS NOT NULL))
);

-- 3.3 Cash, reconciliation, audit -----------------------------------------
CREATE TABLE cash_events (
  id              INTEGER PRIMARY KEY,
  account_id      TEXT NOT NULL,                                 -- guarded bot account (cash is per-account) [LAW]
  ts              INTEGER NOT NULL,
  type            TEXT NOT NULL CHECK (type IN
                    ('deposit','withdrawal','commission','tax','dividend')),
  amount_units    INTEGER NOT NULL,
  amount_nano     INTEGER NOT NULL CHECK (amount_nano BETWEEN -999999999 AND 999999999),
  currency        TEXT NOT NULL,
  ref_order_id    TEXT REFERENCES orders(order_id),
  ref_position_id INTEGER REFERENCES positions(id),
  note            TEXT,
  as_of           INTEGER NOT NULL
);  -- drives recomputation of capital/limits

CREATE TABLE reconciliations (
  id       INTEGER PRIMARY KEY,
  ts       INTEGER NOT NULL,
  kind     TEXT NOT NULL,                                        -- startup|periodic|post_restart
  result   TEXT NOT NULL CHECK (result IN ('clean','mismatch','blocked')),
  mismatch TEXT,                                                 -- JSON detail
  as_of    INTEGER NOT NULL
);

CREATE TABLE control_state (
  id         INTEGER PRIMARY KEY CHECK (id = 1),                 -- singleton row
  mode       TEXT NOT NULL CHECK (mode IN
               ('running','paused','killed','blocked_reconciliation_mismatch')),
  since_ts   INTEGER NOT NULL,
  reason     TEXT,
  updated_at INTEGER NOT NULL
);  -- persists pause/kill/blocked across restarts so kill/pause + the post-restart
    -- reconciliation gate survive a restart (TZ §7-§8); read on startup before any action.

CREATE TABLE audit_journal (
  id          INTEGER PRIMARY KEY,                               -- append-only (see triggers)
  ts          INTEGER NOT NULL,
  account_id  TEXT,                                              -- guarded bot account (NULL for global events: pause/kill)
  event       TEXT NOT NULL,                                     -- signal_selected|proposal_created|confirm_received|
                                                                 -- order_submitted|fill|position_opened|exit|
                                                                 -- pause|resume|kill|reconciliation|...
  proposal_id TEXT REFERENCES proposals(proposal_id),
  order_id    TEXT REFERENCES orders(order_id),
  position_id INTEGER REFERENCES positions(id),
  actor       TEXT,                                              -- 'system' | 'owner:<telegram_user_id>'
  detail      TEXT                                               -- JSON
);
-- Append-only: block UPDATE/DELETE so the audit trail is tamper-evident.
CREATE TRIGGER audit_journal_no_update BEFORE UPDATE ON audit_journal
  BEGIN SELECT RAISE(ABORT, 'audit_journal is append-only'); END;
CREATE TRIGGER audit_journal_no_delete BEFORE DELETE ON audit_journal
  BEGIN SELECT RAISE(ABORT, 'audit_journal is append-only'); END;

-- 3.4 Indexes (query paths) ------------------------------------------------
CREATE INDEX idx_candles_uid_ts        ON candles(instrument_uid, ts);
CREATE INDEX idx_signals_ts            ON signals(ts);
CREATE INDEX idx_signals_uid           ON signals(instrument_uid);
CREATE INDEX idx_orders_state          ON orders(state);
CREATE INDEX idx_orders_proposal       ON orders(proposal_id);
CREATE INDEX idx_positions_state       ON positions(state);
CREATE INDEX idx_positions_uid         ON positions(instrument_uid);
CREATE INDEX idx_dividends_uid_lastbuy ON dividends(instrument_uid, last_buy_date);
CREATE INDEX idx_audit_ts              ON audit_journal(ts);
CREATE INDEX idx_cash_events_ts        ON cash_events(ts);
```

## 4. Invariants encoded by the schema
- **No float money** — every monetary value is a `units`/`nano` integer pair.
- **No-lookahead** — `candles.is_complete=1` only once the bar reflects the **final** close per
  `config.close_definition`: for `auction_close`, the auction close from `GetClosePrices`/`OrderBook.close_price`
  captured at/after 18:50 (19:00 from 2026-03-23); for `d1_candle_after_evening`, the GetCandles D1 close re-read
  after ~23:50. The data layer asserts the close source matches `close_definition` before setting `is_complete=1`;
  the signal `ts` is that final closed D1. (Until the "does evening print into the D1 close" check passes, prefer `auction_close`.)
- **Idempotency** — `orders.order_id` is the client key and the PK (a retry/restart cannot create a duplicate).
- **Managed registry** — `whitelist_status` CHECK exactly matches the frozen vocabulary; indices carry NULL.
- **Limit-only** — `orders.type` CHECK admits only `LIMIT`; market/bestprice can't be persisted.
- **Audit trail** — `audit_journal` is append-only (triggers) and FK-links proposal→order→position.
- **Data truth** — `data_status` + `data_conflicts` record divergence; the risk engine skips **entry** (never an exit) when `data_status='data_conflict'`.
- **State-machine parity** — `orders.state` / `positions.state` / `proposals.state` enums match TZ §8;
  `control_state.mode` persists `paused`/`killed`/`blocked_reconciliation_mismatch` so they survive a restart (TZ §7-§8).
- **Account guard (row-level)** — `orders.account_id` and `cash_events.account_id` (NOT NULL) and
  `audit_journal.account_id` carry the guarded account so the order/cash/audit trail is provably scoped; the
  engine asserts `== config.account_id` at submit **and** at reconciliation [LAW].

## 5. Resolved by research `whq6u1gxe` + remaining empirical checks
**Resolved (folded in above):**
- Index candle source = **MOEX ISS** (`candles.source='moex_iss'`; engine=stock, market=index, interval=24).
- Dividends from T-Invest `GetDividends`; **`dividend_net` is GROSS**; **no ex_dividend_date** → `ex_date`
  derived = `last_buy_date` + 1 trading day; the `to` request param filters on `record_date`.
- Splits / corp-actions / renames = **MOEX ISS** (no T-Invest API): `/iss/statistics/engines/stock/splits`,
  `/iss/cci/corp-actions`, `…/securities/changeover` (TCSG→T confirmed 2024-11-27); stitch on **ISIN**.
- D1 close = main-session **auction** close (recommended `close_definition=auction_close` via GetClosePrices)
  — pins the `candles.ts`/close convention once the owner ratifies it.

**Remaining empirical (M1/M4 — do NOT block the M0 contract):**
- Are T-Invest D1 **share** candles already split-adjusted? Verify on a known split before backtest use.
- Does the evening session print into the T-Invest GetCandles D1 `close`? Empirical (snapshot 19:00 vs 23:50).
- Does a rename change the T-Invest `instrument_uid`? Confirm ISIN is the stable join key at integration.
- ISS corp-action/split endpoint coverage + anonymous availability per ticker.

## 6. Cross-references
- Spec `docs/TZ.md` §5, §5.1, §8. Frozen LAW `docs/frozen-decisions.md` (money/no-float, managed registry,
  state machine, data truth, idempotency). Config [config-and-secrets.md](config-and-secrets.md);
  taxes [tax-and-dividends.md](tax-and-dividends.md). Skills: `state-machine-discipline`, `broker-api-contract`.
