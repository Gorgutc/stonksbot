# Contract — Testing strategy (TZ §18)

> **Status:** M0 contract, with the first pytest suite now present. This pins the
> **shape and minimum required coverage** of the test suite as later component
> profiles add implementation. **`docs/frozen-decisions.md` 🔒 wins** on any conflict; tests **encode**
> the frozen invariants, they never relax them. **[owner-pending]** = a value the owner/build must confirm
> (do not silently fix it). **[verify]** = depends on an empirical/integration fact still open.
>
> Stack (TZ §18, §1): **pytest** + **hypothesis** (property-based), pure dependency-injected code under test;
> the two read-only PR gates `lookahead-auditor` + `risk-invariant-auditor`. Pairs with
> [db-schema.md](db-schema.md) (enum/type parity), [config-and-secrets.md](config-and-secrets.md)
> (cost/risk keys under test), [tax-and-dividends.md](tax-and-dividends.md) (the НДФЛ fixture).

This contract feeds: the M0+ test layout, the M2 tax fixture, the M3 walk-forward / cost-sensitivity evidence,
and the per-PR auditor gates. Current project verify commands are defined in `.agent-kit.json` (see §9).

---

## 1. Test pyramid (TZ §18)

Four layers, fast→slow, all on **pure, dependency-injected** code (the broker/clock/DB are injected so units
never touch the network). Mirrors TZ §18: unit → integration → e2e, plus a cross-cutting **honesty** band.

| Layer | Scope | What is real vs injected | Marker (pytest) | Where it runs |
| --- | --- | --- | --- | --- |
| **L1 unit** | strategy, risk engine, state machine, sizing, FIFO/tax math | everything injected (no I/O); fixed candle arrays in | `unit` (default) | every commit / `verify.fast` |
| **L2 integration** | T-Invest adapter, data layer, DB DDL apply + queries | broker = **mock/recorded**; DB = real ephemeral SQLite | `integration` | `verify.deep` |
| **L3 e2e** | full **confirm cycle** in `paper` mode (signal → proposal → confirm → order → fill → position → exit) | clock + broker + Telegram = fakes; DB real; **paper mode only** | `e2e` | `verify.deep` / nightly |
| **H honesty** | cross-cutting: no-lookahead, fills, costs, tax, walk-forward | see §3 | `honesty` | `verify.deep` + M3 evidence gate |
| **P property** | hypothesis-generated invariants (§4) | generators + pure code under test | `property` | `verify.deep` |

Rules:
- **Pyramid weight:** the bulk of assertions live in **L1** (pure, deterministic, millisecond-fast); L2/L3 are
  thin plumbing checks, **not** where strategy/risk correctness is proven.
- **No network in L1/unit ever.** A test that needs the live or sandbox broker is L2+ and must be marked and
  skippable offline (`@pytest.mark.integration` gated on a token/env flag — never fail CI for a missing token).
- **Determinism:** every test pins a clock and a seed; **no wall-clock `now()`, no real `sleep`, no real RNG**
  in the code under test (inject them). A flaky timing-dependent test is a contract violation, not a retry.
- **`paper` mode for e2e:** the confirm-cycle e2e runs in **`mode=paper`** (no broker account, no real order) —
  see [config-and-secrets.md](config-and-secrets.md) §2.1/§3. Sandbox plumbing is exercised in L2 only.

## 2. Layer detail

### 2.1 L1 unit — pure & dependency-injected (TZ §18, §6, §8)
Covers the modules where the expensive bugs live (frozen-decisions.md, "Strategy, data & backtest honesty" (state machine row) — "bugs live in state transitions"):
- **Strategy** (TZ §6): MA20/MA50, pullback band, r/r, signal **only after the D1 close** (the temporal rule is
  asserted here AND in §3 H1).
- **Risk engine** (TZ §7, frozen-decisions.md, "Order & risk rules"): every limit in [config-and-secrets.md](config-and-secrets.md)
  §2.5 — `max_open_positions=1`, `max_position_rub=3_000`, `max_position_pct=30`, `cash_reserve_pct=50`,
  `daily_hard_stop_rub=100`, `max_proposals_per_day=1`, `hard_stop_pct≈4`, market-regime filter, re-entry
  cooldown, dividend-gap block. **Long-only / no-shorts:** assert a `sell` is rejected/clamped when it exceeds
  held qty (never stored as a short — db-schema note on `orders`).
