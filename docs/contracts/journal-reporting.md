# Contract — Journal & reporting (TZ §12)

> **Status:** M0 contract, **resolved on paper (no code yet)**. Pins the irreversible *shape* of the
> append-only audit export, the two-layer-PnL derivation, the daily-status / weekly-report field sets, and the
> monthly whitelist-review job. **`docs/frozen-decisions.md` 🔒 wins** on any conflict; values marked **[LAW]**
> mirror a frozen invariant and may not be changed here (only via owner decision + ADR + same-change rule).
> **[owner-pending]** = a value the owner must confirm before it is locked (do not silently fix it).
> **[verify]** = depends on a fact still being confirmed by research `whq6u1gxe` / §20.
>
> Reuses the **frozen vocabulary verbatim** from [db-schema.md](db-schema.md) (table + column + enum names),
> [config-and-secrets.md](config-and-secrets.md) (config keys), and the two-layer-PnL rules from
> [tax-and-dividends.md](tax-and-dividends.md). Never rename a shared identifier here.

---

## 1. Scope & non-negotiable rules (TZ §12)

- **Read, never write the trading path.** The journal/reporting layer is a **read + derive + render** layer over
  the M0 schema. It MUST NOT create, mutate, or cancel proposals/orders/positions and MUST NOT decide buy/sell
  (LLM/Codex/Claude never trade — frozen scope). The one exception with a *write* effect is the monthly
  whitelist-review job (§8), which may set `whitelist_status='pending'` and emit Telegram proposals — and
  **never** auto-adds to `approved` [LAW].
- **`audit_journal` is append-only** [LAW]: the export reads it; it never UPDATEs/DELETEs (the schema triggers
  `audit_journal_no_update` / `audit_journal_no_delete` abort either). Reports are derived artifacts — recomputing
  a report never edits source rows.
- **Secrets never surface in any output** [LAW: token policy]. No report, export, daily status, weekly report, or
  Telegram message may contain a token, `DASHBOARD_AUTH_TOKEN`, raw `account_id` beyond the masked form (§6), or
  any value caught by the §5 secret-scan shapes in [config-and-secrets.md](config-and-secrets.md). Log/echo
  token presence only as a boolean (`token_loaded=true`).
- **Two-layer PnL everywhere** [LAW: backtest-honesty]: every PnL number in every surface is labeled **Layer A**
  (economic, pre-cost/pre-tax) or **Layer B** (broker/tax realized). The honest verdict always reads **Layer B**;
  Layer A is diagnostic only (TZ §12, [tax-and-dividends.md](tax-and-dividends.md) §1).
- **No money as float** [LAW]: all monetary fields are derived from Quotation `*_units`/`*_nano` integer pairs
  (db-schema §1) and rendered to display strings only at the edge. `Decimal` for intermediate math, never `float`.
- **No-lookahead in derivation** [LAW]: any per-day report aggregates only events whose `ts` ≤ the report's
  as-of boundary; PnL marks for *open* positions use the **final** D1 close per `config.close_definition`
  (db-schema §4) — never an intraday/partial mark that the model could not yet have known.

## 2. Audit-journal export schema (TZ §12 "exportable")

The export is a **faithful projection of the `audit_journal` table** — same column names, same order, no
renames, append-only source (db-schema §3.3). It joins the FK-linked rows so an auditor can replay
proposal → confirm → order → fill → position → exit without the live DB.

### 2.1 Exported columns (verbatim from `audit_journal`)

```
id            INTEGER   -- audit_journal.id (monotonic, append-only PK)
ts            INTEGER   -- epoch-ms UTC (db-schema §1 timestamp rule)
account_id    TEXT      -- guarded bot account; NULL for global events (pause/kill). MASKED in human exports (§6)
event         TEXT      -- event vocabulary, §2.2
proposal_id   TEXT      -- FK proposals.proposal_id  (NULL if N/A)
order_id      TEXT      -- FK orders.order_id        (NULL if N/A)
position_id   INTEGER   -- FK positions.id           (NULL if N/A)
actor         TEXT      -- 'system' | 'owner:<telegram_user_id>'   (db-schema §3.3)
detail        TEXT      -- JSON payload (event-specific)
```

### 2.2 `event` vocabulary (frozen — extends the db-schema §3.3 comment list)

