# Contract — Strategy (signal generation) (TZ §6)

> **Status:** M0 contract, **resolved on paper (no strategy code yet)**. This pins the **pure-function**
> signature + the entry/ranking rules the M2 `strategy/` module implements verbatim. **`docs/frozen-decisions.md` 🔒
> wins** on any conflict — values marked **[LAW]** mirror a frozen invariant (pinned live) and may not be
> changed here (only via owner decision + ADR + the same-change rule). The optimization grid is **research-only**;
> **the live bot never self-optimizes**.
> **[owner-pending]** = a value the owner must confirm/pin before live (do not silently fix it).
> **[verify]** = an empirical/M3 fact still to be measured (specify the structure now, never assert the value).
> Pairs with [db-schema.md](db-schema.md), [config-and-secrets.md](config-and-secrets.md),
> [tax-and-dividends.md](tax-and-dividends.md).

This contract owns **signal generation only** — "is this ticker a buy candidate today, and at what limit?".
**Exits live in the risk engine** (TZ §7; close_reason vocabulary in [db-schema.md](db-schema.md)); they are
summarized in §8 here for completeness but are NOT this module's write zone. **Sizing lives in the risk engine**
(TZ §7.4) — the strategy proposes an entry reference + stop reference; the risk engine sizes lots. The LLM never
decides buy/sell: this module is a **deterministic pure function over closed D1 candles** [LAW].

---

## 1. Concept (TZ §6)
**Pullback inside an uptrend, on D1.** Enter a confirmed shallow pullback while a stock is in an established
uptrend and the market regime is healthy. One candidate at most per day reaches a proposal (≤1 proposal/day
[LAW]). Everything is computed on the **final** daily close; the entry is placed for the **next session**
(no intraday lookahead [LAW]).

## 2. Pure function — signature & contract (TZ §6)
The strategy is the **only** place strategy logic lives. It is a **pure, deterministic, side-effect-free**
function: same inputs → same `signals` rows. No I/O, no clock read, no broker call, no DB write inside it
(the caller persists the returned rows to the `signals` table).

```python
# Conceptual signature (M2 implements verbatim; types per db-schema Quotation units/nano).
def generate_signals(
    decision_ts: int,                      # epoch-ms UTC of the acted-on FINAL D1 close (candles.ts) [LAW: no-lookahead]
    candles_by_uid: Mapping[str, list[Candle]],  # per instrument_uid, ascending ts, is_complete=1 ONLY (§3)
    index_candles: list[Candle],           # IMOEX (price) D1 series for the market-regime gate; is_complete=1 only
    eligible_uids: Sequence[str],          # approved & passed per-cycle eligibility (risk engine pre-filtered)
    params: StrategyParams,                # pinned live params (§4) — strategy.* config keys
    dividend_calendar: Mapping[str, list[DivEvent]],  # for the dividend-gap pre-check (informational; risk engine enforces)
) -> list[StrategySignal]:
    ...
```

Returned `StrategySignal` shape (maps onto the `signals` table + drives one proposal when `decision='candidate'`
and ranked first):

| Field | Type | Notes |
| --- | --- | --- |
| `instrument_uid` | TEXT | db-schema `signals.instrument_uid` |
| `ts` | INTEGER (epoch-ms) | `= decision_ts`; the acted-on D1 close |
| `decision` | enum | `candidate` \| `skipped` (NOT `selected`/`risk_rejected` — see §6) |
| `reason` | TEXT \| NULL | skip code from the frozen vocab (§3.4) when `decision='skipped'`, else NULL |
| `entry_ref` | Quotation pair | reference price for the next-session LIMIT (§5) — risk engine rounds the tick DOWN for buy |
| `stop_ref` | Quotation pair | reference hard-stop level (≈4% below entry_ref) — feeds risk-engine sizing, NOT a literal 50 ₽ stop |
| `features` | JSON (TEXT) | snapshot: `{ma_fast, ma_slow, local_high, pullback_pct, confirmation, rr, spread_bps, rank_score}` → `signals.features` |
| `rank_score` | float | the §7 ranking score (also inside `features`); the caller picks the top-1 |