- **State machine** (TZ §8, db-schema §2): every legal transition over the frozen enums
  `proposals.state` {`awaiting_confirmation`,`confirmed`,`rejected`,`expired`},
  `orders.state` {`submitted`,`partially_filled`,`filled`,`cancel_requested`,`cancelled`,`reconcile_required`},
  `positions.state` {`open`,`closed`}, and **illegal transitions are rejected**.
- **Sizing / FIFO / tax math** (TZ §12.1): Quotation `units`/`nano` arithmetic with **no float** (db-schema §1).

### 2.2 L2 integration — adapter vs mocks (TZ §18, §11)
- **Broker adapter** against a **mock / recorded-response** T-Invest client: `instrument_uid` (not FIGI),
  pre-order `min_price_increment`/lot checks, **client `order_id` idempotency** (a retried submit with the same
  `order_id` creates **no** duplicate — db-schema §4, frozen-decisions.md, "Order & risk rules" (idempotency/order_id row)), rate-limit backoff, reconciliation.
- **Limit-only at the adapter edge:** assert a market/bestprice order is **rejected before submit** (it is not
  even storable — `orders.type` CHECK = `LIMIT`, db-schema §2).
- **DB layer:** apply the db-schema DDL to an ephemeral SQLite file; assert CHECK constraints reject
  out-of-vocabulary enums and float money is never written; `audit_journal` UPDATE/DELETE triggers fire.
- **Account guard (L2):** with `mode ∈ {sandbox,confirm}`, a missing/mismatched `account_id` **refuses to start**
  (config-and-secrets §3, §3.1) — assert the non-zero exit, not a warning.

### 2.3 L3 e2e — full confirm cycle in paper (TZ §18, §8, §9)
One happy-path and the key failure paths through the **whole** state machine, in `paper`:
- happy: signal → `selected` → proposal `awaiting_confirmation` → owner confirm → order `submitted` → fill →
  position `open` → protective exit → position `closed` (`close_reason ∈` db-schema §2 vocab).
- TTL expiry: proposal `expired` after `button_ttl_minutes`; **no order**.
- partial fill then TTL: cancel remainder, manage the filled position (frozen-decisions.md, "Order & risk rules" (order TTL row)).
- **`kill`**: stops the bot + cancels active orders but **does NOT sell positions** (frozen-decisions.md, "Order & risk rules" (kill/pause row)) —
  an e2e assertion that no `sell` order is emitted by `kill`; `control_state.mode='killed'` persists a restart.
- startup **reconciliation**: a manual/external position is **adopted** (`positions.source='manual_adopted'`),
  not treated as an error (frozen-decisions.md, "Strategy, data & backtest honesty" (state machine row), state-machine-discipline).

## 3. Honesty-test catalog [LAW: backtest-honesty] (TZ §13, §18; frozen-decisions.md, "Strategy, data & backtest honesty")

The honesty band is the heart of this contract — each row is a **required** test, traced to a frozen line. The
backtest is "honest" only if **all** pass; a single backtest or sandbox run is **never** proof (frozen-decisions.md,
"Strategy, data & backtest honesty" (anti-overfitting row)).

