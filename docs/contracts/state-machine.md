# Contract — Order/position state machine & execution (TZ §8)

> **Status:** M0 contract, **resolved on paper (no code yet)**. This pins the **build-ready** state model
> the M4 `execution/` + `risk/` + `broker/` layers implement **verbatim**. **`docs/frozen-decisions.md` 🔒
> wins** on any conflict — values marked **[LAW]** mirror a frozen invariant and may not be changed here
> (only via owner decision + ADR + same-change rule). A divergent state, transition, or guard silently
> weakens an invariant, so the enums below are **frozen** and reuse [db-schema.md](db-schema.md) §2 exactly.
> **[owner-pending]** = a value the owner must confirm before it is locked (do not silently fix it).
> **[verify]** = depends on a `docs/contracts/` / TZ §20 fact still being confirmed.
>
> This is the natural **executable-test** target: every transition table below is a unit-test row
> (pure, dependency-injected — TZ §18). Pairs with [config-and-secrets.md](config-and-secrets.md),
> [db-schema.md](db-schema.md), [tax-and-dividends.md](tax-and-dividends.md).

---

## 1. Scope & vocabulary (reused verbatim — never rename)

This contract governs the lifecycle **signal → proposal → confirm → preflight → order → (partial) fill →
position → exit**, plus the control plane (`pause` / `kill` / `resume`) and **startup reconciliation**. It
reuses the frozen enums from [db-schema.md](db-schema.md) §2 — the **same column names and values**, no
synonyms:

```text
signals.decision      : candidate | selected | skipped | risk_rejected         (db-schema §2)
proposals.state       : awaiting_confirmation | confirmed | rejected | expired (db-schema §2)
orders.side           : buy | sell
orders.type           : LIMIT  (only — market/bestprice hard-rejected upstream, never storable [LAW])
orders.state          : submitted | partially_filled | filled
                        | cancel_requested | cancelled | reconcile_required     (db-schema §2)
positions.source      : bot | manual_adopted | managed_only
positions.state       : open | closed
positions.close_reason: risk | trend | target_trailing | time | manual
reconciliations.result: clean | mismatch | blocked
control_state.mode    : running | paused | killed | blocked_reconciliation_mismatch  (db-schema §2)
```

**Identity / keys** (db-schema §3):
- **Idempotency key = `orders.order_id`** — the CLIENT-generated key passed to **every** T-Invest `PostOrder`
  and the table PK. A retry/restart with the same `order_id` can never create a duplicate broker order [LAW].
- `proposals.proposal_id` (uuid) binds a confirm button to one `signals.id` and one `telegram_user_id`.
- `orders.account_id` / `positions.account_id` = the guarded bot account; asserted `== config.account_id`
  at submit **and** at reconciliation [LAW] (TZ §7.1, config §3).

## 2. Signal → proposal (TZ §6 ranking, §8)

The strategy is a pure function; the risk engine (TZ §7) decides. Exactly one outcome per candidate per cycle.

| From (`signals.decision`) | Event / guard | To | Side effects | TZ |
| --- | --- | --- | --- | --- |
| (new candidate) | strategy emits a candidate for an `approved` ticker | `candidate` | row written, `features` JSON snapshot | §6 |
| `candidate` | fails an eligibility filter (per-cycle, config §2.4) | `skipped` | `reason` ∈ `lot_too_expensive`\|`low_liquidity`\|`wide_spread`\|`not_trading`\|`data_missing`\|`data_conflict` (db-schema §2); **skip ≠ remove from approved** [LAW] | §7.3 |
| `candidate` | passes eligibility, **wins** the ≤1/day ranking (§6) | `selected` | exactly **one** `proposal` created (`awaiting_confirmation`) | §6, §8 |
| `candidate`/`selected` | a hard risk rule rejects (limit breach, regime, daily hard stop, max-positions, re-entry cooldown, dividend-gap, …) | `risk_rejected` | **recorded HERE only — NO order row** (db-schema §3.2); `reason` set | §7 |

