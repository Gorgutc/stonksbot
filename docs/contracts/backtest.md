# Contract — Backtest honesty & cost model (TZ §13, §14)

> **Status:** M0 contract, **resolved on paper (no backtest code yet)**. This pins the **honest fill model**,
> the **both-side cost model**, the **metrics** (incl. Deflated Sharpe + cost-sensitivity), the **benchmarks**,
> and the **PASS/WEAK/FAIL gate** the M2 `backtest/` module implements verbatim, then M3 finalizes with the
> **binding tariff**. **`docs/frozen-decisions.md` 🔒 wins** on any conflict — values marked **[LAW]** mirror a
> frozen invariant (pinned) and may not be changed here (only via owner decision + ADR + the same-change rule).
> **[owner-pending]** = a value the owner must pin (do not silently fix it). **[verify]** / **[verify M3]** = an
> empirical fact still to be measured (specify the structure now, never assert the value).
> Pairs with [strategy.md](strategy.md) (the entry rule this fill model mirrors), [db-schema.md](db-schema.md)
> (`candles`/`signals`/`fills`/`cash_events` enums + Quotation units/nano), [config-and-secrets.md](config-and-secrets.md)
> §2.8 (cost keys), and [tax-and-dividends.md](tax-and-dividends.md) (the Layer-B tax/dividend hook).

This contract owns the **honest research/validation loop only** — "would this rule have survived real costs and
honest fills?". It does **not** decide buy/sell (the strategy emits candidates; the LLM never trades [LAW]) and it
does **not** place live orders (execution owns that, TZ §8). A single backtest or sandbox run is **never** proof of
edge [LAW]; the gate verdict (§7) reads the **after-tax Layer-B** number under the §2 fill model + §3 costs.

---

## 1. Scope, parity & the no-lookahead spine (TZ §13)

1.1 **Inputs.** D1 `candles` (`is_complete=1` only, db-schema §4) for the `approved` universe + the index series
(IMOEX price, MCFTR total-return — `instrument_kind='index'`, `source='moex_iss'`, db-schema §3.1), the
`dividends` calendar (tax §5), and the pinned config (`strategy.*`, `risk.*`, `costs.*`, `tariff`, `benchmarks`).
History = **3 years D1** for `approved` + index, **plus ~100 warm-up leading bars** (TZ §13) so the first tradable
day is not starved (matches strategy §3.2 warm-up `max(ma_slow, local_high_lookback)`).

1.2 **Backtest/live parity [LAW].** The backtest calls the **same** pure `generate_signals` function the live bot
calls (strategy §2); indicators are computed **locally** (NOT broker `GetTechAnalysis`) so backtest and live are
bit-identical (strategy §3.2–3.3). The backtest never re-implements strategy logic — a divergent re-implementation
is a contract violation.

1.3 **No intraday lookahead [LAW].** Per-day loop, strictly causal:
- On day `D` the model consumes only bars with `ts ≤ decision_ts` where `decision_ts` = the **final** D1 close of
  `D` (`is_complete=1`, sourced per `config.close_definition`, config §2.9). `generate_signals(decision_ts=…)`
  may **never** read a bar with `ts > decision_ts`.
- A signal computed on `D`'s close produces an order intent for the **next** session `D+1` — entry is evaluated
  against `D+1`'s OHLC, never `D`'s. **Same-day fill of a same-close signal is forbidden** (the canonical lookahead
  bug). *(TZ §13; frozen-decisions.md, "Strategy, data & backtest honesty" (no-lookahead row); mirrors strategy §3.1.)*
- A dividend/ex-date may gate an entry only if known as-of `decision_ts` (`declared_date`/`as_of ≤ decision_ts`,
  tax §6) — no peeking at a later-declared dividend.

## 2. Honest fill model — both sides conservative [LAW] (TZ §13)