> **Decision-vs-selection split (matches db-schema §3.2 `signals` DDL):** the strategy emits `candidate` / `skipped`
> only. The **caller** (signal-orchestrator) applies `decision='selected'` to exactly the top-ranked candidate
> that the risk engine accepts, and `decision='risk_rejected'` to a candidate the risk engine refuses — those
> two transitions are NOT the strategy's call. The strategy never writes an order, a proposal, or a position.

## 3. Inputs, lookback & warm-up (TZ §6, §13)
### 3.1 No-lookahead gate [LAW]
- The function consumes **only** candles with `candles.is_complete = 1` (db-schema §4 — set only once the bar
  reflects the **final** close per `config.close_definition`). A bar with `is_complete=0` is invisible to the
  strategy. This is the executable form of *"signal only after the daily candle closes; entry no earlier than
  the next session; no intraday lookahead"* [LAW].
- `decision_ts` is the timestamp of the **latest** `is_complete=1` D1 bar — the strategy reads candles with
  `ts ≤ decision_ts` and **never** a future bar. Entry executes the **next** `NORMAL_TRADING` session (risk
  engine + execution own that; no intraday data is ever consulted).
- A bar marked `is_stale=1` or an instrument with `instrument_reference.data_status='data_conflict'` →
  `decision='skipped'` (reason `data_missing` / `data_conflict`) — never silently trade degraded data [LAW: data truth].

### 3.2 Warm-up / minimum history
- Indicators are computed **locally** (NOT via broker `GetTechAnalysis`) so backtest/live stay identical [LAW: parity].
- Warm-up = the indicator window must be fully populated with `is_complete=1` bars before a value is valid.
  Required leading bars per instrument = **`max(ma_slow, local_high_lookback)`** completed D1 bars
  (with `ma_slow=50` and `local_high_lookback=20` → ≥ 50 bars). The index regime series needs ≥ `ma_slow` (50)
  completed bars too. **An instrument/index with insufficient history → `skipped` (reason `data_missing`)** —
  never compute an MA on a short window.
- The backtest loads extra leading history (TZ §13: ~100 warm-up bars) so the first tradable day is not starved.
  The warm-up is *exclusion*, not a value to optimize.

### 3.3 Moving averages (computed once, reused everywhere) [LAW: pinned live]
- `ma_fast` = SMA over `strategy.ma_fast` closes (**pinned live = 20** [LAW]).
- `ma_slow` = SMA over `strategy.ma_slow` closes (**pinned live = 50** [LAW]).
- Canonical MA20/MA50 are reused across entry trend filter, exits (trend-break/trailing-support), and the
  market-regime gate so **all three agree** [LAW]. SMA, on closes, using `Decimal` for research math (db-schema
  §1: convert Quotation→Decimal for math only; never float money).

### 3.4 Skip-code vocabulary (frozen — must match db-schema `signals.reason` §2 exactly)
```text
lot_too_expensive | low_liquidity | wide_spread | not_trading | data_missing | data_conflict
```
> These are the **eligibility/data** skip codes owned by the risk engine's per-cycle filter (db-schema §2
> enum table, config §2.4). The strategy applies `data_missing` / `data_conflict` for warm-up/staleness gaps (§3.1–3.2);
> the remaining four are set by the risk-engine eligibility pass that runs **before** `generate_signals`
> (only `eligible_uids` reach the strategy). A strategy-pattern miss (no uptrend / wrong pullback / no
> confirmation) is **NOT a skip row** — it is simply "no candidate" (not persisted as a skip), keeping the
> skip vocabulary strictly the frozen six. *(Lab-mode rich logging of non-candidates is a journal concern, not
> a `signals.reason` value — see §10.)*