The db-schema comment is illustrative (`signal_selected|proposal_created|confirm_received|order_submitted|fill|
position_opened|exit|pause|resume|kill|reconciliation|...`). This contract **pins the closed set** the writer
emits and the export validates against. Adding a value is a frozen change (same-change rule).

```
-- decision flow (mirror state-machine transitions, frozen-decisions.md, "Strategy, data & backtest honesty" (state-machine row))
signal_selected          -- signals.decision='selected'
proposal_created         -- proposals.state='awaiting_confirmation'
confirm_received         -- proposals.state='confirmed'  (actor = owner:<telegram_user_id>)
proposal_rejected        -- proposals.state='rejected'
proposal_expired         -- proposals.state='expired'    (TTL lapse)
order_submitted          -- orders.state='submitted'
order_partially_filled   -- orders.state='partially_filled'
fill                     -- a fills row (entry or exit)
order_cancel_requested   -- orders.state='cancel_requested' (TTL / one-attempt rule)
order_cancelled          -- orders.state='cancelled'
position_opened          -- positions.state='open'
exit                     -- positions.state='closed' (detail.close_reason ∈ db-schema positions.close_reason)
-- control plane (account_id NULL — global)
pause                    -- control_state.mode='paused'
resume                   -- control_state.mode='running' (after extra confirm + preflight)
kill                     -- control_state.mode='killed'   (cancels orders, NEVER sells [LAW])
-- safety / data
reconciliation           -- a reconciliations row (detail.result ∈ clean|mismatch|blocked)
reconcile_blocked        -- control_state.mode='blocked_reconciliation_mismatch'
data_conflict            -- a data_conflicts row (entry skipped, never an exit)
risk_rejected            -- signals.decision='risk_rejected' (no order row)
whitelist_review         -- monthly job ran (§8); detail lists proposals emitted
unauthorized_telegram    -- non-whitelisted telegram_user_id ignored+logged (config §2.2 [LAW])
```

> `close_reason` inside `exit.detail` reuses the frozen `positions.close_reason` enum verbatim:
> `risk | trend | target_trailing | time | manual` (db-schema §2). Skip codes inside `signal_*`/`risk_rejected`
> `detail` reuse the frozen `signals.reason` enum: `lot_too_expensive | low_liquidity | wide_spread |
> not_trading | data_missing | data_conflict` (db-schema §2, frozen-decisions.md, "Strategy, data & backtest honesty" (per-cycle eligibility row)).

### 2.3 Export format & integrity

| Aspect | Rule |
| --- | --- |
| Formats | **JSONL** (one `audit_journal` row per line, canonical) **and** CSV (same columns, same order). |
| Ordering | Strict ascending `(ts, id)`; `id` is the tie-breaker (append-only monotonic PK). |
| Range filter | `[from_ts, to_ts]` epoch-ms inclusive; default = full table. |
| Timestamps | Exported as epoch-ms UTC (raw) **plus** an ISO-8601 `Europe/Moscow` rendering (`config.timezone`) for humans; the epoch-ms value is canonical. |
| Tamper-evidence | Export records `row_count` and the `(min_id, max_id, max_ts)` covered; a gap in `id` is flagged (append-only ⇒ no deletes, so ids are contiguous). [verify] whether to add a rolling SHA-256 chain over rows — pinned later, not M0-blocking. |
| Secrets | `account_id` masked (§6); `detail` JSON is scrubbed against the §5 secret-scan shapes before write. |

## 3. Two-layer-PnL derivation (TZ §12, [tax-and-dividends.md](tax-and-dividends.md) §1)

PnL is **derived** from `fills`, `positions`, `cash_events`, and `dividends` — never stored as a float, never
trusted from a single broker field. Both layers are computed per **closed trade** (FIFO, db-schema + tax §2)
and aggregated to day / week / since-inception.

### 3.1 Source tables (read-only)

| Source | Used for |
| --- | --- |
| `fills` (price + `commission_units/nano`, db-schema §3.2) | actual fill price + actual commission (LIVE Layer B). |
| `positions` (`avg_price`, `qty`, `close_reason`, FIFO lots) | open marks (Layer A/B unrealized) + realized per closed lot. |
| `cash_events` (`type ∈ deposit|withdrawal|commission|tax|dividend`) | capital recomputation; tax + dividend cashflows. |
| `dividends` (`gross_units/nano`) | dividend accrual (NET in Layer B; tax §5). |