**Hard gating before `selected` (TZ §7, all [LAW]):** account_id guard passes; `control_state.mode = running`
(not `paused`/`killed`/`blocked_reconciliation_mismatch`); market-regime OK (IMOEX close ≥ MA50 **and** 5d
return ≥ `risk.market_regime_5d_floor_pct`); `data_status != data_conflict`; `risk.max_open_positions` not
exceeded; `risk.max_proposals_per_day` not exceeded; daily hard stop not hit; re-entry cooldown clear;
dividend-gap window clear (tax §6). The post-close selection cycle does **not** require live
`NORMAL_TRADING`, because it creates a next-session proposal rather than submitting an order; the
`NORMAL_TRADING` / `DEALER_NORMAL_TRADING` / auction-state gate is mandatory in preflight (§4) immediately
before any entry order submission. **Entry-only gates never block a protective exit.**

## 3. Proposal lifecycle (`proposals.state`) — wall-clock TTL (TZ §8)

`ttl_ms` is set from `config.button_ttl_minutes` (default `45`, db-schema `proposals.ttl_ms`). **TTL is
wall-clock**: expiry is judged by `created_at + ttl_ms` vs **now**, NOT by process uptime — a proposal whose
wall-clock TTL elapsed while the bot was down is **expired on the next evaluation/restart** (§8, §10). One
confirm button = one `proposal_id` + one whitelisted `telegram_user_id` (replay-protected, one-shot).

| From (`proposals.state`) | Event / guard | To | Side effects | TZ |
| --- | --- | --- | --- | --- |
| (new) | a signal reaches `selected` | `awaiting_confirmation` | button sent to the bound `telegram_user_id`; `audit_journal: proposal_created` | §8, §10 |
| `awaiting_confirmation` | whitelisted bound user taps **Подтвердить** **and** `now < created_at + ttl_ms` | `confirmed` | trigger **preflight** (§4); `audit_journal: confirm_received` | §8 |
| `awaiting_confirmation` | user taps **Отклонить** | `rejected` | no order; `audit_journal: proposal_rejected` | §10 |
| `awaiting_confirmation` | `now ≥ created_at + ttl_ms` (timer fire **or** first evaluation after restart) | `expired` | button dead; alert (TTL); `audit_journal: proposal_expired` | §8 |
| `awaiting_confirmation` | confirm by a non-bound / non-whitelisted user | `awaiting_confirmation` (unchanged) | **ignored + logged**, no state change [LAW: whitelist] | §10 |
| `awaiting_confirmation` | `kill` issued | `awaiting_confirmation` (frozen) | no auto-expire; resume re-evaluates (TTL may expire it) | §7.8 |
| `confirmed` | preflight passes (§4) → entry order submitted (§5) | `confirmed` (terminal; an `orders` row now carries the lifecycle) | first `orders` row created (`submitted`, §5); `audit_journal: order_submitted` | §8 |
| `confirmed` | preflight fails (§4) | `rejected` | **no order** (§4); `audit_journal: preflight_failed`; Telegram alert | §8 |

**Restart rule (idempotent):** on startup every `awaiting_confirmation` proposal is re-evaluated against
wall-clock TTL **before** any new action; expired ones move to `expired` (no stale button can fire) [LAW].
For `confirmed` proposals, startup adopts an existing local/broker order for the deterministic `order_id`;
if no such order exists, it rejects/expires the old confirm with `reason=restart_before_submit` and requires a
fresh signal/proposal/confirm. Startup must never re-enter preflight and submit a newly-created entry order
from an old confirm [LAW].

## 4. Preflight (the re-check between confirm and submit) (TZ §8)

Preflight runs **on confirm** (and again on `resume`, §7) and is a hard gate — **any failure aborts to NO
order**: the proposal moves `confirmed → rejected` (§3) and nothing is submitted. It re-checks, fresh, because
state may have moved since the proposal was created:

1. **Account guard** — `account_id` present and exactly matches `config.account_id` [LAW] (config §3).
2. **Mode** — `control_state.mode = running` (abort on `paused`/`killed`/`blocked_reconciliation_mismatch`).
3. **Tradability + session** — `is_tradable=1` and trading status `NORMAL_TRADING` (TZ §9).
4. **Price / spread / lot / tick** — re-read last price, `min_price_increment`, lot; spread ≤
   `eligibility.max_spread_bps`.
5. **Limits** — `risk.max_open_positions`, `risk.cash_reserve_pct`, daily hard stop, `risk.max_proposals_per_day`.
6. **No conflicting order/position** — no active order for the uid; not already holding it; re-entry cooldown clear.
7. **Limit price construction** — reference + ≤ `order.max_entry_premium_pct` (0.20%), **rounded to a valid
   `min_price_increment` tick — DOWN for a buy** so the 0.20% ceiling is never exceeded [LAW] (TZ §8).

Pass → build the entry order with a **deterministic client `order_id` for this `proposal_id`**, derived or
persisted before any broker call and reused on every retry/restart (§5); the proposal stays `confirmed`
(its lifecycle now lives on the `orders` row, §3). Fail → proposal `confirmed → rejected` (§3); `audit_journal:
preflight_failed` + Telegram alert; no order.

## 5. Order lifecycle (`orders.state`) — idempotent, one attempt, no chasing (TZ §8)

`order.type = LIMIT` only [LAW]; market/bestprice are hard-rejected upstream and are **not storable**
(db-schema `orders.type` CHECK). **One order attempt per signal, no price chasing** [LAW] — a cancelled/
expired entry does **not** auto-resubmit; a new attempt needs a new signal cycle. The order `order_id` is the
client idempotency key on **every** `PostOrder` (db-schema §3.2). TTL = `config.order.ttl_minutes` (default
`45`, range 30–60), **wall-clock** from submit.

| From (`orders.state`) | Event / guard | To | Side effects | TZ |
| --- | --- | --- | --- | --- |
| (new) | preflight passed → `PostOrder(order_id=…)` accepted | `submitted` | `broker_order_id` stored; `attempts=1`; `audit_journal: order_submitted` | §8 |
| `submitted` | partial execution report | `partially_filled` | `fills` row(s); position opened/updated for filled qty | §8 |
| `submitted` | fully executed | `filled` | `fills` row(s); **`positions` opened** (`source=bot`, `state=open`); `audit_journal: position_opened` | §8 |
| `submitted` | wall-clock TTL elapsed, **0 filled** | `cancel_requested` → `cancelled` | cancel sent; **no resubmit** (one attempt) [LAW]; alert (TTL cancel) | §8 |
| `partially_filled` | remainder fully executes | `filled` | remaining `fills`; position avg/qty updated | §8 |
| `partially_filled` | wall-clock TTL elapsed with remainder open | `cancel_requested` → `cancelled` | **cancel the remainder; KEEP + manage the filled position** [LAW] (no chase) | §8 |
| `submitted`/`partially_filled` | `pause` issued | `cancel_requested` → `cancelled` | **cancel any unfilled entry quantity**; if partially filled, KEEP + manage the filled position; monitoring/protective exits continue; no live entry BUY order may remain while paused [LAW] | §7.8 |
| `submitted`/`partially_filled` | `kill` issued | `cancel_requested` → `cancelled` | **cancel active orders; NEVER sell** the filled portion [LAW] | §7.8 |
| `submitted`/`partially_filled`/`cancel_requested` | broker/local disagree (state, qty, missing order) at reconcile | `reconcile_required` | freeze acting on this order; raise reconciliation (§10) | §8 |
| `cancel_requested` | broker confirms cancel | `cancelled` | terminal; if partial earlier, the filled position persists | §8 |
| any non-terminal | duplicate `PostOrder` with the same `order_id` (retry/restart) | (unchanged) | **dedupe — no second broker order** [LAW: idempotency] | §8 |

**Terminal order states:** `filled`, `cancelled`. `reconcile_required` is a **holding** state cleared only by
reconciliation (→ adopt true broker state, or `cancelled`/`filled`).

