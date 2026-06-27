# Contract — Tax / Dividend / Corporate-action modeling (TZ §12.1)

> **Status:** M0 contract, **resolved on paper (no code yet)**. Pins the rules the **after-tax PnL layer**
> and the **honest backtest** implement. **`docs/frozen-decisions.md` 🔒 wins**. **[verify]** items are
> confirmed by research `whq6u1gxe` / §20. The sandbox models **none** of this — the after-tax layer is
> validated only against **hand-computed fixtures** (§5).

This contract feeds: the two-layer PnL journal (TZ §12), the backtest cost/tax model (TZ §13), the
`dividends` + `fills` + `cash_events` tables ([db-schema.md](db-schema.md)), and the dividend-gap entry block
in the strategy/risk path.

---

## 1. Two-layer PnL (the reason this contract exists)
- **Layer A — economic strategy PnL** (pre-cost, pre-tax): "did the rule make money on price?"
- **Layer B — broker/tax PnL** (realized): commissions (both sides) + **НДФЛ** + **net dividends**.
  - **BACKTEST** Layer-B additionally applies the **slippage** buffer (a modeled fill cost, config §2.8).
  - **LIVE** Layer-B derives cost from **actual fill prices + actual commissions** — slippage is already in the
    fill price, so there is **no separate slippage line** (adding one would double-count).
- **Tax accrual for the curve:** Layer-B equity, drawdown, and per-trade expectancy use НДФЛ **accrued at each
  realization** (reduce Layer B immediately), not deferred to year-end withholding — else mid-period equity is
  overstated and Sharpe flattered. Withholding timing stays a documented cashflow caveat only (§2).

Reports and the gate read **Layer B** for the honest verdict; Layer A is diagnostic. Both are logged per trade.

## 2. НДФЛ on realized securities gains
- **Rate:** **13%** on realized gains for a Russian tax resident; the 2025+ progressive scale adds **15% on the
  portion of annual taxable income above 2.4M ₽**. At the **10 000 ₽ pilot** the 15% bracket is **effectively
  unreachable → model a flat 13%** (keep the bracket as a config constant so it is not hard-coded away).
  ✅ Research `whq6u1gxe`: the securities base is computed **separately** from the salary base; tax =
  2.4M×13% + (base−2.4M)×15%; **resident-only** (non-resident = 30% on sale gains) — enforce a resident guard.
- **Realization:** tax accrues on **closing** a position (sell), not on open/unrealized marks.
- **Cost basis = FIFO** (Russian broker convention) — first lots bought are the first sold. The backtest and the
  journal both compute realized gain per closed lot via FIFO.
- **Commissions reduce the taxable base** (buy + sell commissions are part of cost/proceeds) — model accordingly.
- **Broker = tax agent** (НК РФ ст. 214.1; confirmed `whq6u1gxe`): T-Bank computes/withholds/remits НДФЛ — at
  **year-end** (night 31.12→01.01; recovery to 31.01 if cash short) and **on cash withdrawal**. For the **model**
  we accrue tax at realization into Layer B; **withholding timing differs from accrual** (cashflow-timing caveat,
  not a PnL difference). The bot tax number is an **estimate**; the **broker tax report is the legal source of
  truth** — reconcile against it, never treat the bot figure as authoritative.

## 3. ЛДВ (3-year long-term holding exemption) — **NOT modeled**
The 2–6 week (max 8) horizon [LAW] is always far below the **3-year** ЛДВ threshold, so the long-term-ownership
deduction can never apply. State this explicitly so it is **not** implemented and not silently assumed.

## 4. Loss offset
- **Within-period netting:** sale gains and losses inside the reporting period net against each other in the
  **sale-gain base** for the Layer-B after-tax number.
- **Dividends are a SEPARATE tax base** (confirmed `whq6u1gxe`) — dividend income is **not** netted against
  trading losses; the two bases are taxed independently.
- **Cross-year carry-forward** of losses is **out of MVP scope** (noted for completeness; the pilot runs short).

## 5. Dividends
- **Taxed at source 13/15%** (separate dividend base) → the bot's dividend cashflow in **Layer B is NET**; the
  broker (T-Bank) withholds within ~1 business day of crediting (confirmed `whq6u1gxe`).
- **Benchmark like-for-like:** the **MCFTR** total-return benchmark is **GROSS** total return. We compare the
  strategy's **net** dividends against a **gross** MCFTR — this asymmetry is **documented and intentional**
  (re-deriving a net-MCFTR is out of scope); reports state "strategy net vs MCFTR gross".
- **Dividend source (resolved `whq6u1gxe`):** T-Invest `InstrumentsService.get_dividends`. The per-share GROSS
  amount is in the field **`dividend_net`** (mislabeled — it is gross; the API applies no withholding). There is
  **no `ex_dividend_date`**: derive **ex_date = `last_buy_date` + 1 trading day** (T+ settlement). Stored per
  [db-schema](db-schema.md) `dividends` (last_buy_date/record_date/payment_date + gross units/nano). The `to`
  request param filters on `record_date`.

