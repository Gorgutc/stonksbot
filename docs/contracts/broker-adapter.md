# Contract — T-Invest broker adapter (TZ §9, §7.2, §8)

> **Status:** M4 contract, **resolved on paper (no adapter code yet)**. This pins the **API-boundary
> discipline** the M4 `broker/` adapter implements verbatim — the last line of defense for the frozen risk
> invariants even if upstream logic slips. **`docs/frozen-decisions.md` 🔒 wins** on any conflict; this contract
> may only *detail* invariants, never weaken them. Values marked **[LAW]** mirror a frozen invariant and may not
> be changed here (only via owner decision + ADR + same-change rule).
> **[owner-pending]** = a value/behavior the owner must confirm before it is locked (do not silently fix it).
> **[verify]** = an API/SDK fact to re-confirm against live T-Invest docs/SDK before that code ships (TZ §20).
> **[verified]** = web-verified 2026 (TZ §9/§20; research archived in Second Brain `2026-06-27-tinvest-api-grounding`).
> Pairs with [db-schema.md](db-schema.md), [config-and-secrets.md](config-and-secrets.md),
> [tax-and-dividends.md](tax-and-dividends.md). Skills: `broker-api-contract`, `secrets-token-policy`,
> `risk-policy-guardian`, `state-machine-discipline`.

The adapter is a **thin, dumb boundary**: it normalizes, validates, and forwards. It contains **no strategy and
no risk-policy decisions** — those live in the risk engine (TZ §7). But the adapter is the **API boundary
guard**: it re-asserts the hard order rules (§3) so a bug upstream can never reach the broker.

---

## 1. Scope, modes & SDK

- **Broker = T-Invest (T-Bank) API only; market = MOEX Russian shares.** Sber/QUIK is phase 2, out of MVP. [LAW]
- **SDK / package:** **`t-tech-investments`** (grpc), installed from T-Bank's **GitLab simple-index**
  (`--index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple`), **NOT** public PyPI;
  legacy `tinkoff-investments` is quarantined. **Pin the exact version** at build time. *(resolved: ADR-0005 /
  research whq6u1gxe; latest 1.49.2 @ 2026-06-15 — a build dependency, not a runtime config key.)*
- **Modes (separate clients, separate tokens) — never cross.** [LAW: sandbox/live separation]

| `mode` | Adapter target | Token (env, §4) | Orders reach the real market? |
| --- | --- | --- | --- |
| `paper` | no broker trade calls; read-only market data only | reuse a read-only / sandbox-scoped token, or cached snapshots / MOEX ISS | no (internal simulator) |
| `sandbox` | T-Invest **Sandbox** services | `TINVEST_TOKEN_SANDBOX` | no (sandbox plumbing) |
| `confirm` | T-Invest **live** services, confirm flow | `TINVEST_TOKEN_LIVE_CONFIRM` | yes (guarded account) |
| `auto_small` | *architected, DISABLED in MVP* | `TINVEST_TOKEN_LIVE_AUTO_SMALL` (absent) | no — off [LAW] |

- **`paper` needs no broker trade account** — for read-only candles reuse a read-only/sandbox token or MOEX ISS;
  never require a trade-scoped token merely to read data (mirrors config §3). [LAW: least privilege]
- The adapter selects the sandbox vs live service surface **strictly by `mode`**; a `confirm`/`auto_small` token
  must never be handed to a sandbox client path or vice-versa (TZ §9 sandbox/live separation). [LAW]

## 2. Identifiers & instrument normalization (TZ §9)

- **`instrument_uid` is the primary identifier — never FIGI.** [verified, LAW-aligned] Every adapter call that
  takes an instrument uses `instrument_uid`; FIGI is not stored or passed (matches [db-schema.md](db-schema.md)
  `instrument_reference` PK).
- **Indices are non-tradable.** IMOEX/MCFTR (`instrument_kind=index`, `is_tradable=0`) are never order targets;
  index candles come from **MOEX ISS**, T-Invest gives index *last price* only *(resolved: ADR-0005 / whq6u1gxe)*.