**Exit orders** are `orders` rows too (db-schema: `proposal_id` NULL for protective exits): `side=sell`,
`type=LIMIT`, same idempotency/TTL machinery. Exits are emitted by the position FSM (§6), **not** by a
proposal/confirm, and are **never blocked** by entry-only gates or by `pause`. **Long-only / no-shorts**: a
sell may never exceed held qty — enforced in the risk engine and the adapter normalizer (TZ §7.2, §9),
never stored as a short [LAW].

## 6. Position lifecycle (`positions.state`) + automated exits (TZ §7.7)

A position opens from an entry fill (`source=bot`) or via reconciliation adoption (`manual_adopted` /
`managed_only`, §10). While `open`, the position FSM monitors **every** evaluation cycle and emits a protective
**exit order** (§5) when an exit rule fires; the position closes only when that sell **fully fills**.

| From (`positions.state`) | Event / guard | To | `close_reason` | TZ |
| --- | --- | --- | --- | --- |
| (new) | entry order `filled`/`partially_filled` | `open` | — | §8 |
| (new) | reconciliation adopts an external long | `open` | — (`source=manual_adopted`\|`managed_only`) | §8, §10 |
| `open` | hard stop-loss ~`risk.hard_stop_pct` (≈4%) hit | (exit sell) → `closed` on full fill | `risk` | §7.7 |
| `open` | trend break — close < MA50 (`strategy.trend_break_ma`) | (exit sell) → `closed` | `trend` | §7.7 |
| `open` | take-profit `strategy.take_profit_pct` (6%) then trailing `strategy.trailing_pct` (3%) / close < MA20 | (exit sell) → `closed` | `target_trailing` | §7.7 |
| `open` | time exit — held ≥ `strategy.max_holding_days` **[owner-pending {20,40}]** (8-week max w/o review [LAW]) | (exit sell) → `closed` | `time` | §7.7 |
| `open` | owner taps Telegram **«Закрыть позицию»** + confirm | (exit sell) → `closed` | `manual` | §10 |
| `open` | `kill` issued | `open` (unchanged) | — | **kill NEVER sells** [LAW] | §7.8 |

**db-schema CHECK honored:** `state=open` ⇒ `closed_at IS NULL AND close_reason IS NULL`; `state=closed` ⇒
both non-NULL (db-schema §3.2). **Exit ordering / priority:** protective `risk` (and `trend`) exits are
**always allowed even under `pause`** — entry-only gates never block a protective exit (frozen-decisions.md,
"Order & risk rules" (Risk exits grouping; market-regime row: *exits are always allowed*)). Under
`blocked_reconciliation_mismatch` the frozen BINARY (TZ §8) is narrower — see the canonical exit policy in §7
and §10: **RISK exits ALLOWED; PROFIT / `target_trailing` exits FORBIDDEN**; `trend`/`time` are not classified
by the frozen LAW and are **[owner-pending]** (not auto-fired while blocked). A risk exit takes precedence if
multiple fire the same cycle.

## 7. Control plane (`control_state.mode`) — singleton, persisted (TZ §7.8)

`control_state` is a **singleton** row (db-schema `id=1` CHECK) persisted across restarts so `pause`/`kill`/
`blocked_reconciliation_mismatch` survive a crash and are read **on startup before any action** (db-schema
§3.3). Commands arrive via Telegram (`/pause`, `/resume`, `/kill`) from a whitelisted user.