### 3.2 Layer A — economic strategy PnL (pre-cost, pre-tax)

```
realized_A(trade)   = Σ_lots (sell_price − buy_price) × qty          -- price only, FIFO, NO commission/tax/slippage
unrealized_A(pos)   = (mark_close − avg_price) × qty                 -- mark = final D1 close per close_definition
```

Layer A answers "did the *rule* make money on price?". It is **diagnostic** — never the verdict.

### 3.3 Layer B — broker/tax realized PnL (the honest verdict)

```
realized_B(trade)   = realized_A(trade)
                      − buy_commission − sell_commission             -- both sides (frozen: costs both sides)
                      − ndfl_accrued(trade)                          -- 13% × commission-inclusive FIFO base (NOT gross price gain); accrued AT realization, pilot (tax §2,§8)
                      + net_dividends_in_holding                     -- NET (taxed at source, separate base; tax §5)
                      − slippage_buffer(trade)   [BACKTEST ONLY]     -- config costs.slippage_bps; LIVE: already in fill price → OMIT
```

LIVE/LIVE-derived rules (must not double-count — tax §1):
- **LIVE Layer B** uses **actual** `fills.commission_*` and **actual** fill price; there is **NO separate
  slippage line** (slippage is already inside the fill price). Adding one is a defect.
- **BACKTEST Layer B** adds the modeled `costs.slippage_bps` buffer (config §2.8) on top of modeled commission.
- **НДФЛ base is commission-inclusive FIFO**, NOT the gross price gain: `ndfl_accrued(trade) = 13% ×
  (realized_A(trade) − buy_commission − sell_commission)` per closed FIFO lot — buy + sell commissions reduce the
  taxable base (tax §2; worked fixture tax §8). The 15% bracket is unreachable at the 10 000 ₽ pilot → flat 13%.
- **НДФЛ accrual** reduces Layer B **immediately at each realization** (not deferred to year-end withholding),
  so mid-period equity/Sharpe are not flattered (tax §1). Withholding timing is a documented cashflow caveat,
  not a PnL difference.
- **Tax = estimate.** The bot's НДФЛ number is an estimate; the **broker tax report is the legal source of
  truth** — reports state this and reconcile against it (tax §2). The bot figure is never labeled authoritative.
- **Dividends NET** in Layer B; the **MCFTR** benchmark is **GROSS** — reports state "strategy net vs MCFTR
  gross" (the asymmetry is intentional and documented; tax §5).
- **ЛДВ does not apply** (2–6 week horizon ≪ 3 years) — never modeled in Layer B (tax §3).

### 3.4 Equity curve & per-trade expectancy

- Two equity curves are maintained — `equity_A` and `equity_B` — both rebased to `risk.capital_rub` start.
- **Reports + the evidence gate read `equity_B`** (drawdown, expectancy, Sharpe) — the Layer-B (after-cost,
  after-tax-accrual) curve is the honest one (tax §1).
- Per-trade rows carry **both** `pnl_A` and `pnl_B` (frozen: both logged per trade).

## 4. Daily status (TZ §12)

A read-only snapshot rendered to the dashboard, the structured log, and an optional Telegram digest. **No
secrets** (§1). One row per trading day, as-of the day's **final** close (`close_definition`, no-lookahead §1).

### 4.1 Fields (frozen set — TZ §12 line 237)

```
date                  -- YYYY-MM-DD Europe/Moscow (config.timezone)
mode                  -- config.mode: paper|sandbox|confirm   (NEVER auto)
control_mode          -- control_state.mode: running|paused|killed|blocked_reconciliation_mismatch
account_masked        -- masked account_id (§6); name shown, id masked
cash_rub              -- free cash (from cash_events recomputation)
positions             -- list[{ticker, qty, avg_price, mark_close, unrealized_A, unrealized_B, exit_rules}]
daily_pnl_A           -- Layer A realized+unrealized delta for the day
daily_pnl_B           -- Layer B realized+unrealized delta for the day  (the honest number)
total_pnl_A           -- since-inception Layer A
total_pnl_B           -- since-inception Layer B
signals               -- counts by signals.decision: candidate|selected|skipped|risk_rejected
trades                -- orders/fills today (entries + exits), with order_id + state
skip_reasons          -- counts by signals.reason: lot_too_expensive|low_liquidity|wide_spread|
                      --                           not_trading|data_missing|data_conflict
api_data_errors       -- counts: rate-limit/backoff, data_conflict, reconciliation mismatch, token-scope block
```

