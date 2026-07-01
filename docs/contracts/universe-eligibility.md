# Contract — Universe registry & eligibility filters (TZ §5, §6, §7.3)

> **Status:** M0/M1 contract, **resolved on paper (no code yet)**. This pins the **managed-registry**
> semantics and the **per-cycle eligibility filter** the universe/risk layers implement verbatim.
> **`docs/frozen-decisions.md` 🔒 wins** on any conflict — values marked **[LAW]** mirror a frozen
> invariant and may not be changed here (only via owner decision + ADR + same-change rule).
> **[owner-pending]** = a value the owner must confirm before it is locked (do not silently fix it).
> Enum vocabularies are **frozen** and must match [db-schema.md](db-schema.md) exactly — a divergent
> enum silently weakens an invariant. Pairs with [config-and-secrets.md](config-and-secrets.md) and
> [db-schema.md](db-schema.md).

This contract feeds: the `instrument_reference.whitelist_status` registry ([db-schema.md](db-schema.md)),
the eligibility-filter step of the risk engine (TZ §7.3), the `signals.decision='skipped'` path with its
skip-reason codes ([db-schema.md](db-schema.md) §2), the monthly whitelist-review job (TZ §12), and the
`universe.*` + `eligibility.*` config keys ([config-and-secrets.md](config-and-secrets.md) §2.3–§2.4).

---

## 1. Two distinct mechanisms — do not conflate (the central invariant)

The universe has **two layers** that must never be collapsed into one:

| Layer | Question it answers | Cadence | Who may change it | Persistence |
| --- | --- | --- | --- | --- |
| **Registry** (`whitelist_status`) | *Is this ticker allowed in the trading universe at all?* | rarely; owner-driven | **owner only** (bot may set `pending`, never `approved`) [LAW] | durable (`instrument_reference`) |
| **Eligibility** (per-cycle filter) | *Is this approved ticker tradable on THIS cycle?* | every decision cycle | the bot (deterministic filter) | transient (`signals.decision='skipped'` for that cycle) |