- **Ticker/uid history:** the strategy keys on `instrument_uid`; renames (e.g. TCSG→T) are stitched in
  `instrument_reference.identifier_history` (source MOEX ISS, join on ISIN — see [tax-and-dividends.md](tax-and-dividends.md) §7).
  Whether a rename changes the T-Invest `instrument_uid` is **[verify]** (empirical at M1/M4).
- All money/price returned by the adapter is the T-Invest **Quotation** (`units` INTEGER + `nano` INTEGER,
  value = `units + nano/1e9`) — **never float**, never lossy. Convert to `Decimal` for research math only. The
  adapter stores/returns the raw pair so reconciliation/idempotency keep exact equality
  ([db-schema.md](db-schema.md) §1). [LAW: no float money]

## 3. Hard order rules at the API boundary (TZ §7.2, §9) — the normalizer guard [LAW]

The order normalizer is the **last gate before the wire**. It re-enforces the frozen hard rules so a bug in the
risk engine can never emit an unsafe order. These are **rejections, not warnings** — on any violation the
adapter raises and emits **no** broker call.

```text
ORDER NORMALIZER — invariants (reject → no PostOrder):
1. order_type == ORDER_TYPE_LIMIT  ALWAYS.
   - ORDER_TYPE_MARKET      -> REJECT (no market orders)            [LAW]
   - ORDER_TYPE_BESTPRICE   -> REJECT (best-price = a market order) [LAW]
   - any other order_type   -> REJECT
2. NEVER set confirmMarginTrade=true  (the field is hard-pinned false / never sent true) -> no margin   [LAW]
3. direction == ORDER_DIRECTION_BUY  OR  (SELL AND lots_to_sell <= held_lots_for_uid_on_account)
   - SELL exceeding held qty -> REJECT (long-only, no shorts)        [LAW]
   - the held-qty source is the reconciled position on config.account_id, not a cached guess
4. account_id == config.account_id  (exact match) -> else REJECT (account guard)  [LAW]
5. lots > 0  AND  lots is an integer count of LOTS (not shares); shares = lots * instrument.lot
6. price is a valid tick: price == round_to_tick(price)  (see §6) -> else REJECT
```

- **Normalizer output is `ORDER_TYPE_LIMIT` and only `ORDER_TYPE_LIMIT`.** It is structurally impossible for the
  adapter to construct a market/best-price/margin/short order. This mirrors [db-schema.md](db-schema.md) §2
  (`orders.type` CHECK admits only `LIMIT`) and config §2.7 (`order.type` const `LIMIT`).
- The frozen order-type enum tokens used here are the T-Invest constants. The **stored** value is the contract
  string `LIMIT` (db-schema); the **wire** value is `ORDER_TYPE_LIMIT`. Do not conflate or rename either.

```text
FROZEN T-INVEST ORDER CONSTANTS (referenced — emit only the first; reject the rest)
  ORDER_TYPE_LIMIT          # the ONLY type the adapter ever emits
  ORDER_TYPE_MARKET         # hard-rejected
  ORDER_TYPE_BESTPRICE      # hard-rejected (market-equivalent)
  ORDER_DIRECTION_BUY       # allowed
  ORDER_DIRECTION_SELL      # allowed ONLY for lots <= held (long-only close)
  confirmMarginTrade=false  # NEVER true
```

## 4. Token policy & startup scope check (TZ §9, §16) [LAW: token policy]

- **Per-mode tokens, loaded from env / secret store only** — never in code, config files, logs, dashboard, or
  Telegram. Env keys per [config-and-secrets.md](config-and-secrets.md) §1
  (`TINVEST_TOKEN_SANDBOX` / `TINVEST_TOKEN_LIVE_CONFIRM`; `..._AUTO_SMALL` absent — disabled). [LAW]
- Only the **active** mode's token is required at startup (a missing token for an inactive mode is not an error).
- Tokens live in memory only; **never echoed**. Log presence as a boolean (`token_loaded=true`), never the value.
  No request-header logging (TZ §16). [LAW]