- `positions` honors **max 1 open position** in the pilot (config `risk.max_open_positions=1` [LAW]) — the field
  is a list for forward-compat but the pilot renders ≤1.
- `skip_reasons` and `signals` reuse the frozen enums verbatim (db-schema §2) — no display-only relabeling that
  diverges from the stored code.
- The daily-status job runs **after** `daily_run_time` (config §2.9), which the loader binds to ≥ the final
  close so the snapshot is lookahead-safe (config §3.1 [LAW]).

## 5. Weekly report (TZ §12)

A read-only digest over the trailing week + since-inception. Same secret/no-lookahead/two-layer rules (§1).
**Verdict reads Layer B.** A weekly report is **not** a profitability proof (frozen: never trust a single
sandbox/backtest run — `backtest-honesty`); the verdict is descriptive, not a go-live signal.

### 5.1 Fields (frozen set — TZ §12 lines 238–239)

```
period                  -- ISO week range, Europe/Moscow
weekly_return_A / _B    -- Layer A / Layer B return over the week (B = verdict)
inception_return_A / _B -- since-inception Layer A / Layer B return
trades                  -- count + list (entry/exit, ticker, pnl_A, pnl_B, close_reason)
open_positions          -- end-of-week snapshot (ticker, qty, unrealized_A/_B, exit_rules)
commissions_rub         -- Σ cash_events(type='commission')  (both sides)
taxes_rub               -- Σ НДФЛ accrued (cash_events(type='tax')) — ESTIMATE; broker report is source of truth (tax §2)
dividends_net_rub       -- Σ NET dividends (cash_events(type='dividend')); note "net vs MCFTR gross" (tax §5)
skipped_candidates      -- by signals.reason (frozen skip vocab)
risk_filters            -- which risk gates fired: daily_hard_stop, market_regime (index<MA50 / 5d<−5%),
                        --   reentry_cooldown, dividend_gap_block, eligibility(lot/spread/turnover/days)
errors                  -- API/data/reconciliation errors for the week
verdict                 -- SHORT free-text, derived from Layer B; explicitly NOT a profitability proof
benchmarks              -- weekly + inception vs IMOEX (price), MCFTR (gross TR), cash, equal_weight (config §2.9)
```

- `risk_filters` names mirror the frozen config keys [LAW], each from its own section so a filter that fires maps
  1:1 to its frozen knob: `daily_hard_stop_rub`, `market_regime_index_ma`, `market_regime_5d_floor_pct`,
  `reentry_cooldown_days` are `risk.*` (config §2.5); `dividend_gap_block_days` is config §2.9 (data/benchmarks/
  schedule); `eligibility.*` (lot/spread/turnover/days) is config §2.4 (per-cycle eligibility filters).
- `benchmarks` reuse `config.benchmarks` verbatim: `IMOEX`, `MCFTR`, `cash`, `equal_weight` (config §2.9); the
  MCFTR comparison is **gross vs strategy-net** and says so (tax §5).

## 6. Account-id & secret masking in all outputs [LAW: token policy + account guard]

- `account_id` is rendered **masked** in every human-facing surface (export, daily status, weekly report,
  Telegram, dashboard): show the broker **account name** + a masked id (e.g. last-4 only) — the full id is
  internal/state only. The on-startup full name+id display (config §3) is the *one* human-verification surface;
  reports do not re-echo the full id.
- No token, `DASHBOARD_AUTH_TOKEN`, Telegram bot token, or any `*_TOKEN` / `*SECRET*` / `*API_KEY` value ever
  appears — `detail`/free-text is scrubbed against the §5 secret-scan shapes in
  [config-and-secrets.md](config-and-secrets.md) before render/export.
- Telegram: only **whitelisted** `telegram_user_id`s receive reports/proposals; others are ignored + logged as
  `unauthorized_telegram` (config §2.2 [LAW]).

## 7. Scheduling & delivery (TZ §12, §17)