| ID | Honesty invariant | Required assertion | Frozen source |
| --- | --- | --- | --- |
| **H1** | **No intraday lookahead** | Signal is computed **only from bars with `is_complete=1`** as-of the decision `ts`; entry fills **no earlier than the next session**. A test feeds a series where "peeking" at the same-day close would change the decision and asserts the decision is **unchanged**. | frozen-decisions.md, "Strategy, data & backtest honesty" (no-lookahead row), db-schema §4 |
| **H2** | **Conservative both-side fills** | A **limit buy fills only if the order's TTL-window low ≤ limit**; otherwise **no trade** (not a magic close fill). With only D1 OHLC available, the fallback is `D+1.open <= limit` else unfilled; whole-day `D+1.low` cannot prove a ~45-minute live order fill. | frozen-decisions.md, "Strategy, data & backtest honesty" (conservative both-side fills row) |
| **H3** | **Costs both sides** | Round-trip applies commission **both sides** + slippage buffer per the configured `tariff`: `investor` 0.30%/side (`costs.investor_commission_bps=30`) or `trader` 0.05%/side + **`costs.trader_monthly_fee_rub=390`/mo**; plus `costs.slippage_bps=10`/side (BACKTEST only) and the `costs.min_commission_units`/`costs.min_commission_nano` (Quotation pair) 0.01 ₽ floor. ≈0.80% round trip on `investor`. | frozen-decisions.md, "Strategy, data & backtest honesty" (conservative both-side fills row), config §2.8 |
| **H3a** | **390 ₽/mo monthly fee is modeled** | On `tariff=trader`, the **390 ₽ monthly fee is charged in the backtest PnL** (amortized per the model), so the trader tariff is not silently cheaper than reality (TZ §20: Трейдер 0.05%/side **+390 ₽/mo**). A test asserts the fee line appears in Layer-B for `trader` and is **absent** for `investor`. | config §2.8, TZ §20 |
| **H4** | **НДФЛ tax fixture** | The after-tax (Layer-B) implementation reproduces the **hand-computed** worked example in [tax-and-dividends.md](tax-and-dividends.md) §8 (FIFO cost basis, commission in base, flat 13% at the pilot, realize-on-close), to the exact Quotation `units`/`nano` — **no broker/sandbox reference exists for tax** (tax §8). | tax §2 (NDFL on realized gains) + §8 (worked fixture); frozen-decisions.md, "Strategy, data & backtest honesty" (anti-overfitting row) |
| **H5** | **Slippage not double-counted (live)** | LIVE Layer-B derives cost from **actual fill price + actual commission** with **no separate slippage line**; only BACKTEST adds `slippage_bps`. A test asserts a live-path PnL has no slippage line and a backtest-path PnL does. | tax §1, config §2.8 |
| **H6** | **Walk-forward over global fit** | Params are fit on the **train window only** and validated **out-of-sample**; a test/eval rejects a config whose only support is a single in-sample fit. The M3 artifact `docs/evidence/walk-forward-latest.md` is the evidence-gate target (`.agent-kit.json` evidenceGates). | frozen-decisions.md, "Strategy, data & backtest honesty" (anti-overfitting row), TZ §13 (validation metrics) / §14 (STOP gate) / §18, config |
| **H7** | **Cost-sensitivity is reported** | The backtest report carries cost-sensitivity (commission/slippage swept) so the STOP gate "edge dies after costs → stop" (TZ §14) is checkable; a test asserts the metric is present and the gate is enforced. | TZ §13 (validation metrics) / §14 (STOP gate) / §18 |
| **H8** | **Data truth → skip, not trade** | When `instrument_reference.data_status='data_conflict'` (close divergence > `data_conflict.close_divergence_pct`), the engine **skips entry** (`signals.decision='skipped'`, reason `data_conflict`) and **never** skips a protective **exit**. | frozen-decisions.md, "Strategy, data & backtest honesty" (data truth row), db-schema §2/§4 |
| **H9** | **Managed registry — engine never self-promotes** | The engine **NEVER** sets `instrument_reference.whitelist_status='approved'` itself: at most it may write `whitelist_status='pending'` (a monthly-review **proposal** that needs owner confirm); a test asserts an attempt to auto-approve is rejected. **And** a `signal`/`order` for any ticker whose `whitelist_status != 'approved'` is **rejected** (not silently traded) — growing the trading universe must not silently grow risk. | frozen-decisions.md, "Strategy, data & backtest honesty" (managed registry row), db-schema §2 (enum table) |

> **Required validation metrics (TZ §13 (validation metrics) / §14 (STOP gate) / §18):** the walk-forward artifact reports out-of-sample equity (Layer-B),
> max drawdown, hit rate, turnover, exposure, and **cost-sensitivity**; a test asserts each metric exists in the
> artifact so "the gate read Layer B for the verdict" (tax §1) is mechanically true.

## 4. Property-based invariants (hypothesis) (TZ §18)

For **any** generated valid D1 candle series (+ generated config within frozen bounds), the following hold. Each
is a hypothesis `@given` strategy over `units`/`nano` candle arrays; shrinking must surface the minimal failing
series. Generators **never** emit floats for money and **never** emit out-of-vocabulary enums.