| From (`control_state.mode`) | Command / event | To | Effect [LAW] | TZ |
| --- | --- | --- | --- | --- |
| `running` | `/pause` | `paused` | **block NEW entries**; cancel/cancel-request still-live entry BUY orders; keep monitoring + automated exits/protective exit orders | §7.8 |
| `running` | `/kill` | `killed` | stop the bot; **cancel active orders only** (§5); **NEVER sell positions** | §7.8 |
| `paused` | `/resume` | `running` | requires **extra confirmation + preflight** (§4) before re-enabling entries | §7.8 |
| `paused` | `/kill` | `killed` | as above | §7.8 |
| `killed` | `/resume` | `running` | requires **extra confirmation + preflight**; startup reconciliation must be clean first | §7.8, §8 |
| `running`/`paused` | reconciliation persistent mismatch (§10) | `blocked_reconciliation_mismatch` | block new entries; monitoring on; **RISK exits ALLOWED, PROFIT/`target_trailing` exits FORBIDDEN** (frozen BINARY, §10); `trend`/`time` **[owner-pending]**, not auto-fired | §8 |
| `blocked_reconciliation_mismatch` | 2 consecutive clean reconciliations + owner confirm | `running` | exit the blocked state only after reconcile clears | §8 |
| any | `/kill` | `killed` | kill is always reachable; idempotent | §7.8 |

**`pause` vs `kill` (the load-bearing distinction):** `pause` blocks **new entries** and cancels any still-live
entry BUY quantity so it cannot fill after the pause, but the position FSM (§6) keeps running — monitoring and
**automated exits stay on**. `kill` halts the engine and cancels open orders but **must never itself sell a position** — exits become a manual owner action only. **`resume` from
either state requires extra confirmation AND a fresh preflight** [LAW]; resume from `killed` additionally
requires a clean startup reconciliation.

## 8. Idempotency & wall-clock semantics (TZ §8) [LAW]

- **Single idempotency key:** `orders.order_id` (client-generated, the table PK, passed to every `PostOrder`).
  No other surrogate. Re-processing the same logical action (retry, restart, duplicate scheduler tick) reuses
  the **same** `order_id` and therefore can place **no second broker order** (db-schema §4 idempotency).
- **`attempts`** (db-schema `orders.attempts`) counts broker-call attempts for **one** `order_id` (network
  retries/backoff under T-Invest rate limits — ≤50 req/s total, `PostOrder` 15/s, TZ §9). It does **not**
  authorize a *new* entry attempt: **one order attempt per signal, no price chasing** [LAW].
- **Wall-clock TTL** governs **both** `proposals.ttl_ms` and `orders` TTL: expiry = `created_at + ttl` vs
  **now** (not uptime). A TTL that elapsed while the process was down is honored at the next evaluation —
  expired proposals never fire; expired unfilled orders are cancelled (never silently left live or resubmitted).
- **Money equality:** all price/commission comparisons use Quotation `units`/`nano` integer equality — never
  float (db-schema §1) — so reconciliation/idempotency matches are exact.

## 9. Audit trail (TZ §8, §12) [LAW]

Every transition above writes an **append-only** `audit_journal` row (db-schema §3.3 triggers block
UPDATE/DELETE) FK-linking `proposal_id → order_id → position_id`, with `actor` = `system` or
`owner:<telegram_user_id>` and `account_id` = the guarded account (NULL only for global pause/kill events).
Canonical `event` values (db-schema §3.3 comment): `signal_selected`, `proposal_created`, `confirm_received`,
`order_submitted`, `fill`, `position_opened`, `exit`, `pause`, `resume`, `kill`, `reconciliation`. The chain
**signal → proposal → confirm → order → fill → position → exit** must be reconstructable end-to-end from the
journal alone.

## 10. Startup reconciliation & adoption of external/manual change (TZ §8) [LAW]

On startup/restart (and periodically), reconcile **before trading**: read broker positions + active orders +
cash and compare to local state. **External/manual changes are adopted via reconciliation — NEVER treated as
errors** [LAW].

**Procedure (TZ §8):**
1. Read `control_state` first; if `killed` stay killed; if `blocked_reconciliation_mismatch` keep the blocked
   policy until cleared.
2. Re-evaluate every `awaiting_confirmation` proposal against **wall-clock** TTL → `expired` if elapsed (§3).
   Resolve every `confirmed` proposal before any new action: if a local/broker order exists for its deterministic
   `order_id`, adopt broker truth (§5, idempotent by `order_id`); if no such order exists, transition
   `confirmed → rejected` (or `expired` if TTL elapsed), audit `reason=restart_before_submit`, alert the owner,
   and require a fresh signal/proposal/confirm. **Startup must never submit a newly-created entry order from an
   old confirmed proposal** [LAW].