## 6. Dividend-gap entry block [feeds risk engine]
- **No new entry from 2 trading days before a known ex-date until the ex-date passes** (`dividend_gap_block_days`
  = 2, configurable). The ex-date is **derived** = `last_buy_date` + 1 trading day (API has no ex_dividend_date).
  Rationale: avoid buying into a predictable post-ex price gap.
- Applies to **entries only** — never blocks a protective **exit**.
- Requires the dividend calendar (§5). If the calendar is unavailable for a ticker, log it and **fall back to
  skip-entry conservatively** for that ticker on ambiguous days (fail safe, not fail open).
- **No-lookahead (backtest):** a dividend/ex-date may gate an entry only if it was **known as-of the decision day**
  (`declared_date`/`as_of` ≤ decision `ts`) — never use a dividend the model could not yet have known.
- **Freshness/lead-time (live):** load the calendar with enough forward horizon that any ex-date within
  `dividend_gap_block_days + 1` trading days is known **before** the entry decision; treat a **late-declared**
  dividend whose derived ex_date already falls inside the window as data-ambiguous → **skip-entry**.
- **Trading-day arithmetic:** "`last_buy_date` + 1 trading day" uses the **MOEX trading calendar**, not calendar +1.

## 7. Corporate actions — splits & ticker/uid history
- **Split adjustment:** back-adjust historical prices by the split ratio and adjust volume; set
  `candles.adjusted = true`. **Resolved `whq6u1gxe`: there is NO T-Invest API for splits/corp-actions → source
  MOEX ISS** (`/iss/statistics/engines/stock/splits[/{sec}]`, `/iss/cci/corp-actions`). *(Empirical: verify
  whether T-Invest D1 share candles already arrive split-adjusted before mixing sources.)*
- **Ticker/uid changes:** stitched via `instrument_reference.identifier_history` (JSON). Source = MOEX ISS
  `…/shares/securities/changeover` (confirmed **TCSG → T**, action_date 2024-11-27). Stitch on **ISIN** (the
  changeover log gives MOEX trading-code continuity; T-Invest `instrument_uid` continuity across a rename is
  unexposed — confirm ISIN as the stable join key at integration). The strategy keys on `instrument_uid`.

## 8. Worked НДФЛ fixture (the M2 test artifact) [LAW: backtest-honesty]
The tax layer has **no broker/sandbox reference**, so it is validated by a **hand-computed fixture**. M2 must ship
a test asserting the implementation reproduces this worked example.

**Example (illustrative — single ticker, FIFO, Инвестор 0.30%/side):**
- Buy 10 lots @ 100.00 ₽, commission 0.30% → cost = 1000.00 + 3.00 = **1003.00 ₽**.
- Sell 10 lots @ 106.00 ₽, commission 0.30% → proceeds = 1060.00 − 3.18 = **1056.82 ₽**.
- Realized gain (taxable base) = 1056.82 − 1003.00 = **53.82 ₽**.
- НДФЛ 13% = 53.82 × 0.13 = **6.9966 ₽ → 7.00 ₽** (rounding rule **[verify]** — RUB rounding to whole ₽ for НДФЛ).
- Layer-B net result = 53.82 − 7.00 = **46.82 ₽** (vs Layer-A economic +60.00 ₽ pre-cost).

> The exact rounding rule and partial-lot FIFO splitting are pinned in the M2 fixture; this example fixes the
> **method** (commission in base, FIFO, 13%, realize-on-close). All money via Quotation units/nano, not float —
> the rounded НДФЛ is encoded as `cash_events(type='tax')` units=<rubles>, nano=0; pin the rounding **direction**
> (math-round vs floor) in the M2 fixture. **Note:** this fixture asserts the **tax method only** — slippage is
> excluded by design (the M2 honest-backtest Layer-B verdict reads the full cost model incl. slippage, §1).

## 9. Owner-pending / [verify] summary
**Resolved by research `whq6u1gxe`** (see References `2026-06-27-tinvest-moex-tax-verify`):
- НДФЛ two-step 13/15% @ 2.4M on a **separate** securities base; broker = tax agent (year-end + on withdrawal).
- Dividends from `GetDividends` (`dividend_net` = GROSS; ex-date derived = last_buy_date + 1); separate dividend base.
- Splits / corp-actions / renames = MOEX ISS (no T-Invest API); stitch on ISIN.

**Remaining (empirical / owner):**
- RUB rounding rule for the НДФЛ amount — pin in the M2 worked fixture.
- Exact within-year withholding mechanics on partial withdrawals — reconcile against the live broker tax report.
- Resident-status guard (resident 13/15% vs non-resident 30%/15%) — enforce at runtime.
- Re-verify the tax law for the 2026 tax year before live.
- (Out of MVP scope: cross-year loss carry-forward; net-MCFTR reconstruction.)

## 10. Cross-references
- Frozen LAW: `docs/frozen-decisions.md` (backtest honesty, costs both sides). Spec: `docs/TZ.md` §12, §12.1, §13.
- Schema: [db-schema.md](db-schema.md) (`dividends`, `fills`, `cash_events`, `positions.close_reason`).
- Config: [config-and-secrets.md](config-and-secrets.md) (`dividend_gap_block_days`, `benchmarks`, `tariff`).
- Skill: `backtest-honesty`. Auditor: `lookahead-auditor` (gates the backtest surface).