```text
# Generators (hypothesis strategies)
gen_candle_series   : list of D1 OHLCV with units/nano money, monotone ts, high≥max(open,close)≥min(open,close)≥low, volume≥0
gen_config          : config within frozen bounds (risk.* / strategy.* / costs.* from config-and-secrets §2)

# Property invariants (must hold for ALL generated inputs)
P1  no-lookahead       : strategy_decision(series[:i]) is independent of series[i:]  (only is_complete bars used)
P2  long-only          : engine never emits a sell exceeding held qty; net position qty ≥ 0 at all times
P3  one-position       : open positions ≤ risk.max_open_positions (=1)
P4  one-proposal/day   : proposals created per calendar day ≤ risk.max_proposals_per_day (=1)
P5  sizing-cap         : any proposed order value ≤ min(max_position_rub, max_position_pct% of capital) AND leaves ≥ cash_reserve_pct% cash
P6  limit-only         : every emitted order has type=LIMIT (never market/bestprice)
P7  costs-monotone     : round-trip net PnL is monotone non-increasing as commission_bps or slippage_bps increase
P8  no-float-money     : every money value round-trips through Quotation units/nano with exact equality (no float drift)
P9  fill-conservatism  : a limit buy is filled  <=>  TTL-window low ≤ limit; D1-only data fills only when D+1 open ≤ limit
P10 state-legality     : every state transition emitted is in the frozen transition set (proposals/orders/positions); idempotent re-apply is a no-op
P11 kill-safety        : applying `kill` to any generated open-position state emits zero sell orders
```

> P1, P2, P5, P6, P10, P11 are **frozen-LAW** invariants restated as properties — a failing example is a frozen
> violation, not a flaky test. P7/P9 encode the honesty model (§3 H2/H3).

## 5. Per-PR auditor gates (TZ §18; frozen-decisions enforcers)

Two **read-only** subagent gates run on **every PR** that touches a guarded surface (mirrors AGENTS.md
Subagents + the `lookahead-auditor` / `risk-invariant-auditor` contracts):

| Gate | Fires when a PR touches | Verdict it returns | Blocks merge on |
| --- | --- | --- | --- |
| `lookahead-auditor` | `**/strategy/**`, `**/backtest/**`, `**/signals/**`, data/close-definition code | PASS / FAIL + evidence | any lookahead (H1/P1), non-conservative fill (H2), under-modeled cost incl. 390 ₽/mo (H3/H3a), missing walk-forward/cost-sensitivity (H6/H7) |
| `risk-invariant-auditor` | sizing, limits, order construction, account/mode, `kill`/`pause` | PASS / FAIL + evidence | any breach of limit-only, no-margin/shorts, account guard, portfolio limits, `kill` never-sells (P2/P3/P5/P6/P11) |

- **Result contract (AGENTS.md):** each gate returns **PASS / FAIL + evidence + explicit defers**; a *defer*
  must never silently override a **blocker** (a FAIL is unsafe-to-ship regardless of defers).
- **Evidence gate (machine):** in addition to the human/agent gates, `.agent-kit.json` `evidenceGates` requires
  `docs/evidence/walk-forward-latest.md` when `**/strategy/**`/`**/backtest/**`/`**/signals/**` change — this
  fires only **after** `tools/install-hooks.mjs` is installed and the surface exists.
- These gates are **review/agent** guards now for the M0 skeleton; they become
  deeper wired tests as each guarded surface lands. A defer never overrides a
  blocker.

## 6. Test data & fixtures
- **Fixtures are committed, deterministic, and hand-checkable.** The М2 НДФЛ fixture (tax §8) and small candle
  arrays for H1/H2 are checked into the repo, **not** generated at run time (property tests generate; honesty
  fixtures are pinned).
- **No real tokens in fixtures or recordings** [LAW: token policy] — recorded broker responses are scrubbed of
  any token; the secret-scan gate (config §5) treats a committed token as a leak.
- **No real `account_id` in committed fixtures** — use an obvious placeholder; the account guard is tested with a
  fake id.
- **Quotation everywhere:** fixtures store money as `units`/`nano` integer pairs (db-schema §1), never float —
  including the rounded НДФЛ as `cash_events(type='tax')` units=<rubles>, nano=0 (tax §8).

## 7. Coverage policy
- **Coverage targets are [owner-pending]** — set the threshold at M0 alongside the verify commands (§9). The
  **contractual** requirement is **behavioral, not a %**: every frozen invariant in §3 and §4 has at least one
  test, and every `signals.decision` / `orders.state` / `positions.close_reason` enum value (db-schema §2) is
  exercised at least once.
- A raw line-coverage number is **not** accepted as proof of honesty — H6 (walk-forward) and the auditor gates
  are the real bar (sandbox/coverage ≠ proof).