> **The fill rules MUST MIRROR the strategy entry rule (strategy §5 (entry rule, step 7)):** the entry reference is the `entry_ref`
> the strategy emits (default = the `decision_ts` close); the risk engine applies `order.max_entry_premium_pct`
> (0.20%) and **rounds the tick DOWN for a buy** (config §2.7, TZ §8). The backtest uses that **same** rounded
> limit price — never "bought at the close" or "bought at the magic target".

### 2.1 Entry fill (limit buy, next session) [LAW]
```text
premium_ceiling = entry_ref × (1 + order.max_entry_premium_pct/100)
entry_limit = round_tick_down( premium_ceiling, min_price_increment )  # buy limit; premium ceiling never exceeded
FILLED  in the D+1 order TTL window  IFF  the observed trade low  ≤  entry_limit
fill_price = entry_limit                                               # a limit never fills better than its price in this model
UNFILLED  ⇒  NO TRADE   (one order attempt per signal; no price chasing; no carry to D+2) [LAW]
```
- **One attempt, one session.** Mirrors execution TTL "single attempt/signal; unfilled → cancel" (TZ §8,
  frozen-decisions.md, "Order & risk rules" (order TTL row)): if the order is not filled inside the configured
  `order.ttl_minutes` window on `D+1`, the signal is **dropped** — it is **not** retried on `D+2` and the limit is
  **not** re-priced. `UNFILLED = no trade`, recorded as such (no `fills` row, no position).
- **Required intraday evidence for TTL parity:** an M2 backtest may fill from the day's low only if it has
  intraday/market-data evidence that the low occurred inside the first `order.ttl_minutes` after next-session
  submission. If only D1 OHLC is available, the pessimistic fallback is:
  `FILLED iff D+1.open <= entry_limit`, else `UNFILLED`. Whole-day `D+1.low` is forbidden as a D1-only fill
  proxy because the live order expires after ~45 minutes.
- **Gap-down entry conservatism:** if `D+1` gaps below the limit (`open < entry_limit`), still fill at
  `entry_limit` (the limit, not the better open) — this model **never** awards a price better than the posted
  limit. (Refusing the windfall keeps the model pessimistic, the honest direction.)
- The limit price uses the strategy's `entry_ref` (NOT the close of `D+1`, which would be lookahead on the entry).

### 2.2 Exit fills — modeled just as conservatively [LAW]
Exits are owned by the risk engine (strategy §8; `positions.close_reason ∈ {risk, trend, target_trailing, time,
manual}`, db-schema §2). The backtest models each exit honestly on the **next** day's OHLC after the exit
condition is detected on a **final D1 close** (no intraday):

```text
# Take-profit (target_trailing, profit side) — a SELL limit at the target:
TP_FILLED  IFF  candle.high ≥ target_price        # the day's HIGH must reach the target
tp_fill_price = target_price                       # never better than the posted target

# Hard stop (~hard_stop_pct, 4%) and trend-break (close < MA50) — STOP/MA-break exits:
#   detected on a final D1 close (trend) or as a stop level (risk); executed next session.
#   If price GAPS through the level, fill at the WORSE gap price, not the exact level:
stop_fill_price = min( stop_level, candle.open )   # gap-down fills at the gap open (worse), long exit
                                                   # (a buy-back would be max(level, open); long-only ⇒ sells)

# Trailing stop (after TP armed): close < MA20 (trend_support_ma) OR 3% trail — same gap-worse rule.
# Time exit (held ≥ max_holding_days): exit at next session; model as a stop-style exit at next open
#   (conservative: the worse of the reference close and the realized next open).
```
- **No asymmetric optimism [LAW].** Entry conservatism (low must reach the limit) and exit conservatism (high must
  reach the target; stops gap to the worse price) are **symmetric**. Filling a TP at the exact target on a day that
  merely closed near it, or a stop at the exact level through a gap, is the exit-side twin of entry lookahead and is
  **forbidden**.