## 4. Pinned live parameters (config `strategy.*` — must match config-and-secrets §2.6 exactly)
```text
ma_fast            = 20      # [LAW pinned] SMA fast window
ma_slow            = 50      # [LAW pinned] SMA slow window
pullback_min_pct   = 2.0     # [verify M3] lower bound of the pullback band (structure pinned; value tunable in research)
pullback_max_pct   = 6.0     # [verify M3] upper bound of the pullback band
take_profit_pct    = 6.0     # [LAW pinned] (exit; risk engine) — listed for cross-reference
trailing_pct       = 3.0     # (exit; risk engine)
trend_support_ma   = 20      # trailing support = close < MA20 (exit; risk engine)
trend_break_ma     = 50      # trend-break exit = close < MA50 (exit; risk engine)
max_holding_days   = <owner-pending {20,40}>   # [owner-pending] pinned after M3 evidence (decision 6) — time exit
```
- **Pinned-live [LAW]:** `ma_fast=20`, `ma_slow=50`, `take_profit_pct=6.0`. Changing any pinned value = an
  **owner decision to change the frozen contract** (same-change rule: this doc + config + frozen-decisions +
  ADR together) — never a silent live divergence.
- **Research-only optimization grid (TZ §6, config §2.6) — NEVER shipped to live:**
  ```text
  ma_fast {10,20,30}  ma_slow {50,100}  pullback_min {2,3}  pullback_max {4,5,6}
  take_profit {5,6,8}  max_holding {20,40}
  ```
  The live config is **pinned to the frozen values**; the grid is for walk-forward exploration only. The live
  bot **never self-optimizes** [LAW].
- `pullback_min_pct` / `pullback_max_pct` are **[verify M3]**: the **band structure and parameter names are
  pinned now**; the exact percentages are tuned in M3 walk-forward within the research grid, then pinned by
  owner decision. The default band 2–6% is the documented pullback framing (TZ §6; tunable in research, not frozen).

## 5. Entry rule (pure-function body) — in order
For each `uid` in `eligible_uids`, evaluate against bars with `ts ≤ decision_ts` (latest = `decision_ts`):

1. **Warm-up / data gate (§3.1–3.2):** insufficient history, `is_stale`, or `data_conflict` → `skipped`
   (`data_missing` / `data_conflict`). Otherwise continue.