- **Token lifetime = 3 months from last use (rolling, resets each call)** — operational note: keep tokens warm
  or rotate; the adapter does not auto-rotate. [verified]
- **Token scope kinds (referenced):** `read-only` / `full-access` / `sandbox` / **`account-scoped`**. [verified]

### 4.1 Startup scope check — BLOCK, never warn [LAW]

On startup, in `mode ∈ {sandbox, confirm}`, the adapter must run a scope check and **refuse to start**
(exit non-zero) if the token scope is missing / over-broad / unexpected for the mode:

```text
STARTUP SCOPE CHECK (mode in {sandbox, confirm}):
  - sandbox token must be a SANDBOX-scoped token; a live token in sandbox mode -> BLOCK (refuse to start)
  - confirm token must grant trade on the dedicated account; a read-only token in confirm mode -> BLOCK
  - wrong-mode token (sandbox token in live path, or vice-versa) -> BLOCK
  - over-broad token -> BLOCK *only when account-scoping IS available* for the bot account's
    product type (least privilege: prefer an account-scoped token). When account-scoping is NOT
    available (guard-only fallback, §4.2, decision 3), a full-access token is PERMITTED — it is the
    unavoidable residual — and the §5 account guard is the load-bearing control; the check then
    BLOCKS only on read-only / wrong-mode / sandbox-vs-live mismatch, NOT on over-broad.
  Result of a failed check: refuse to start (exit non-zero) + audit_journal event 'startup_scope_block'.
  NEVER a warn-and-continue.
```

This is **one consistent rule** with §4.2: full-access is allowed **iff** account-scoping is
unavailable for the bot account's product type. When scoping is available, prefer (and require) an
account-scoped token and BLOCK on over-broad; when scoping is unavailable, full-access is the permitted
fallback and the §5 account guard carries the confinement.

### 4.2 Account-scoped token vs account-guard-only — the fallback [owner-pending, decision 3]

Account-scoped tokens are the **preferred** defense (the token itself can only touch the one account), **but
they are not available for every product type**: T-Invest does **not** offer account scoping for
**Инвесткопилка / Счёт под ключ / Смарт-счёт**. [verified]

> **This contract does NOT assert that account scoping is available for the bot account.** Whether the dedicated
> bot account's product type supports account-scoped tokens is **[owner-pending / verify, empirical at M4]**.
> Document the **fallback**, do not pre-decide:

- **If account scoping IS available** for the bot account's product type → use an **account-scoped** live token;
  the startup scope check (§4.1) additionally requires the token be scoped to `config.account_id`.
- **If account scoping is NOT available** (product type unsupported) → **fall back to the `account_id` guard
  only** (§5): the token may be full-access, and the **only** thing that confines blast radius is the runtime
  account guard. In this fallback the scope check (§4.1) cannot assert account-scoping; it still BLOCKS on the
  read-only/wrong-mode conditions (over-broad is **permitted** here — full-access is the unavoidable residual),
  and the account guard (§5) becomes the **load-bearing** control.

The owner must record which case applies (and thus whether scope-check-for-account or guard-only is the live
posture) **before** the live confirm gate. Neither branch is asserted as the settled value here.

## 5. `account_id` guard (every order, every reconcile) (TZ §7.1, §9) [LAW]

The adapter enforces the account guard at the API boundary (in addition to config-load validation, config §3):

- **Refuse to start** in `sandbox`/`confirm`/`auto_small` if `account_id` is missing/blank, or if multiple broker
  accounts exist for the token and **none exactly matches** `config.account_id` (no "pick the first" fallback).
- **Show account name + id on startup** (log + dashboard status) for human verification.
- **Manual confirmation required on any `account_id` change** between runs.
- **Every** `PostOrder` / `CancelOrder` / position read asserts the target account `== config.account_id`
  (exact match) — at **submit AND at reconciliation** ([db-schema.md](db-schema.md) §4 row-level guard). [LAW]
- The bot trades **only** the one configured account; migration to the main account needs manual approval. [LAW]