| Job | When | Source of timing | Output |
| --- | --- | --- | --- |
| Daily status | after `daily_run_time` (≥ final close) | config §2.9 / §3.1 [LAW: no-lookahead] | dashboard + log + optional Telegram digest |
| Weekly report | end of trading week (day **[owner-pending]**) | scheduler | dashboard + log + Telegram |
| Monthly whitelist-review | monthly (day/time **[owner-pending]**) | scheduler (§8) | Telegram **proposals** for owner confirm |
| Audit export | on demand / pre-PR / archival | manual or scheduled | JSONL + CSV (§2) |

- All schedule times are **MSK** (`config.timezone=Europe/Moscow`) and rendered display-only; storage stays
  epoch-ms UTC.
- The weekly day and the monthly run day/time are **[owner-pending]** (not asserted here — guessing a schedule
  is not a frozen value but is still an owner choice; surfaced in §10).

## 8. Monthly whitelist-review job (TZ §12, frozen-decisions.md, "Strategy, data & backtest honesty" (managed registry row)) [LAW: managed registry]

A scheduler job that **re-evaluates** the current `approved` + `watch_only` instruments and **proposes**
replacements to the owner — it can never grow the trading universe on its own.

### 8.1 Inputs (read-only)

- Current `instrument_reference.whitelist_status` rows (`approved`, `watch_only`) + their liquidity stats
  (`avg_turnover_rub`, `spread_bps`, `lot`, `is_tradable`, `trading_status`, `first_1day_candle_date`).
- The frozen eligibility thresholds (config §2.4 [LAW]): `eligibility.max_lot_value_pct` (30),
  `eligibility.max_spread_bps` (50), `eligibility.min_turnover_rub` (50M), `eligibility.min_trading_days` (40).
- Recent signal-quality stats from `signals` (e.g. how often this ticker reached `selected` vs `skipped`).

### 8.2 Evaluation (re-evaluate `approved` + `watch_only` on liquidity/lot/spread/availability/signal-quality)

For each `approved`/`watch_only` ticker, compute pass/fail against the frozen eligibility thresholds plus
availability (`is_tradable`, not `blocked`, `data_status='ok'`) and signal-quality. The job classifies each as
keep / demote-candidate / replace-candidate. The **same** skip vocabulary is reused for the reason codes:
`lot_too_expensive | low_liquidity | wide_spread | not_trading | data_missing | data_conflict` (db-schema §2).

### 8.3 Actions — bounded by the managed-registry LAW

```
ALLOWED (bot may do automatically):
  - set whitelist_status = 'pending' on a candidate ticker the job wants to propose adding
    (config universe.pending; "bot sets pending, never auto-approved" — config §2.3)
  - emit a REPLACEMENT PROPOSAL to Telegram (whitelisted owner) describing keep/demote/add with evidence
  - write an audit_journal row event='whitelist_review' (detail = the proposals emitted)

FORBIDDEN (LAW — never automatic):
  - set whitelist_status = 'approved'        -- owner-only, via confirm  [LAW frozen-decisions.md, "Strategy, data & backtest honesty" (managed registry row)]
  - add a ticker to the trading universe      -- bot may NOT grow the universe itself [LAW: managed registry row, same table]
  - move 'approved' -> 'blocked' / remove     -- a demotion is a PROPOSAL, not an auto-action
  - place/cancel/modify any order             -- not a trading-path actor (§1)
```

- The owner confirms a proposal **in Telegram** (same confirm-first control plane as entries); only an owner
  confirm transitions a `pending` candidate to `approved`. A rejected/expired proposal leaves the registry
  unchanged.
- Each emitted proposal is auditable: an `audit_journal` row (`event='whitelist_review'`, `actor='system'`) and,
  on owner action, a follow-up row (`actor='owner:<telegram_user_id>'`).
- **skip ≠ remove** parity: marking an `approved` ticker that currently fails eligibility does **not** demote it
  here — per-cycle eligibility failures are handled in the trading cycle as `skipped`
  (frozen-decisions.md, "Strategy, data & backtest honesty" (per-cycle eligibility row));
  the monthly job only *proposes* registry changes, it does not silently remove (config §2.4 [LAW]).

## 9. Frozen invariants honored

- **Append-only audit trail** [LAW]: export is a read-only projection of `audit_journal`; never UPDATE/DELETE
  (schema triggers enforce; export flags any `id` gap) — §2.
- **Managed-registry LAW** (frozen-decisions.md, "Strategy, data & backtest honesty" (managed registry row)): the monthly job re-evaluates `approved`+`watch_only`, may set
  `pending`, emits Telegram replacement **proposals** for owner confirm, and **never** auto-adds to `approved` /
  grows the universe — §8.