- **Same-bar ambiguity is resolved against the strategy [LAW].** If entry and an exit trigger are both possible
  inside the same bar/window and the data cannot prove ordering, use the conservative ordering: no same-bar TP
  credit; a same-bar hard stop/risk exit may fire; if stop and target both touch in an ambiguous bar, the stop or
  otherwise worse executable price wins. With D1-only data, do not claim an entry+TP round trip from the same
  candle.
- **Protective exits always model, even on degraded data [LAW].** A `data_conflict`/stale bar blocks a new **entry**
  (§1.3, strategy §3.1) but **never** blocks modeling a protective **exit** on an open position (frozen-decisions.md,
  "Strategy, data & backtest honesty" (data truth row); backtest-honesty skill §8). The backtest must not strand a losing position because a feed wobbled.
- **MVP exit scope:** single position, **full exits only** — no partial scaling/trimming (matches
  `risk.max_open_positions=1`, strategy §8.1).

### 2.3 Fill-rate is a first-class output, not an assumption
The fraction of signals that actually filled under §2.1 is recorded per run and is a **cost-sensitivity axis** (§5):
an edge that only survives at an unrealistic 100% fill is fragile. (Mirrors TZ §13 "fill-rate break-even".)

## 3. Cost model — both sides, both tariffs, config not constants [LAW] (TZ §13, config §2.8)

> All cost params are **config keys** (config §2.8), never literals in code — the binding `tariff` is an
> **[owner-pending]** decision finalized by M3 cost-sensitivity. Money is **Quotation units/nano**, converted to
> `Decimal` for cost math only — **never float** (db-schema §1). Costs apply on **BOTH** sides of every round trip.

### 3.1 Per-side cost components (applied to entry fill AND exit fill)
```text
commission_bps   = costs.investor_commission_bps (30 = 0.30%/side, NO monthly fee)   # tariff='investor'
                 | costs.trader_commission_bps   ( 5 = 0.05%/side)                    # tariff='trader' (+ §3.2 fee)
slippage_bps     = costs.slippage_bps            (10 = 0.10%/side)                    # BACKTEST-modeled buffer
iceberg_bps      = costs.iceberg_surcharge_bps   ( 1 = +0.01%/side)                   # 1-lot pilot never icebergs ⇒ ~0; kept for parity
min_commission   = costs.min_commission_units/nano (0, 10_000_000 = 0.01 ₽ floor)    # Quotation, never float

per_side_cost(notional) = max( notional × (commission_bps)/10_000 , min_commission )   # commission, floored at 0.01 ₽
                        + notional × (slippage_bps + iceberg_bps)/10_000               # slippage buffer (+iceberg)
round_trip_cost = per_side_cost(buy_notional) + per_side_cost(sell_notional)
```
- **Инвестор:** ≈ 0.30% + 0.10% = **0.40%/side** (+0.01% iceberg, ~0 at pilot) ⇒ ≈ **0.80% round trip**
  (frozen-decisions.md, "Strategy, data & backtest honesty" (conservative fills row)). **No** monthly fee.
- **Трейдер:** ≈ 0.05% + 0.10% = **0.15%/side** (+0.01% iceberg, ~0 at pilot) ⇒ ≈ **0.30% round trip**, **plus** the §3.2 monthly fee.
- **Min-commission floor** is applied **per side** before slippage; at the 3000 ₽ pilot notional the 0.01 ₽ floor
  does **not** bind (commission ≈ 9 ₽ Инвестор / ≈ 1.5 ₽ Трейдер) — it is modeled for completeness, not because it binds at the pilot notional.

### 3.2 Трейдер monthly fee (390 ₽/mo) — MUST be modeled at 10k [LAW]
```text
trader_monthly_fee = costs.trader_monthly_fee_rub (390 ₽/mo)   # applies ONLY when tariff='trader'
```
- Charged **per calendar month over the whole backtest span** (not only months with trades) for the Трейдер tariff,
  as a `cash_events(type='commission')` accrual reducing Layer B. At 10 000 ₽ capital, 390 ₽/mo ≈ **47%/yr drag**
  (frozen-decisions.md, "Known drift / owner decisions pending" (commission tier vs edge), TZ §2) — omitting it would make Трейдер look falsely cheap.