## 6. Pre-order checks (TZ §7.1, §9) — run before EVERY order and on confirm-preflight

The adapter performs the broker-side pre-order checks; the risk engine performs policy checks (§7). All of these
must pass or the order is **not** submitted (the proposal/order is rejected with a recorded reason):

| # | Check | Rule | Source |
| --- | --- | --- | --- |
| 1 | **Tradable** | `instrument_reference.is_tradable = 1` AND instrument is a `share` (never an index) | [verified] |
| 2 | **Trading status** | `NORMAL_TRADING` only for **entries**; **`DEALER_NORMAL_TRADING` (=14) and all auction/closing-auction phases are EXCLUDED** | [verified] |
| 3 | **Last price** | fetch last/reference price for the premium ceiling + tick rounding | [verified] |
| 4 | **`min_price_increment`** | the instrument tick (Quotation units/nano); the limit price must be a valid tick (§6.1) | [verified] |
| 5 | **Lot size** | order is in **lots**; shares = `lots × instrument.lot`; reject non-integer lots | [verified] |
| 6 | **Pre-trade cost** | `GetOrderPrice` for limit-order pre-trade cost where the SDK exposes it (limit orders) | [verify §20] |

### 6.1 Trading-status eligibility (entries) [LAW: NORMAL_TRADING only]

```text
ENTRY-ELIGIBLE trading status:
  SECURITY_TRADING_STATUS_NORMAL_TRADING  -> eligible

NOT entry-eligible (exclude — new entries forbidden):
  SECURITY_TRADING_STATUS_DEALER_NORMAL_TRADING (= 14)   # dealer session, NOT main session
  ... opening auction / closing auction / discrete auction / break / not-available-for-trading ...
  (any non-NORMAL_TRADING phase)
```

- Entries are allowed **only** in `NORMAL_TRADING`; the dealer session (`DEALER_NORMAL_TRADING`, numeric **14**)
  and **all auction phases** are explicitly excluded (TZ §7.1, §9). This pairs with the frozen
  "no entries in weekend / evening / dealer sessions" rule. [LAW]
- **Exits** are governed by the risk engine, not blocked here: a protective exit is always allowed to be
  attempted (TZ §7.8, §8) — the adapter does not refuse a protective SELL on session grounds. The
  `NORMAL_TRADING`-only gate is an **entry** gate.
- The exhaustive `SecurityTradingStatus` enum member list (beyond the two pinned facts: `NORMAL_TRADING`
  eligible, `DEALER_NORMAL_TRADING=14` excluded) is **[verify §20]** against the live SDK enum before M4 ships;
  the **rule** (only `NORMAL_TRADING` admits entries; dealer + auctions excluded) is frozen regardless of the
  exact member spelling.

### 6.2 Tick rounding — DOWN for a buy (TZ §8) [LAW: max-entry-premium ceiling]

```text
round_to_tick(price, min_price_increment, side, is_protective_exit):
    # tick = min_price_increment as exact Quotation arithmetic (units/nano), NEVER float
    if side == BUY:                       floor price to the nearest tick   (round DOWN)
    elif side == SELL and is_protective_exit:  floor price to the nearest tick   (round DOWN)
    else (non-urgent target/limit SELL):  round to nearest valid tick
    # BUY rounds DOWN so the 0.20% max_entry_premium ceiling is NEVER exceeded by rounding.
    # A protective-exit SELL rounds DOWN (toward a more-fillable price) — symmetric to BUY-DOWN —
    # so rounding never lifts a stop/risk-exit limit UP and lowers fill probability on the path
    # that must not fail. Only non-urgent target/limit SELLs may round to nearest.
```

- The limit price is computed at reference + ≤ `order.max_entry_premium_pct` (0.20%, config §2.7) and then
  **rounded DOWN to a valid `min_price_increment` tick for a BUY** so rounding can never push the price above the
  premium ceiling (TZ §8). Rounding is exact Quotation arithmetic, **never float**. [LAW]