2. **Market-regime gate [LAW]** (applies to the whole cycle, evaluated on the IMOEX index series):
   - **No NEW entries** when the index close < its `ma_slow` (MA50) **OR** the index 5-day return < −5%
     (`risk.market_regime_5d_floor_pct`). When the regime is risk-off, **no candidate is emitted at all**
     (exits are always allowed — but exits are the risk engine's job, never gated here). Matches frozen
     "market-regime filter" + config §2.5 `risk.market_regime_index_ma` / `risk.market_regime_5d_floor_pct`.
3. **Trend filter (uptrend established):**
   - `close > ma_slow` (price above MA50) **AND** `ma_fast > ma_slow` (MA20 above MA50). Both must hold on
     the `decision_ts` bar. (TZ §6: "price > MA50 and MA20 > MA50".)
4. **Pullback in band:**
   - `local_high` = highest `high` over the last `local_high_lookback` (= 20) completed bars.
   - `pullback_pct = (local_high − close) / local_high × 100`.
   - Require `pullback_min_pct ≤ pullback_pct ≤ pullback_max_pct` (default band 2–6%). A pullback shallower than
     the min = not yet a pullback; deeper than the max = the uptrend's "critical level" is treated as broken →
     no candidate. *(Frozen: "pullback 2–6%, critical level intact".)*
5. **Entry confirmation (at least one must hold on the `decision_ts` bar):**
   - `close > prior_close` (up day off the pullback low), **OR**
   - `close > ma_fast` (recovered back above MA20), **OR**
   - `volume > confirmation_volume_factor × avg_volume(N)` on a recovery bar (volume rise on the bounce).
   - Confirmation parameters (`confirmation_volume_factor`, `avg_volume` window `N`) are **[verify M3]** —
     **names pinned now**, values tuned in research; default factor ≈ 1.2 over a 20-bar average volume
     (placeholder, not asserted as LAW). *(Frozen/TZ §6: "close > prior day OR back above MA20 OR volume rise
     on recovery".)*
6. **Dividend-gap pre-check (informational):** if a known ex-date (derived `last_buy_date + 1 trading day`,
   tax contract §6) falls within `dividend_gap_block_days + 1` of the next session, flag it in `features`.
   **The risk engine enforces the block** (entries only, never exits) — the strategy surfaces it but does not
   own the gate.
7. **Emit candidate:** compute `entry_ref` (the reference price for the next-session limit — by default the
   `decision_ts` close; the risk engine applies `order.max_entry_premium_pct` and rounds the tick DOWN for a
   buy, config §2.7) and `stop_ref` (≈ `hard_stop_pct` = 4% below `entry_ref`, the level the risk engine sizes
   against). Set `decision='candidate'`, fill `features` + `rank_score` (§7).

> **Re-entry discipline is enforced by the risk engine [LAW], surfaced here:** the strategy may still produce a
> `candidate` for a just-exited ticker, but the risk engine rejects it (→ `risk_rejected`) under the frozen
> re-entry law: **no same-day re-entry into a just-exited ticker; 5-day cooldown (`risk.reentry_cooldown_days`);
> re-entry requires a fresh pullback + new signal + new confirm.** The strategy does not "remember" prior exits
> (it is pure); re-entry state lives with the risk engine / positions. Never sell at +6% and immediately re-buy
> because price kept rising.

## 6. Decision semantics & the ≤1-proposal/day cap [LAW]
- The strategy returns **0..N `candidate` rows + any `skipped` rows**. It does **not** select.
- The caller ranks candidates (§7), then hands the **top-1** to the risk engine. If the risk engine accepts →
  that row becomes `decision='selected'` and **exactly one** proposal is created (db-schema §3.2 `signals` DDL). If it
  refuses → `decision='risk_rejected'`, **no order row** (db-schema §3.2 `signals` DDL), and the caller may **not** fall
  through to the 2nd-ranked candidate in the same cycle — the **≤1 proposal/day** cap is a hard limit
  (`risk.max_proposals_per_day = 1`, config §2.5) [LAW], not "1 successful proposal".
- This guarantees at most one entry proposal per day even with several technically-valid candidates.

## 7. Ranking (≤1 proposal/day) (TZ §6)
When ≥1 candidate exists, rank to pick the single best. Inputs are all known as-of `decision_ts` (no lookahead):

| Factor | Direction | Source |
| --- | --- | --- |
| Liquidity | higher better | `instrument_reference.avg_turnover_rub` |
| Trend strength | higher better | `(ma_fast − ma_slow)/ma_slow`, and `close` distance above `ma_slow` |
| Pullback quality | closer to band centre better | `pullback_pct` vs the band midpoint |
| Reward/risk (rr) | higher better | `(take_profit_pct) / hard_stop_pct` adjusted by entry/stop distance |
| Spread | lower better | `instrument_reference.spread_bps` (also a §2.4 eligibility filter) |

- Combine into `rank_score` (the exact weighting is **[verify M3]** — the **factor set is pinned**; the weights
  are tuned in research and then pinned). **Tie-break = liquidity** (highest `avg_turnover_rub`), then lowest
  `spread_bps`, then lexical `instrument_uid` for full determinism (pure-function requirement).
- The caller takes `rank_score`-max. No randomness; deterministic given inputs.

## 8. Exit behavior — owned by the risk engine, summarized for completeness (TZ §7.7)
> Exits are **NOT this module's write zone** — they live in the risk engine / position manager
> (`positions.close_reason` ∈ `risk|trend|target_trailing|time|manual`, db-schema §2 enum table). Pinned here so the
> strategy + exits agree on the same canonical MAs.

| Exit | Rule | close_reason | Frozen source |
| --- | --- | --- | --- |
| Hard stop | price ≤ ≈ `hard_stop_pct` (4%) below entry | `risk` | frozen "hard stop ~4%" |
| Trend break | D1 close < MA50 (`trend_break_ma`) | `trend` | frozen "trend-break exit (close below MA50)" |
| Target → trailing | take-profit **6%** [LAW] then trail **3%**; trailing support = close < MA20 (`trend_support_ma`) | `target_trailing` | frozen "TP 6% then trailing 3%" |
| Time exit | held ≥ `max_holding_days` (**[owner-pending]** {20,40}); **max 8 weeks (≈40 bars) without review** | `time` | frozen "horizon 2–6 weeks (max 8)" |
| Manual | owner "Закрыть позицию" in Telegram | `manual` | TZ §10 |

### 8.1 Qualitative TIME-EXIT BEHAVIOR rule (strategy decision NOW, not an M3 output) [LAW intent]
> *Verifier-required: what to do when a position is near the time horizon but the trend is still live and PnL is
> weak-plus or small-minus. This is a strategy-contract decision now (decision 6; frozen-decisions "Known
> drift" on the 2–6 week horizon), distinct from the empirical `max_holding_days` value.*

The time exit is governed by these qualitative rules, evaluated only on a **final D1 close** (no intraday):

1. **Trend broken → exit wins immediately.** If a trend-break (close < MA50) or hard-stop condition is true,
   the time horizon is irrelevant — exit now under `trend` / `risk`. Protective exits always take precedence
   over "hold to horizon".
2. **Trend still live (close ≥ MA50) and within horizon (held < `max_holding_days`):** **hold to horizon** —
   do not cut a healthy, in-trend position early just because PnL is flat/weak. A weak-plus or small-minus mark
   inside a live uptrend is **not** an exit reason; the thesis (pullback-in-uptrend) is intact until MA50
   breaks, the hard stop hits, or take-profit/trailing fires. Let the automated stop define the downside.
3. **Trend still live but horizon reached (held ≥ `max_holding_days`):** **time-exit (close, `close_reason='time'`).**
   The horizon is a hard discipline cap, not a suggestion — a position that has neither hit take-profit nor
   broken trend by `max_holding_days` is closed to free the single position slot (pilot `max_open_positions=1`)
   and avoid open-ended holds. **Do not roll the horizon forward** to "wait for it to work".
4. **Take-profit already armed (≥ +6%, trailing active):** the trailing stop (close < MA20 / 3% trail) governs
   the exit, **not** the time horizon — let a winner run under the trail rather than time-cutting it. The time
   exit applies to positions that never reached take-profit.
5. **Beyond 8 weeks (≈40 bars): never silently roll.** The frozen "max 8 weeks without review" means a position
   may exceed the base 2–6 week horizon only via an explicit **owner review**; absent a review, the time-exit at
   `max_holding_days` (≤ ~40 bars) closes it. The bot never extends a hold past the cap on its own.

> Net: **protective exits > hold-to-horizon > time-cap**; weak/flat-but-in-trend = hold, not trim; winners trail,
> not time-cut; the horizon is a ceiling the bot never raises itself. (Trimming/partial-scaling is **out of MVP
> scope** — single position, full exits only; matches `max_open_positions=1`.)

## 9. Worked example (illustrative — not a pinned fixture)
> Illustrates the **method** only (like the tax contract §8). All money via Quotation units/nano, not float.

- `decision_ts` = a final D1 close; SBER `close = 300.00 ₽`, `ma_fast(20) = 305.00`, `ma_slow(50) = 290.00`.
- Trend filter: `close (300) > ma_slow (290)` ✓ and `ma_fast (305) > ma_slow (290)` ✓.
- `local_high(20) = 315.00` → `pullback_pct = (315 − 300)/315 × 100 = 4.76%` → inside the 2–6% band ✓.
- Confirmation: `close (300) > prior_close (297)` ✓ (one confirmation suffices).
- IMOEX regime: index `close > index MA50` and 5d return `> −5%` ✓ → entries allowed.
- Emit `candidate`: `entry_ref ≈ 300.00` (risk engine adds ≤0.20% premium, rounds tick DOWN for buy),
  `stop_ref ≈ 288.00` (≈ 4% below). Ranked; if top-1 and risk engine accepts → `selected` → one proposal.

## 10. Lab-mode diagnostics (research/paper only — does not change the risk frame)
Per TZ §1 (LABORATORY character), the research/paper posture logs **every** evaluated ticker with rich features
(trend/pullback/confirmation values, rank inputs) for fast iteration — but this is a **journal concern**, written
by the caller to the audit/journal layer, **not** new `signals.reason` codes. The live posture stays conservative
(≤1 proposal/day, pinned params, frozen limits). "Lab" changes how we *learn*, never how we *risk* [LAW].

## 11. Frozen invariants honored
- **No intraday lookahead** — consumes only `is_complete=1` bars; `decision_ts` = latest final D1 close; entry
  next session (§2, §3.1) [LAW; frozen-decisions.md, "Strategy, data & backtest honesty" (no-lookahead row), db-schema §4].
- **Pinned live MAs + TP** — `ma_fast=20`, `ma_slow=50`, `take_profit=6%`; live never self-optimizes; the grid is
  research-only (§4) [LAW; frozen-decisions.md, "Order & risk rules" (risk-exits row), config §2.6].
- **≤1 proposal/day** — the cap is on proposals, not successes; no fall-through to the 2nd candidate (§6) [LAW;
  frozen-decisions.md, "Order & risk rules" (portfolio-limits row), config §2.5].
- **Market-regime filter** — no new entry when IMOEX close < MA50 or 5d return < −5%; exits always allowed (§5 step 2)
  [LAW; frozen-decisions.md, "Order & risk rules" (market-regime row), config §2.5].
- **Re-entry discipline** — no same-day re-entry; 5-day cooldown; fresh pullback + new signal + new confirm;
  enforced by the risk engine, surfaced here (§5 note) [LAW; frozen-decisions.md, "Order & risk rules" (re-entry row)].
- **Holding horizon 2–6 weeks (max 8)** + qualitative time-exit (§8.1) [LAW; frozen-decisions.md, "Order & risk rules" (risk-exits row) + "Known drift / owner decisions pending" (holding-horizon row)].
- **Hard stop ~4% / trend-break close<MA50 / TP6%→trail3% (support close<MA20)** — exits agree on canonical MAs
  (§8) [LAW; frozen-decisions.md, "Order & risk rules" (risk-exits row)].
- **Data truth** — stale / `data_conflict` instrument → `skipped`, never traded (§3.1) [LAW; frozen-decisions.md, "Strategy, data & backtest honesty" (data-truth row)].
- **LLM never decides buy/sell** — deterministic pure function over closed candles; indicators computed locally
  for backtest/live parity (§2, §3.2–3.3) [LAW; frozen-decisions.md, "Scope & product shape" (LLM-never-trades row), TZ §9].
- **Strategy is the single home of strategy logic** — exits/sizing/selection live in the risk engine; this
  module only emits `candidate`/`skipped` (§2, §6, §8) [TZ §6, §7].

## 12. Open questions / owner-pending
| Item | Status | Resolves at | Note |
| --- | --- | --- | --- |
| `strategy.max_holding_days` | **[owner-pending]** | M3 (decision 6) | from grid {20,40}; 40 bars ≈ 8-week cap; pinned by owner after evidence — **do not assert** |
| `pullback_min_pct` / `pullback_max_pct` exact values | **[verify M3]** | M3 walk-forward | band **structure + names pinned now**; values tuned in research grid {min 2,3 / max 4,5,6}, then pinned |
| Entry-confirmation thresholds (`confirmation_volume_factor`, avg-volume window `N`) | **[verify M3]** | M3 | rule + parameter names pinned now; numeric defaults (~1.2× / 20-bar) are placeholders, not LAW |
| `rank_score` factor weights | **[verify M3]** | M3 | factor set + tie-break pinned now; weighting tuned then pinned |
| `local_high_lookback` (default 20) | **[verify M3]** | M3 | pullback reference window; default mirrors MA20; tunable in research |
| Final tariff (cost-sensitivity affects edge, not the rule) | **[owner-pending]** | M3 | config §2.8 `tariff`; does not change strategy structure |

## 13. Cross-references
- Frozen LAW: `docs/frozen-decisions.md` (no-lookahead, pinned MAs/TP, ≤1 proposal/day, regime filter, re-entry,
  horizon, exits, data truth, LLM-never-trades).
- Spec: `docs/TZ.md` §6 (strategy), §7 (risk engine / exits / sizing), §13 (backtest warm-up & honest fills).
- Schema: [db-schema.md](db-schema.md) (`signals` / `proposals` / `positions` enums, `candles.is_complete`).
- Config: [config-and-secrets.md](config-and-secrets.md) §2.5 (risk/regime), §2.6 (strategy params), §2.7 (order).
- Taxes: [tax-and-dividends.md](tax-and-dividends.md) §6 (dividend-gap entry block, ex-date derivation).
- Skills: `risk-policy-guardian`, `backtest-honesty`. Auditors: `lookahead-auditor`, `risk-invariant-auditor`.