- The official waiver (no-trades month / ≥1.5M assets / ≥5M turnover, TZ §13) is **unreachable** at the 10k pilot →
  the fee is **always** modeled for Трейдер. Do not model the waiver at pilot scale.
- **Инвестор has no monthly fee** — model 0.

### 3.3 BACKTEST vs LIVE cost asymmetry (no double-count) [LAW] (tax §1)
- **BACKTEST** applies the **modeled slippage buffer** (`costs.slippage_bps`) on top of commission — slippage is a
  *modeled* fill cost because the backtest fills at the posted limit/target, not a microstructure-realized price.
- **LIVE** Layer-B cost = **actual fill prices + actual commissions** from `fills` (db-schema §3.2). Slippage is
  already inside the live fill price, so **no separate slippage line** is added live (adding one double-counts).
- **Sandbox ≠ cost truth [LAW].** The T-Bank sandbox applies a flat 0.05% commission and simplified fills — a
  plumbing artifact, **not** the Трейдер tariff and **not** proof of live economics (frozen-decisions.md, "Known
  drift / owner decisions pending" (sandbox ≠ live economics), config §2.8, TZ §9). The backtest always uses the configured `tariff` + slippage + min-commission, never the sandbox figure.

## 4. Tax / dividend hook — Layer-B after-tax PnL (TZ §12.1; tax contract)

The backtest produces the **two-layer PnL** (tax §1) — the gate (§7) reads **Layer B**:
- **Layer A (economic, pre-cost/pre-tax):** "did the rule make money on price?" — diagnostic only.
- **Layer B (broker/tax, realized):** Layer A − round-trip costs (§3) − **НДФЛ** + **net dividends**.
  - **НДФЛ:** flat **13%** on realized gains at the 10k pilot (15% bracket above 2.4M unreachable; resident-only),
    **FIFO** cost basis, commissions reduce the taxable base, **accrued at each realization** into the equity curve
    (not deferred to year-end — else mid-period equity is overstated and Sharpe flattered). *(tax §2; LAW: honesty.)*
  - **Net dividends:** dividend cashflow is **NET** (taxed at source 13/15%, separate base), entered as
    `cash_events(type='dividend')`; the **dividend-gap entry block** (`dividend_gap_block_days=2`, derived
    `ex_date = last_buy_date + 1 trading day`) gates **entries only**, never an exit (tax §5–§6).
  - **MCFTR gross vs strategy net:** the MCFTR benchmark (§6) is **gross** total return; the strategy's dividends
    are **net** — this asymmetry is **documented and intentional** ("strategy net vs MCFTR gross"), not corrected
    (tax §5). Reports state it explicitly.
  - **ЛДВ NOT modeled** — the 2–6 week horizon is far below the 3-year threshold (tax §3).
- **Validation:** the tax layer has **no broker/sandbox reference** → it is validated against the **hand-computed
  НДФЛ fixture** (tax §8, the M2 test artifact). The honest-backtest Layer-B verdict reads the **full** cost model
  incl. slippage; the tax fixture asserts the **tax method only** (slippage excluded by design, tax §8).

## 5. Metrics (TZ §13) — report all; the gate reads Layer B

5.1 **Core metrics (per run, per tariff at M2):**
```text
expectancy        = p · avgWin − (1−p) · avgLoss − round_trip_cost   # after costs (Layer B per trade) [LAW]
hit_rate (p)      = wins / closed_trades
max_drawdown      = max peak-to-trough on the Layer-B equity curve
sharpe            = annualized mean/stdev of Layer-B returns
turnover          = traded notional / capital
exposure          = fraction of time in a position
fill_rate         = filled_signals / candidate_signals (§2.3)
trades_count      = closed trades (a verdict on < a documented minimum is "insufficient sample", not PASS)
```

5.2 **Deflated Sharpe Ratio (DSR) — multiple-testing honesty [LAW: anti-overfitting]** (TZ §13)
The plain Sharpe is inflated by how many strategy configs were tried. The backtest must compute the **Deflated
Sharpe Ratio**, which deflates the observed Sharpe by the **number of independent trials**:
```text
RECORD per walk-forward run (into docs/evidence/walk-forward-latest.md):
  trial_count   = number of (param-combo × tariff × window) configurations evaluated   # MUST be recorded [LAW]
  candidate_sharpes = the distribution of in-sample Sharpes across those trials
  DSR           = deflated_sharpe( observed_sharpe, trial_count, skew, kurtosis, n_returns )
```
- **The `trial_count` is mandatory and must be honest** — it includes the **full** research grid actually swept
  (strategy §4 grid: `ma_fast{10,20,30} × ma_slow{50,100} × pullback_min{2,3} × pullback_max{4,5,6} ×
  take_profit{5,6,8} × max_holding{20,40}`, times tariffs and windows). Reporting a Sharpe without its trial count
  is a contract violation — DSR is meaningless without it. The artifact persists `trial_count` (TZ §13).
- DSR `≤ 0` (the edge is indistinguishable from luck after deflation) **forces FAIL** regardless of raw Sharpe.

5.3 **Cost-sensitivity (the break-even sweep) [LAW]** (TZ §13) — "at what cost does the edge die?"
```text
sweep commission_bps  ∈ {investor 30, trader 5, … up to break-even}
sweep slippage_bps    ∈ {5, 10, 15, 20, … up to break-even}
sweep fill_rate       ∈ {realized, 90%, 75%, 50%}                      # the §2.3 fill model, degraded
report break-even points: the commission / slippage / fill-rate at which Layer-B expectancy crosses 0
```
- An edge that **only** survives at Трейдер 0.05% but dies at Инвестор 0.30% is **flagged**, feeding the M3 binding
  tariff decision (frozen-decisions.md, "Known drift / owner decisions pending" (commission tier vs edge)). An edge whose break-even slippage is below ~0.10% is fragile.

## 6. Benchmarks (TZ §13) — "the bot made money" is not a benchmark [LAW]

The backtest reports the strategy against **all** of these, on the same window and capital:
```text
equal_weight   = equal-weight buy-and-hold of the `approved` universe (SYNTHETIC; no DB row, db-schema §3.1)
IMOEX          = MOEX index, PRICE return       (instrument_kind='index', source='moex_iss')
MCFTR          = MOEX index, TOTAL-return GROSS  (instrument_kind='index', source='moex_iss'; gross vs strategy net §4)
cash           = 0% / risk-free hold            (SYNTHETIC; no DB row)
```
- `benchmarks = [IMOEX, MCFTR, cash, equal_weight]` (config §2.9). Index candles come from **MOEX ISS**
  (`index_source=moex_iss`) — T-Invest exposes index *last price* only (config §2.9, db-schema §3.1).
- Benchmarks use the **same** honest cost treatment where applicable (a buy-and-hold pays one round trip; cash pays
  nothing) — never compare a costed strategy to a cost-free benchmark.

## 7. Gate criteria — PASS / WEAK PASS / FAIL (TZ §13–§14) — **locked** [LAW]

The verdict is computed on **Layer-B after-tax** results under the §2 fill model + §3 costs. **M2 reports a
PROVISIONAL verdict per tariff** (both Инвестор and Трейдер); **M3 finalizes** under the **binding tariff**
(`tariff` [owner-pending], resolved by the §5.3 cost-sensitivity at M3).

```text
PASS       (all must hold):
  • Layer-B return  ≥  equal_weight + 2 pp        (≥ +2 percentage points vs equal-weight buy-and-hold)
  • AND  not worse than the index (IMOEX / MCFTR per the documented gross/net basis)
  • AND  max_drawdown  ≤  benchmark drawdown
  • AND  NOT a one-lucky-trade result            (robust across trades/windows; DSR > 0; adequate trades_count)
WEAK PASS:
  • Layer-B return  ≥  equal_weight + 1 pp        (and the other PASS conditions hold)  → proceed with caution
FAIL:
  • the edge dies after costs (Layer-B expectancy ≤ 0), OR DSR ≤ 0, OR fails any PASS condition above
```
- **STOP gate after M3 [LAW] (TZ §14):** if the M3 evidence is **FAIL** (edge dies after costs — the most likely
  outcome per all research), **STOP the live track** — do **NOT** build M4+. Options: iterate strategy/params
  *within frozen constraints* and re-run M2–M3, or shelve. This operationalizes the phased-path LAW
  (frozen-decisions.md, "Scope & product shape" (phased path row)).
- **Walk-forward, not global fit [LAW]:** the gate reads the **walk-forward** result (optimize on train → test on
  next → shift → aggregate, TZ §14), preferring **robust** params over max return; a config that works on one ticker
  or one short period is **rejected** (strategy §4 grid is research-only; the live config stays pinned). A single
  backtest is never proof (frozen-decisions.md, "Strategy, data & backtest honesty" (anti-overfitting row)).
- **Sandbox is never the gate input [LAW]:** the ≥30-day paper/sandbox window proves **plumbing + fill-model
  parity** (TZ §14, backtest §8), not edge — the gate verdict comes from the honest backtest, not sandbox PnL.

## 8. Fill-model parity check (M4/M5) [verify]

A **deferred-but-pinned** check (TZ §9, §14 pre-live gate 2): the backtest's **assumed** fills (§2) must be
reconciled against **observed** sandbox/paper fills during the ≥30-day window — documented as the fill-model parity
artifact. A large gap (the bot fills far more/less often, or at materially different prices than §2 assumes)
invalidates the backtest's fill assumptions and must be resolved before live. The sandbox's simplified fills (no
partial fills, fixed-style commission) are themselves **not** truth (§3.3) — the parity check measures the **gap**,
it does not adopt sandbox economics.

## 9. Frozen invariants honored
- **No intraday lookahead** — strictly-causal day loop; `decision_ts` = final D1 close; signal on `D` ⇒ order on
  `D+1`; same-day fill forbidden (§1.3, §2.1) [LAW; frozen-decisions.md, "Strategy, data & backtest honesty" (no-lookahead row), strategy §3.1].
- **Conservative both-side fills** — entry fills only inside the next-session TTL window; with D1-only data the
  fallback is `D+1.open ≤ limit` else unfilled; TP only if `high ≥ target`; stop/MA-break gap-fills at the worse
  price; ambiguous same-bar stop/TP ordering resolves to the worse outcome; unfilled = no trade; one
  attempt/signal; no asymmetric exit optimism (§2)
  [LAW; frozen-decisions.md, "Strategy, data & backtest honesty" (conservative fills row), strategy §5 (entry rule, step 7)/§8].
- **Costs both sides, both tariffs** — commission per `tariff` + 0.10%/side slippage + 390 ₽/mo (Трейдер) + 0.01 ₽
  min-commission floor, as config not constants (§3) [LAW; frozen-decisions.md, "Strategy, data & backtest honesty" (conservative fills row) + "Known drift / owner decisions pending" (commission tier vs edge), config §2.8, TZ §13].
- **Backtest/live parity** — same `generate_signals`, indicators computed locally (§1.2) [LAW; strategy §3.2–3.3].
- **Anti-overfitting** — train-only optimize, walk-forward validation, prefer robust params, DSR with recorded
  `trial_count`, cost-sensitivity sweep (§5, §7) [LAW; frozen-decisions.md, "Strategy, data & backtest honesty" (anti-overfitting row), TZ §13–§14].
- **Data truth** — `data_conflict`/stale blocks a new **entry**, never a protective **exit** (§2.2) [LAW;
  frozen-decisions.md, "Strategy, data & backtest honesty" (data truth row)].
- **Costs/tax honest** — Layer-B reads НДФЛ accrued-at-realization (FIFO, 13%) + net dividends; BACKTEST adds
  modeled slippage, LIVE does not (no double-count) (§4, §3.3) [LAW; tax §1–§2].
- **Sandbox ≠ proof** — sandbox is plumbing + fill-parity only; never the gate input; never the cost figure (§3.3,
  §7, §8) [LAW; frozen-decisions.md, "Known drift / owner decisions pending" (sandbox ≠ live economics), TZ §9].
- **Benchmarks mandatory** — equal-weight, IMOEX (price), MCFTR (gross), cash; "the bot made money" is not a
  benchmark (§6) [LAW; TZ §13].
- **LLM never decides buy/sell** — the backtest evaluates a deterministic rule; it proposes nothing live (§intro)
  [LAW; frozen-decisions.md, "Scope & product shape" (LLM never trades row)].

## 10. Open questions / owner-pending
| Item | Status | Resolves at | Note |
| --- | --- | --- | --- |
| Binding `tariff` (Инвестор vs Трейдер) | **[owner-pending]** | M3 (cost-sensitivity §5.3) | M2 reports per-tariff PROVISIONAL; M3 picks the binding tariff from the cost-sensitivity sweep; config §2.8 default `investor` |
| Final strategy params (`max_holding_days`, pullback band, confirmation thresholds, rank weights) | **[owner-pending]** / **[verify M3]** | M3 walk-forward | from the research grid (strategy §4, §12); pinned by owner after evidence — the backtest sweeps the grid, the **live** config stays pinned to frozen |
| Minimum `trades_count` for a non-"insufficient sample" verdict | **[verify M3]** | M3 | pin the sample-size floor in the walk-forward artifact (a verdict on too few trades is not a PASS) |
| DSR auxiliary inputs (skew/kurtosis/`n_returns` estimator details) | **[verify M3]** | M3 | the DSR **formula + recorded `trial_count` are pinned now**; estimator specifics tuned and documented in the artifact |
| Fill-model parity tolerance (backtest-assumed vs sandbox/paper observed) | **[verify]** | M4/M5 (§8) | the parity check is pinned; the acceptable gap threshold is measured during the ≥30-day window before live |
| НДФЛ RUB rounding direction (math-round vs floor) | **[verify]** | M2 (tax §8 fixture) | pinned in the worked НДФЛ fixture; affects Layer-B by sub-ruble amounts |
| Are T-Invest D1 **share** candles already split-adjusted before mixing with ISS? | **[verify]** | M1 (db-schema §5) | empirical check on a known split before the backtest consumes mixed sources |

## 11. Cross-references
- Frozen LAW: `docs/frozen-decisions.md` — "Strategy, data & backtest honesty" (no-lookahead, conservative
  both-side fills + costs, anti-overfitting, data truth rows); "Known drift / owner decisions pending"
  (commission tier vs edge, sandbox ≠ live economics); "Scope & product shape" (LLM never trades row).
- Spec: `docs/TZ.md` §13 (backtest & validation), §14 (walk-forward, STOP gate, pre-live gates), §12.1 (tax/dividend).
- Strategy: [strategy.md](strategy.md) (the entry rule §5 + exits §8 this fill model mirrors; `entry_ref`/`stop_ref`).
- Schema: [db-schema.md](db-schema.md) (`candles.is_complete`, `signals.decision`, `fills`, `cash_events`,
  `positions.close_reason`, Quotation units/nano).
- Config: [config-and-secrets.md](config-and-secrets.md) §2.8 (costs/tariff), §2.9 (benchmarks/index_source/close_definition).
- Taxes: [tax-and-dividends.md](tax-and-dividends.md) (Layer-A/B, НДФЛ FIFO, net dividends, dividend-gap, MCFTR gross/net).
- Skills: `backtest-honesty`, `risk-policy-guardian`. Auditors: `lookahead-auditor`, `risk-invariant-auditor`.