- A **protective-exit SELL** (stop-loss / risk exit) **rounds DOWN** toward a more-fillable price — symmetric to
  the BUY-DOWN rule — so tick rounding can never push a protective limit UP and reduce fill probability on the
  exit path that must not fail. Only **non-urgent target/limit SELLs** may round to nearest. [LAW]
- An order whose rounded price is not a multiple of `min_price_increment` is **rejected** (§3 rule 6).

## 7. Order lifecycle, idempotency & rate limits (TZ §8, §9) [LAW]

### 7.1 Idempotency — client `order_id` on every PostOrder [LAW]

- **Every `PostOrder` carries a client `order_id`** (the idempotency key) — this is the `orders.order_id` PK in
  [db-schema.md](db-schema.md) §3 (TEXT, client-generated, the PK). [verified]
- **A retry or restart must never double-submit.** The adapter dedupes by `order_id`: re-issuing the same
  `order_id` is idempotent (the broker treats it as the same order); the adapter never invents a new `order_id`
  for a retry of the same logical order. [LAW: no duplicate orders after restart/retry]
- **One order attempt per signal; no price chasing** (TZ §8): on an unfilled/expired order the adapter cancels
  (and, if partially filled, cancels the remainder and hands the filled qty to position management) — it does
  **not** resubmit a chased price. The "one attempt" rule is policy in the risk/execution layer; the adapter
  honors it by not auto-retrying with a new price.

### 7.2 TTL (TZ §8, frozen) [LAW]

- **Order TTL ~45 min (30–60)** (config `order.ttl_minutes`); unfilled → cancel; partially filled → cancel
  remainder + manage the filled position. TTL is wall-clock. The proposal/confirm button TTL is separate
  (config `button_ttl_minutes`, default 45) and is re-evaluated/expired across a restart (TZ §8). [LAW]

### 7.3 Rate limits & backoff (TZ §9) [LAW]

| Limit | Value | Source |
| --- | --- | --- |
| Total request rate | **≤ 50 req/s** (recommendation) | [verified] |
| `PostOrder` | **15/s (900/min)** | [verified] |
| `GetOrderPrice` / `GetTechAnalysis` per-method caps | unknown | **[verify §20]** |
| `PostOrderAsync` 600/min | not used (bot uses **sync** PostOrder) | **[verify §20]** |

- The adapter layers **reconnect / retry / exponential backoff** under these caps (TZ §9). [LAW: honor rate
  limits with backoff] Backoff/retry must remain **idempotent** (§7.1) — a backoff retry re-uses the same
  `order_id`, never a fresh one.
- Per-method caps for `GetOrderPrice` / `GetTechAnalysis` and the `PostOrderAsync` 600/min figure are
  **[verify §20]**; the bot uses **sync** `PostOrder` (15/s) so the async cap is moot unless that choice changes.

## 8. Reconciliation & state adoption (TZ §8) [LAW: startup reconciliation]

- **On startup/restart the adapter syncs positions + orders before any trading** (state-machine reconciliation,
  [db-schema.md](db-schema.md) `reconciliations`, `control_state`). [LAW]
- **Retry 3× (60 / 180 / 300 s); require 2 consecutive clean checks.** Persistent mismatch →
  `control_state.mode = blocked_reconciliation_mismatch` (TZ §8): **block new entries; monitoring on; RISK exits
  allowed; PROFIT/target exits FORBIDDEN; require a broker-confirmed position and no conflicting active orders;
  notify before AND after any exit attempt.** [LAW]
- **External/manual changes are adopted via reconciliation, not treated as errors** (TZ §8): a manual buy of an
  `approved` ticker → adopt + manage; a manual sell → update; deposit/withdraw → recompute limits; a manual
  position outside `approved` → the Telegram §10 prompt (`managed_only` / `approved` / ignore). The adapter
  surfaces broker truth; the state machine adopts it. [LAW]
- Reconciliation re-asserts the **account guard** (§5): every position/order synced must belong to
  `config.account_id`. [LAW]