3. Reconcile orders/positions/cash; retry up to **3×** with backoff `60s / 180s / 300s`; require **2
   consecutive clean checks** before normal trading (`reconciliations.result = clean`).
4. Persistent mismatch → `reconciliations.result = mismatch` then `control_state.mode =
   blocked_reconciliation_mismatch`. **Canonical `blocked_reconciliation_mismatch` exit policy** (this and
   [reconciliation.md](reconciliation.md) §5 MUST state it identically — the frozen BINARY, TZ §8;
   frozen-decisions.md, "Order & risk rules"):
   - **NEW entries** — BLOCKED.
   - **monitoring / evaluation** — ON (the position FSM keeps running).
   - **RISK exits** (hard stop-loss ~4%, daily hard stop) — ALLOWED.
   - **PROFIT / `target_trailing` exits** — FORBIDDEN.
   - **`trend`-break and `time` exits** — **[owner-pending]**: not classified by the frozen LAW, therefore
     **not auto-fired while blocked**; promoting either to allowed or forbidden needs an owner decision + ADR.
   - **`kill`** — reachable, NEVER sells.
   - **pre-exit precondition** — require a broker-confirmed position AND no conflicting active orders before any
     allowed exit; notify the owner BEFORE and AFTER every exit attempt (TZ §8).
5. A reconciliation that cannot be safely resolved → `reconciliations.result = blocked`.

**Adoption rules (TZ §8, §10):**

| External change observed | Adoption |
| --- | --- |
| Manual **buy** of an `approved` ticker (held but unknown locally) | adopt as a position (`source=bot` if it maps to a known order, else surface) and **manage** under bot exit rules |
| Manual **buy** of a ticker **outside** `approved` | **do not auto-manage** → Telegram manual-position prompt: «Сопровождать `managed_only`» / «Добавить в approved» / «Игнорировать» (TZ §10). `managed_only` uses the **same exit rules** as bot entries; entry price = broker average; holding-period source = broker operation date (fallback adoption date) |
| Manual **sell** / partial sell | update `positions` (qty/avg or `closed`, `close_reason=manual`); reconcile linked orders |
| **Deposit / withdrawal** | write `cash_events`; **recompute** capital + all limits (TZ §8) |
| Broker order absent / extra / qty mismatch vs local | set the affected `orders.state = reconcile_required`; resolve via the procedure above |

**Idempotency under reconciliation:** adoption matches on `order_id` / `instrument_uid` / Quotation-exact
price+qty; re-running reconciliation must not double-adopt or double-submit (the `order_id` key guarantees no
duplicate order).

## 11. Frozen invariants honored