> **[LAW] skip ≠ remove.** A failing `approved` ticker is marked `skipped` **for that cycle only**; its
> `whitelist_status` stays `approved`. The bot must **never** demote, remove, or auto-add a ticker in the
> registry as a side effect of an eligibility failure. (TZ §5, §7.3; frozen-decisions.md, "Strategy, data &
> backtest honesty" (managed registry + per-cycle eligibility rows).)

> **[LAW] the bot may NEVER auto-add a ticker to the trading universe.** Growing the universe must not
> silently grow risk. The only registry write the bot may perform autonomously is setting `pending` (a
> *proposal*, not an admission); promotion to `approved`/`watch_only` is an **owner decision** via the
> monthly-review Telegram flow (§6, TZ §12). (frozen-decisions.md, "Strategy, data & backtest honesty"
> (managed registry row).)

## 2. Registry — `whitelist_status` vocabulary [LAW]

Stored in `instrument_reference.whitelist_status` ([db-schema.md](db-schema.md) §2). **Frozen vocabulary —
reuse verbatim; do not rename or add values:**

```
whitelist_status ∈ { approved, managed_only, watch_only, blocked, pending }    -- NULL for indices
```

| Status | Meaning | Bot may enter? | Bot may exit/manage? | Set by |
| --- | --- | --- | --- | --- |
| `approved` | In the active trading universe; eligible for new entries (subject to §3 filters). | yes (if eligible) | yes | owner |
| `watch_only` | Tracked for data/ranking/monthly-review, **never traded**; no proposals. | no | n/a | owner |
| `managed_only` | A manually-adopted position outside `approved`: bot **manages exits** with the same exit rules but opens **no new entries**. | no | yes (exits only) | adoption flow (TZ §10) — owner confirms the prompt |
| `blocked` | Explicitly excluded — never proposed, never entered, even if data passes. | no | no (no new) | owner |
| `pending` | A monthly-review **replacement proposal**; awaiting owner confirm. **Not** in the trading universe yet. | no | n/a | **bot may set this** (the one autonomous registry write); owner promotes/discards |

Notes:
- Indices (`instrument_kind='index'`: IMOEX, MCFTR) carry `whitelist_status = NULL` and are not part of the
  trading universe; they live in `instrument_reference` only as benchmark/regime data
  ([db-schema.md](db-schema.md) §3). The eligibility filter (§3) **never** runs on an index row.
- A ticker present in the broker portfolio but **not** in `approved` triggers the manual-position prompt
  (TZ §10): owner picks `managed_only` / add to `approved` / ignore. Until then it is observe-only.
- `managed_only` positions use the **same exit rules** as bot entries; entry price = broker average,
  holding-period origin = broker operation date (fallback adoption date) (TZ §10). They are an **exit-only**
  adoption, never an entry source.

### 2.1 Config ↔ registry mapping

The committed config lists (`universe.*`, [config-and-secrets.md](config-and-secrets.md) §2.3) are the
**declarative source** of registry membership at load; the loader materializes them into
`instrument_reference.whitelist_status`. Each config list maps 1:1 to a status value:

| Config key | Maps to `whitelist_status` |
| --- | --- |
| `universe.approved` | `approved` |
| `universe.watch_only` | `watch_only` |
| `universe.managed_only` | `managed_only` |
| `universe.blocked` | `blocked` |
| `universe.pending` | `pending` |

A ticker must appear in **at most one** list; the loader hard-fails on a ticker present in two lists
(ambiguous status is a config error, not a silently-resolved default).
The owner-ratified M0 lists are `universe.approved=[SBER,T,GAZP,ROSN,TATN,X5]` and
`universe.watch_only=[IRAO,LKOH]` (2026-06-29).

Materialization semantics (`data/registry.py::materialize_universe_registry`, M1):

- **Idempotent:** a re-run with unchanged config changes no rows and journals nothing. A status transition
  updates the row **in place** (the reference table is mutable-with-provenance — uid-only PK, unlike
  version-keyed candles), bumps `source_version`, and appends one `audit_journal` row.
- **Config-driven changes are owner acts.** The loader writes only what the owner committed in config; this is
  the owner acting through config (§2), not an autonomous bot registry write.
- **Orphans (PROVISIONAL — owner decision 2.5, `docs/ops/pre-live-owner-decisions.md`):** a DB share row absent
  from every config list is **surfaced** to the caller as drift for owner attention — never deleted, never
  demoted (the bot may never demote, §2).
- Index rows (IMOEX/MCFTR) are seeded separately (`seed_index_reference`): `instrument_kind='index'`,
  `is_tradable=0`, `whitelist_status NULL`; `cash`/`equal_weight` stay synthetic with no DB row
  ([db-schema.md](db-schema.md) §3.1).

## 3. Per-cycle eligibility filter (TZ §5, §6, §7.3) [LAW: starting values]

Runs **only** on `approved` tickers, **every decision cycle** (after the final D1 close, per
`close_definition`; [config-and-secrets.md](config-and-secrets.md) §2.9). A ticker that fails **any** check
is recorded as `signals.decision='skipped'` with the matching `signals.reason` code (§4) and contributes no
proposal that cycle. A ticker passing **all** checks is eligible to be ranked (TZ §6) for the ≤1 daily
proposal.

> **Close convention ratified.** `close_definition=auction_close` and `daily_run_time=19:05 Europe/Moscow`
> (owner decision 2026-06-29, config §2.9). "After the final D1 close" means after the main-session auction
> close source, not after the evening GetCandles D1 close.

### 3.1 Filter checks (config thresholds — [config-and-secrets.md](config-and-secrets.md) §2.4)

> **Data_conflict is a post-close pre-check; live session status is submit-time.** Per TZ §7/§8 the
> `data_conflict → skip entry` gate runs before this filter and emits `signals.decision='skipped'` with
> reason `data_conflict` (§4). The live `session = NORMAL_TRADING` gate
> (`risk.allowed_trading_status`; DEALER_NORMAL_TRADING + auction states excluded) is **not** run during the
> post-close daily selection cycle because no order is submitted there. It is re-read in confirm/preflight
> immediately before entry order submission; a failure rejects the proposal rather than rewriting a selected
> signal to `skipped`.

| # | Check | Config key | Starting value | Fail → skip reason |
| --- | --- | --- | --- | --- |
| 1 | **Candles present & complete** — required lookback of `is_complete=1` D1 bars exists, none stale/missing | (see §3.3 warm-up) | — | `data_missing` |
| 2 | **Min recent trading days** — ≥ N D1 bars actually traded in the recent window | `eligibility.min_trading_days` | `40` | `low_liquidity` |
| 3 | **Min avg daily turnover** — `instrument_reference.avg_turnover_rub` ≥ threshold | `eligibility.min_turnover_rub` | `50_000_000` (₽) | `low_liquidity` |
| 4 | **Max spread** — `instrument_reference.spread_bps` ≤ threshold | `eligibility.max_spread_bps` | `50` (bps = 0.50%) | `wide_spread` |
| 5 | **Max lot value** — `lot × reference_price` ≤ `max_lot_value_pct` of `risk.capital_rub` | `eligibility.max_lot_value_pct` | `30` (% of capital) | `lot_too_expensive` |

All money/price comparisons use Quotation `units`/`nano` integers — **never float**
([db-schema.md](db-schema.md) §1). `spread_bps` and `avg_turnover_rub` are the integer liquidity stats
already stored on `instrument_reference`.

### 3.2 Evaluation order & reason precedence (deterministic)

The filter is **fail-fast in a fixed order** so the recorded skip reason is deterministic and a single,
defensible cause is logged per cycle. Within §3.1 the order = the table above: **1 → 5**. The step-1
data-truth pre-check (`data_conflict`) runs earlier in the engine (step 1, §5) and therefore **dominates**
every step-3 eligibility reason — the overall per-cycle precedence is:

```
data_conflict  >  data_missing  >  low_liquidity  >  wide_spread  >  lot_too_expensive
└ step-1 data-truth pre-check ┘  └────────────── step-3 eligibility filter (§3.1) ──────────────┘
```

Rationale: data problems (the ticker can't be trusted *at all*) dominate economic-fit problems
(too illiquid / too wide / too expensive). The first failing check wins; remaining
checks are not evaluated. (Implementation note: this ordering is the contract — do not reorder without an
owner decision, since it changes which reason is journaled.)

### 3.3 Warm-up / lookback (no-lookahead) [LAW]

- The candle-presence check (§3.1 #1) requires `warm_up = max(ma_slow, local_high_lookback)` D1 bars of
  `is_complete=1` data, evaluated on the **pinned live windows** (`ma_slow=50`, `local_high_lookback=20`, index
  regime MA = 50) → **≥ 50** completed D1 bars per instrument (and ≥ 50 on the index series). This matches
  [strategy.md](strategy.md) §3.2 (`max(ma_slow, local_high_lookback)`) so the live entry gate and the strategy
  warm-up agree. The research-grid MA100 (~100 bars) is **not** the live gate — it only sizes the **backtest**
  leading-history load (TZ §13: load ~100 leading bars so the first tradable day is not starved). A ticker
  without enough warm-up history → `data_missing`.
- Eligibility is computed **only from closed bars** (`candles.is_complete=1`, per ratified
  `close_definition=auction_close`, config §2.9 — see §3 note) — the filter must not read an in-progress bar. This inherits the
  **no-intraday-lookahead** LAW ([db-schema.md](db-schema.md) §4; frozen-decisions.md, "Strategy, data &
  backtest honesty" (no-lookahead row)).
- Newly-approved tickers (`first_1day_candle_date` too recent for warm-up) skip with `data_missing` until
  enough history accrues — never silently traded on a starved window.

## 4. Skip-reason vocabulary [LAW]

Recorded in `signals.reason` when `signals.decision='skipped'`. **Frozen vocabulary — reuse verbatim from
[db-schema.md](db-schema.md) §2 / frozen-decisions.md, "Strategy, data & backtest honesty" (per-cycle
eligibility row); do not invent new codes:**

```
signals.reason (skip codes) ∈ {
  lot_too_expensive,   -- lot × price exceeds eligibility.max_lot_value_pct of capital
  low_liquidity,       -- avg turnover < min_turnover_rub OR traded days < min_trading_days
  wide_spread,         -- spread_bps > eligibility.max_spread_bps
  not_trading,         -- instrument is not generally tradable/available; live session failures reject proposals in preflight
  data_missing,        -- required candles absent / incomplete / insufficient warm-up
  data_conflict        -- instrument_reference.data_status = 'data_conflict'
}
```

Notes:
- `signals.decision` is the frozen `{candidate, selected, skipped, risk_rejected}` vocabulary
  ([db-schema.md](db-schema.md) §2). **An eligibility failure is `skipped`, not `risk_rejected`** —
  `risk_rejected` is reserved for the risk engine's own rejections (sizing/limit/regime), recorded with no
  order row (TZ §7, [db-schema.md](db-schema.md) §3.2). Eligibility skips and risk rejections are distinct
  decision states.
- A ticker can fail multiple checks; only the **first** per §3.2 precedence is stored as `signals.reason`.
- `low_liquidity` covers **both** the turnover (§3.1 #3) and the min-trading-days (§3.1 #2) failures — there is
  no separate "too few days" code in the frozen vocabulary; do not add one.
- These six codes are the **complete** set. Any new condition must map onto an existing code or be raised as
  an owner decision to extend the frozen vocabulary (same-change rule across this file, db-schema, config,
  and frozen-decisions.md, "Strategy, data & backtest honesty" (per-cycle eligibility row)).

## 5. Interaction with the risk engine (ordering)

Per TZ §7 the risk engine runs, in order: **pre-checks → hard order rules → eligibility filters → sizing →
limits → re-entry → exits → controls**. Eligibility (this contract, §3) is **step 3** — it runs **after**
the account/mode/market-regime/`data_conflict` pre-checks and the LIMIT-only / no-margin / long-only
hard rules, and **before** sizing. Consequences:

- The **`data_conflict`** skip is emitted by the **step-1 data-truth pre-check**, not by this §3.1 filter — it
  short-circuits the cycle before eligibility runs (§3.1 note, §3.2). The live session gate (`NORMAL_TRADING`)
  runs in preflight/submit, not in the post-close eligibility pass. The §3.1 filter owns the step-3 reasons
  `data_missing` / `low_liquidity` / `wide_spread` / `lot_too_expensive`; `not_trading` is reserved for
  non-session tradability/unavailability cases.
- A ticker that fails eligibility is `skipped` and never reaches sizing — so it can never consume the
  ≤1-proposal/day budget or a position slot.
- Eligibility does **not** override the registry: a `blocked` / `watch_only` / `managed_only` /`pending`
  ticker is excluded **before** the eligibility filter even runs (it is not `approved`), so the filter
  evaluates the `approved` set only.
- Eligibility is an **entry gate only**. It **never** blocks a protective **exit** — exits are always
  allowed regardless of liquidity/spread/data state (mirrors the data-truth and kill-never-sells LAWs;
  frozen-decisions.md, "Order & risk rules" (kill/pause row) + "Strategy, data & backtest honesty" (data truth
  row)). Exit logic lives in the risk engine's exit step, not here.

## 6. Monthly whitelist-review job (TZ §12) — the only registry-mutation path

A scheduled job re-evaluates `approved` + `watch_only` on liquidity / lot value / spread / availability /
signal quality (the same stats the filter uses) and may emit **replacement proposals to Telegram for owner
confirm**. Strict rules [LAW]:

- The bot **may set a candidate to `pending`** (a proposal) — this is the single autonomous registry write.
- The bot **may NOT** set `approved` or `watch_only` autonomously; promotion is an **owner decision** via the
  Telegram confirm flow.
- The bot **may NOT** auto-remove an `approved` ticker. A persistently-failing `approved` ticker continues to
  be `skipped` each cycle (never silently demoted); the monthly job may *propose* its replacement, but only
  the owner enacts it.
- Every registry change is journaled in `audit_journal` ([db-schema.md](db-schema.md) §3.3) with the actor
  (`'owner:<telegram_user_id>'` for confirmations; `'system'` for the `pending` proposal; `'system'` with a
  `detail` JSON naming the config origin for config-materialized status changes — the owner authored the
  config, the process applying it is the system; PROVISIONAL — owner decision 2.2,
  `docs/ops/pre-live-owner-decisions.md`).

## 7. Worked examples (illustrative — fixtures live with M1/M4 tests)

Capital `risk.capital_rub = 10_000` ₽; thresholds at their starting values (§3.1).

1. **SBER** (`approved`), `NORMAL_TRADING`, full history, `data_status='ok'`, turnover 60B ₽, spread 4 bps,
   lot 10 × ~310 ₽ ≈ 3 100 ₽ → lot value 31% of 10 000 ₽ **> 30%** → fail eligibility check §3.1 #5 →
   `signals.decision='skipped'`, `signals.reason='lot_too_expensive'`. Stays `approved`.
2. **X5** (`approved`), but the instrument reference says the share is not generally tradable/available →
   `skipped`, `reason='not_trading'`, even though liquidity is fine. If the only issue is that the venue is
   currently outside `NORMAL_TRADING`, the daily selection cycle may still create a next-session proposal; the
   live session gate is re-read at preflight and rejects the proposal if the next-session venue state is still
   ineligible.
3. **TATN** (`approved`), `NORMAL_TRADING`, but T-Invest vs MOEX ISS D1 close diverge > 0.5% →
   `data_status='data_conflict'` → fail the **step-1 data_conflict pre-check** → `skipped`,
   `reason='data_conflict'`. (Entry skipped; any open TATN position still exits normally — §5.)
4. **IRAO** (`watch_only`): never evaluated by the eligibility filter and never proposed — it is not
   `approved`. Tracked for monthly review only.

## 8. Frozen invariants honored

- **Managed registry, not hard-coded** (frozen-decisions.md, "Strategy, data & backtest honesty" (managed
  registry row); TZ §5): membership is the
  `{approved, managed_only, watch_only, blocked, pending}` vocabulary in `instrument_reference`, sourced from
  the `universe.*` config lists.
- **Bot may NEVER auto-add to the trading universe** (frozen-decisions.md, "Strategy, data & backtest honesty"
  (managed registry row)): the only autonomous registry
  write is `pending`; `approved`/`watch_only` promotion is owner-only (§1, §6).
- **Per-cycle eligibility filters with the frozen starting values** (frozen-decisions.md, "Strategy, data &
  backtest honesty" (per-cycle eligibility row); TZ §7.3): max lot
  value 30%, max spread 0.50% (50 bps), min turnover 50M ₽, min trading days 40, trading-status + candles
  required (live trading-status is re-read at preflight/submit; candles + the economic-fit checks are §3.1).
- **skip ≠ remove from approved** (frozen-decisions.md, "Strategy, data & backtest honesty" (per-cycle
  eligibility row)): an eligibility failure marks `skipped` for the
  cycle only; `whitelist_status` is untouched (§1, §3, §6).
- **Frozen skip-reason vocabulary** (frozen-decisions.md, "Strategy, data & backtest honesty" (per-cycle
  eligibility row); [db-schema.md](db-schema.md) §2):
  `{lot_too_expensive, low_liquidity, wide_spread, not_trading, data_missing, data_conflict}` — no new codes
  (§4).
- **No intraday lookahead** (frozen-decisions.md, "Strategy, data & backtest honesty" (no-lookahead row)):
  eligibility reads only closed (`is_complete=1`) bars per
  `close_definition` (§3.3).
- **Data truth** (frozen-decisions.md, "Strategy, data & backtest honesty" (data truth row)): `data_conflict`
  is a skip-reason; exits are never blocked (§5).
- **Exits always allowed** (frozen-decisions.md, "Order & risk rules" (kill/pause row) + "Strategy, data &
  backtest honesty" (data truth row)): the filter is an entry gate only (§5).
- **Money is never float** (frozen-decisions.md, "Strategy, data & backtest honesty" surface; db-schema §1):
  all eligibility comparisons use Quotation `units`/`nano` integers (§3.1).
- **Enum parity** ([db-schema.md](db-schema.md) §2, [config-and-secrets.md](config-and-secrets.md) §2.3):
  `whitelist_status` and `signals.reason` reused verbatim.

## 9. Open questions / owner-pending

- **Eligibility thresholds are *starting* values [verify, empirical M2/M3]** — `max_lot_value_pct` (30),
  `max_spread_bps` (50), `min_turnover_rub` (50M), `min_trading_days` (40) are config defaults the
  cost-sensitivity / liquidity analysis may revise. The frozen LAW pins them as **starting** values, not
  permanent constants; revising them is a config change (same-change rule if a LAW-mirrored value moves).
- **Reference price for the lot-value check (§3.1 #5) [verify, M1/M4]** — which price (prior D1 close vs the
  pre-order last/best) feeds `lot × price ≤ 30%`. The eligibility *filter* (post-close cycle) uses the prior
  closed D1; the *order preflight* (TZ §8) re-checks lot/price/limits at confirm time with the live
  reference. Pin the exact eligibility-stage reference at M1.
- **`spread_bps` / `avg_turnover_rub` computation window [verify, M1]** — the exact recent-window length and
  averaging method behind these `instrument_reference` liquidity stats are an M1 data-layer decision; this
  contract consumes them and does not define their computation.
- **Monthly-review scoring [owner / M5]** — the exact ranking/scoring that turns liquidity/availability/
  signal-quality into a `pending` replacement proposal is deferred to the monthly-review job (TZ §12); only
  the *governance* (bot sets `pending`, owner promotes) is frozen here.

## 10. Cross-references

- Spec `docs/TZ.md` §5 (data/registry), §6 (strategy ranking), §7.3 (eligibility), §10 (manual-position
  adoption), §12 (monthly whitelist review).
- Frozen LAW `docs/frozen-decisions.md`, "Strategy, data & backtest honesty" (managed registry, per-cycle
  eligibility filters, skip-reasons, no-lookahead, data truth rows) + "Order & risk rules" (kill/pause +
  market-regime rows — exits always allowed).
- Schema [db-schema.md](db-schema.md) (`instrument_reference.whitelist_status` / `data_status` /
  `spread_bps` / `avg_turnover_rub`, `signals.decision` / `signals.reason`, `audit_journal`).
- Config [config-and-secrets.md](config-and-secrets.md) §2.3 (`universe.*`), §2.4 (`eligibility.*`), §2.5
  (`risk.allowed_trading_status`, `risk.capital_rub`), §2.9 (`data_conflict.*`, `close_definition`).
- Skills: `risk-policy-guardian` (eligibility + registry guard), `lookahead-auditor` (closed-bar reads),
  `state-machine-discipline` (decision states).