## 9. Indicators, candles & no-lookahead (TZ §9, §13) [LAW: no intraday lookahead]

- **`GetTechAnalysis`** (server indicators: SMA/EMA/RSI/MACD/Bollinger; own `IndicatorInterval` enum) may be
  used **only on closed D1 candles** and **only if** it reproduces the locally-computed indicator values used in
  the backtest — **else compute indicators locally**. This preserves backtest/live parity and the no-lookahead
  invariant. [LAW] Whether the SDK exposes `GetTechAnalysis` is **[verify §20]**.
- **Candle history:** D1 deep (per-call window ~6y, limit 2400; true depth via
  `instrument_reference.first_1day_candle_date`). Seconds granularity is last-month only (not used; MVP = D1). [verified]
- **No-lookahead at the data edge:** the adapter only treats a D1 bar as final per `config.close_definition`
  ([config-and-secrets.md](config-and-secrets.md) §2.9, db-schema §4). The acted-on close is the **final** close
  (auction close via `GetClosePrices`/`OrderBook.close_price` for `close_definition=auction_close`); the adapter
  must not act on an intraday/partial bar. [LAW] Whether the evening session prints into the GetCandles D1 close
  is **[verify §20]** (empirical, M1/M4) — until verified, prefer `auction_close`.
- **Split-adjustment:** whether T-Invest D1 **share** candles already arrive split-adjusted is **[verify]**
  (empirical, before mixing with MOEX ISS split data — [tax-and-dividends.md](tax-and-dividends.md) §7).

## 10. Sandbox is plumbing, never proof (TZ §9) [LAW: sandbox ≠ proof]

- T-Invest **Sandbox** = connectivity/plumbing only: **simplified fills (no partial fills), fixed-style
  commission (flat ~0.05%), no taxes/dividends/full margin**. [verified]
- **Sandbox profitability is NEVER proof of edge or execution quality.** Sandbox drives the **M4/M5 fill-model
  parity check** (backtest-assumed fills vs sandbox/paper observed), not a profitability claim. [LAW]
- The flat 0.05% sandbox commission is a **plumbing artifact**, not the Трейдер tariff (also 0.05%) — cost
  realism always uses the configured `tariff` + slippage both sides + the min-commission floor
  ([config-and-secrets.md](config-and-secrets.md) §2.8). [LAW]

## 11. Frozen invariants honored (boundary re-assertion)

| Invariant (frozen-decisions.md) | How this adapter honors it |
| --- | --- |
| **Limit orders only — no market** | Normalizer emits only `ORDER_TYPE_LIMIT`; `MARKET`/`BESTPRICE` hard-rejected (§3) |
| **No margin** | `confirmMarginTrade` never set true — structurally absent (§3) |
| **No shorts / long-only** | SELL rejected when `lots > held` on `config.account_id` (§3, §5) |
| **Dedicated account + account guard** | Refuse-to-start guard + per-call exact-match assertion at submit & reconcile (§5) |
| **Per-mode tokens, env only, never logged** | §4 token policy; presence logged as boolean, value never echoed |
| **Startup scope check BLOCKS** | §4.1 refuse-to-start on missing/over-broad/wrong-mode scope |
| **`instrument_uid`, not FIGI** | §2 — uid is the only instrument key the adapter passes/stores |
| **Pre-order `NORMAL_TRADING` only; exclude dealer + auction** | §6, §6.1 — entries gated; `DEALER_NORMAL_TRADING(14)` + auctions excluded |
| **Tick rounding / lot checks** | §6.1, §6.2 — exact-Quotation tick rounding (DOWN for buy AND protective-exit SELL), lot integer check |
| **Client `order_id` idempotency; no double-submit** | §7.1 — `order_id` PK reused on retry/restart |
| **Rate limits with backoff (≤50/s; PostOrder 15/s)** | §7.3 — caps + reconnect/retry/exponential backoff |
| **Startup reconciliation; adopt external changes** | §8 — 3× retry, 2 clean checks, `blocked_reconciliation_mismatch` semantics |
| **No intraday lookahead; backtest/live parity** | §9 — closed-D1 indicators only, final-close gating, local-compute parity |
| **`kill` cancels orders, never sells** | §12 — adapter cancels active orders on kill; emits no SELL |
| **Sandbox ≠ proof** | §10 — fill-parity check only, never a profitability claim |
| **Money never float** | §2 — Quotation units/nano end-to-end |