- **Explicit state machine + audit trail** — signal → proposal → confirm → preflight → order → (partial) fill
  → position → exit, every transition journaled append-only (frozen-decisions.md, "Strategy, data & backtest
  honesty" (state-machine row); TZ §8, §12).
- **Idempotent transitions via client `order_id`** — no duplicate orders after restart/retry; the key is the
  PK passed to every `PostOrder` (frozen-decisions.md, "Order & risk rules" (idempotency/order_id row); TZ §8;
  db-schema §4).
- **Order TTL ~45 min (30–60), wall-clock** — unfilled → cancel; partially filled → cancel remainder + manage
  filled; **no price chasing, one attempt per signal** (frozen-decisions.md, "Order & risk rules" (order TTL
  row); TZ §8).
- **Limit-only / no margin / no shorts / long-only** — `orders.type=LIMIT` only; sell ≤ held qty
  (frozen-decisions.md, "Order & risk rules" (limit-only row); TZ §7.2, §9).
- **`kill` cancels orders but NEVER sells; `pause` blocks new entries, cancels/cancel-requests still-live entry
  BUY orders, and keeps monitoring + exits; `resume` needs extra confirmation + preflight** (frozen-decisions.md,
  "Order & risk rules" (kill/pause/resume row); TZ §7.8, §8).
- **Confirm-first** — entries require a whitelisted-user confirm on a TTL-bound proposal; preflight re-checks
  on confirm (frozen-decisions.md, "Order & risk rules" (confirm-first row); TZ §8, §10).
- **Account guard** — `account_id == config.account_id` asserted at submit and reconcile (frozen-decisions.md,
  "Account & access" (account guard row); config §3; db-schema §4).
- **Startup reconciliation; external/manual change adopted, not errored** (frozen-decisions.md, "Strategy, data
  & backtest honesty" (state-machine row); TZ §8).
- **No-lookahead at the edge** — entries only after the final D1 close, next session; the FSM never opens an
  entry intraday (frozen-decisions.md, "Strategy, data & backtest honesty" (no-lookahead row); TZ §6, §8).
  Protective exits monitor continuously (not an entry).
- **Entry-only gates never block a protective exit** — regime, daily hard stop, data_conflict, dividend-gap,
  pause, and reconcile-block all gate **entries**; risk exits stay allowed (frozen-decisions.md, "Order & risk
  rules" (risk exits + market-regime rows); TZ §7).
- **No float money** — all price/commission/TTL comparisons use Quotation integers / epoch-ms (db-schema §1).

## 12. Open questions / owner-pending

- **`strategy.max_holding_days`** drives the `time` exit — **[owner-pending]** from grid {20, 40}; pinned after
  M3 (config §2.6; frozen-decisions.md, "Known drift / owner decisions pending" (holding-horizon row)). The FSM
  treats it as a config value, never a hard-coded constant.
- **Periodic-reconciliation cadence** (beyond startup + post-restart) — **[owner-pending]**: TZ §8 fixes the
  startup retry schedule (3× at 60/180/300s, 2 clean checks); a steady-state periodic interval is not yet
  pinned. Do not assume a value.
- **`reconciliations.kind` vocabulary** — db-schema leaves `kind` free TEXT with examples
  `startup|periodic|post_restart`; **[verify]** whether to freeze it as a CHECK enum like the other state
  columns (mirrors `control_state.mode` discipline) — raise before M4.
- **Bot-account product type** (account-scoped token feasible, else rely on the guard) — **[verify, empirical
  at M4]** (config §6; TZ §9). Affects only the preflight account assertion's source, not the transitions.
- **Confirm-button replay edge:** TZ §8 says one-shot + replay-protected; the exact dedupe of a double-tap
  arriving within the same evaluation tick is an implementation detail pinned by the M4 state-machine tests
  (idempotent: the second tap finds the proposal no longer `awaiting_confirmation`).

## 13. Cross-references

- Frozen LAW: `docs/frozen-decisions.md` — "Strategy, data & backtest honesty" (state-machine, no-lookahead
  rows); "Order & risk rules" (idempotency/order_id, order TTL/one-attempt, limit-only/no-shorts,
  kill/pause/resume, confirm-first, risk exits + market-regime rows); "Account & access" (account guard row);
  "Known drift / owner decisions pending" (holding-horizon row).
- Spec: `docs/TZ.md` §6 (strategy/ranking), §7 (risk engine + controls), §8 (state machine & execution),
  §9 (broker adapter), §10 (Telegram control plane), §12 (audit/journal).
- Schema (enum/type parity — reused verbatim): [db-schema.md](db-schema.md) §2, §3, §4.
- Config (keys: `button_ttl_minutes`, `order.ttl_minutes`, `order.max_entry_premium_pct`, `account_id`,
  `mode`, risk/strategy limits): [config-and-secrets.md](config-and-secrets.md) §2, §3.
- Taxes (dividend-gap entry block feeding §2 gating): [tax-and-dividends.md](tax-and-dividends.md) §6.
- Skills: `state-machine-discipline`, `risk-policy-guardian`, `broker-api-contract`. Auditors:
  `risk-invariant-auditor`, `lookahead-auditor`.