- **Two-layer PnL** [LAW: backtest-honesty]: every PnL labeled Layer A vs Layer B; verdict reads Layer B; LIVE
  omits the separate slippage line; НДФЛ accrued at realization; tax = estimate, broker report = truth — §3.
- **Secrets never echoed** [LAW: token policy]: no token / `DASHBOARD_AUTH_TOKEN` / full `account_id` in any
  report/export/Telegram/dashboard; `detail` scrubbed against the secret-scan shapes — §1, §6.
- **Account guard surfacing** [LAW]: `account_id` carried on audit/cash/order rows is exported masked; the
  startup full name+id display is the only human-verification surface — §6.
- **No-lookahead** [LAW]: per-day aggregation bounded by the as-of close; open-position marks use the final D1
  close per `close_definition`; the daily job runs after `daily_run_time` — §1, §4, §7.
- **No float money** [LAW]: all amounts derived from Quotation units/nano, `Decimal` math, display-only strings.
- **LLM never trades / kill never sells** [LAW]: the journal layer is read-only over the trading path; the only
  write effect (whitelist `pending` + proposal) is explicitly bounded; `kill`/`pause`/`resume` appear only as
  audit events, never as actions this layer initiates — §1, §2.2.
- **Enum/key parity**: `event`, `close_reason`, `signals.reason`, `whitelist_status`, `control_state.mode`,
  `benchmarks`, risk/eligibility keys reused **verbatim** from db-schema + config — §2, §4, §5, §8.

## 10. Open questions / owner-pending

- **Weekly report day** and **monthly whitelist-review run day/time** — `[owner-pending]` (scheduler config; not
  a frozen value but an owner choice). Bind to MSK; storage stays epoch-ms UTC (§7).
- **Audit-export tamper-evidence depth** — `[verify]` whether to add a rolling SHA-256 hash chain over exported
  rows beyond the append-only triggers + `id`-gap check (not M0-blocking) (§2.3).
- **Account-id mask format** — `[owner-pending]` exact masking (last-4 vs name-only) for human surfaces (§6);
  the LAW is "never echo the full id in reports", the display form is an owner choice.
- **НДФЛ rounding direction** for the `taxes_rub` line — inherits the `[verify]` from
  [tax-and-dividends.md](tax-and-dividends.md) §8 (RUB whole-ruble rounding, pinned in the M2 fixture); the
  report renders whatever the tax layer computes — it does not re-decide rounding.
- **Telegram digest opt-in** — `[owner-pending]` whether daily status is pushed to Telegram or dashboard-only;
  whitelist + no-secrets rules apply either way (§6).
- **Telegram whitelist ids** — `[owner-pending]` (config §2.2, needed by M5); reports/proposals go only to
  whitelisted ids.

## 11. Cross-references

- Spec: `docs/TZ.md` §12 (journal/reporting), §12.1 (tax), §13 (backtest costs/metrics), §17 (scheduling).
- Frozen LAW: `docs/frozen-decisions.md` — "Strategy, data & backtest honesty" (state-machine / audit-trail
  row; managed registry row; conservative both-side fills + costs both sides row; no-lookahead row), "Account &
  access" (token policy row), "Order & risk rules" (`kill` never sells — kill/pause row). Two-layer PnL is the
  backtest-honesty invariant in the same "Strategy, data & backtest honesty" table.
- Schema: [db-schema.md](db-schema.md) (`audit_journal`, `signals`, `proposals`, `orders`, `fills`, `positions`,
  `cash_events`, `dividends`, `reconciliations`, `control_state`; enum vocabularies §2).
- Config: [config-and-secrets.md](config-and-secrets.md) (`mode`, `account_id`, `benchmarks`, `risk.*`,
  `eligibility.*`, `universe.*`, `daily_run_time`, `close_definition`, `telegram_user_whitelist`).
- Taxes: [tax-and-dividends.md](tax-and-dividends.md) (two-layer PnL §1, НДФЛ §2, dividends net §5).
- Skills: `state-machine-discipline` (audit trail, transitions), `risk-policy-guardian` (managed registry,
  no-trade journal layer), `secrets-token-policy` (no secrets in output), `backtest-honesty` (two-layer PnL,
  not-a-proof verdict).