## 12. Control verbs at the boundary (TZ §7.8, §8, §10) [LAW]

- **`kill`** → adapter **cancels active orders only** (idempotent `CancelOrder` per open `order_id`) and stops
  the bot; it issues **no SELL** and never liquidates a position. A kill switch must never itself dump the
  portfolio. [LAW]
- **`pause`** → block new entries; keep monitoring + protective exits active. **`resume`** → extra confirm +
  preflight (re-run the §6 pre-order checks and reconciliation before resuming). [LAW]
- These verbs are control-plane (Telegram §10); the adapter just executes the resulting cancel/no-op — it does
  not decide them.

## 13. Open questions / owner-pending (raise before the relevant code ships)

- **[owner-pending / verify §20]** Bot-account product type → does it support **account-scoped tokens**? Decides
  scope-check-for-account vs **account-guard-only fallback** (§4.2, decision 3). *Not asserted here — document
  the fallback; the guard (§5) is load-bearing if scoping is unavailable.* Empirical at M4.
- **[verify §20]** Does the SDK expose **`GetOrderPrice`** (and does it cover market orders — moot, limit-only)?
  Pre-trade cost check (§6 row 6) depends on it.
- **[verify §20]** Does the SDK expose **`GetTechAnalysis`** (and does it reproduce local indicator values)?
  Else compute indicators locally (§9).
- **[verify §20]** Per-method rate caps for `GetOrderPrice` / `GetTechAnalysis`; `PostOrderAsync` 600/min
  (bot uses sync `PostOrder` 15/s — async moot unless that choice changes) (§7.3).
- **[verify §20]** The exhaustive `SecurityTradingStatus` enum member list (beyond `NORMAL_TRADING` eligible and
  `DEALER_NORMAL_TRADING=14` excluded) (§6.1) — the **rule** is frozen; the member spelling is to confirm.
- **[verify §20]** Does the **evening session** print into the T-Invest GetCandles D1 `close`? Pins the
  `close_definition` no-lookahead surface (§9; config §2.9). Until verified, prefer `auction_close`.
- **[verify]** Are T-Invest D1 **share** candles already split-adjusted (before mixing with MOEX ISS splits)?
  Does a **rename** change the T-Invest `instrument_uid` (ISIN as the stable join key)? (§2, §9). Empirical M1/M4.
- **[owner-pending]** Secret-storage backend (Windows local vs VPS secret store) — decided before live (M6);
  the contract is "env / `.env` locally" (config §1).

## 14. Cross-references

- Frozen LAW: `docs/frozen-decisions.md` (limit-only / no-margin / no-shorts, account guard, token policy,
  idempotency + rate limits, reconciliation, kill semantics, no-lookahead, sandbox ≠ proof).
- Spec: `docs/TZ.md` §9 (adapter, 2026-verified), §7 (risk engine, §7.1 pre-checks, §7.2 hard order rules,
  §7.8 controls), §8 (order/position state machine & execution), §16 (security), §20 (verified + must-verify).
- Schema: [db-schema.md](db-schema.md) (`orders`/`fills`/`positions`/`reconciliations`/`control_state` enums,
  Quotation type rule, account-guard row-level). Config: [config-and-secrets.md](config-and-secrets.md)
  (per-mode tokens §1, account guard §3, order/rate/tick keys §2.7, close_definition §2.9).
  Taxes: [tax-and-dividends.md](tax-and-dividends.md) (dividends source, splits/renames, dividend-gap block).
- Skills: `broker-api-contract`, `secrets-token-policy`, `risk-policy-guardian`, `state-machine-discipline`.
  Auditors: `risk-invariant-auditor`, `lookahead-auditor`.