## 8. Frozen invariants honored
- **No-lookahead** — H1 + P1 assert signals use only `is_complete` bars as-of `ts`; entry next session (frozen-decisions.md, "Strategy, data & backtest honesty" (no-lookahead row)).
- **Conservative both-side fills** — H2 + P9: limit buy fills only if the order's TTL-window low reaches the
  limit; D1-only fallback fills only at `D+1.open <= limit`; unfilled = no trade (frozen-decisions.md,
  "Strategy, data & backtest honesty" (conservative both-side fills row)).
- **Costs both sides incl. 390 ₽/mo** — H3 + H3a: commission both sides + slippage (backtest) + the **390 ₽/mo**
  trader fee, per the configured `tariff`; min-commission floor (frozen-decisions.md, "Strategy, data & backtest honesty" (conservative both-side fills row), config §2.8, TZ §20).
- **Honest hand-computed tax fixtures** — H4: Layer-B reproduces the worked НДФЛ example to exact Quotation
  units/nano; tax has no sandbox reference (tax §8).
- **LLM never trades** — tests assert decisions come only from formal strategy + risk rules; **no test injects an
  LLM into the decision path** (frozen-decisions.md, "Scope & product shape" (LLM-never-trades row)).
- **Sandbox ≠ proof** — §7: coverage/sandbox profitability is never accepted as proof; H6 walk-forward + the
  auditor gates are the bar (frozen-decisions.md, "Strategy, data & backtest honesty" (anti-overfitting row);
  "Known drift / owner decisions pending" (sandbox-not-live row)).
- **Limit-only / no-shorts / one-position / kill-never-sells** — P2/P3/P6/P11 + L2/L3 + the
  `risk-invariant-auditor` gate (frozen-decisions.md, "Order & risk rules" (limit-only and kill/pause rows)).
- **Managed registry — engine never self-promotes** — H9: the engine never sets `whitelist_status='approved'`
  itself (at most `pending`, owner confirms); a `signal`/`order` for a non-`approved` ticker is rejected
  (frozen-decisions.md, "Strategy, data & backtest honesty" (managed registry row), db-schema §2).

## 9. Open questions / owner-pending
- **`verify.*` commands** — current M0 commands are wired in `.agent-kit.json`:
  `verify.fast = "ruff check . && pytest -q"`, `verify.deep = "pytest"`, and
  `verify.ship = "pytest --maxfail=1 -q"`.
- **Coverage threshold** (§7) — set at M0 with the verify commands; no % asserted here. **[owner-pending]**
- **NDFL rounding direction** in the fixture (math-round vs floor) and partial-lot FIFO split — pinned in the M2
  fixture (tax §8, §9). **[verify]**
- **`tariff`** (investor vs trader) drives which cost rows H3/H3a assert — finalized at M3 cost-sensitivity
  (config §2.8, §6). **[owner-pending]**
- **`close_definition`** is ratified as `auction_close`; H1 uses the auction-close source. The evening-session
  effect on provider D1 candles remains an empirical data-layer check, not an H1 blocker. **[verify]**
- **Recorded-vs-live broker test mode** — whether L2 uses recorded cassettes or a live sandbox token, and the
  precise sandbox fill/commission semantics, is empirical at M4 (config §2.8 sandbox note, TZ §20). **[verify]**
- **`max_holding_days`** ({20,40}) — the H/property time-exit tests pin to whichever value is chosen at M3
  (config §2.6). **[owner-pending]**

## 10. Cross-references
- Spec `docs/TZ.md` §18 (testing), §13 (backtest cost/tax model + validation metrics), §14 (STOP gate), §6–§9
  (strategy/risk/state/execution), §20 (API facts incl. tariffs + 390 ₽/mo).
- Frozen LAW `docs/frozen-decisions.md`: "Strategy, data & backtest honesty" (no-lookahead, conservative both-side
  fills/costs, anti-overfitting, data truth, managed registry, state machine rows); "Order & risk rules"
  (limit-only, kill/pause rows); "Scope & product shape" (LLM-never-trades row); "Known drift / owner decisions
  pending" (sandbox-not-live row).
- Contracts: [db-schema.md](db-schema.md) (enum/type parity under test), [config-and-secrets.md](config-and-secrets.md)
  (cost/risk keys), [tax-and-dividends.md](tax-and-dividends.md) (the НДФЛ fixture, two-layer PnL).
- Skills: `backtest-honesty`, `state-machine-discipline`. Auditors: `lookahead-auditor`, `risk-invariant-auditor`.
