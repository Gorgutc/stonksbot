# Contract — Hand-computed НДФЛ tax fixture (M2 test oracle) (TZ §12.1, §18)

> **Status:** M0 contract, **hand-computed oracle (no code yet)**. The after-tax Layer-B PnL has **no
> broker/sandbox reference** (TZ §9: the sandbox models no taxes/dividends), so it is validated **only** against
> this hand-computed fixture. M2 ships a pytest that asserts the implementation reproduces the numbers below
> (TZ §18 "tax fixtures"). **`docs/frozen-decisions.md` 🔒 wins.**
>
> **PROVISIONAL — the asserted numbers are not final.** This fixture pins its values on the **recommended
> DEFAULTS** of [tax-open-questions.md](tax-open-questions.md): O1 rounding direction `math_round_half_up`,
> O2 residency `tax.resident = true` (13/15%), O3 partial-lot `proportional_exact`, O4 netting
> `per_realization_accrual + period_aggregate_reconcile`. **Until O1 is verified `[verify before M2]`** every
> rounded НДФЛ amount and every Layer-B net below is marked **PROVISIONAL**; a verified floor/banker's-round
> direction shifts the rounded values (Fixture A by 1 ₽, Fixture D by 1 ₽). **Until O2 is confirmed
> `[owner-pending]`** the 13% rate set itself is assumed (non-resident 30% sale / 15% dividend is unmodeled → blocks the live track).
> The frozen **percentages** (13/15% @ 2.4M, FIFO, separate bases, broker = tax agent, no ЛДВ) are **not**
> reopened here — only the mechanism *around* them is provisional.
>
> Money is always **Quotation** `units`/`nano` integers — never float (db-schema §1). The rounded НДФЛ is stored
> as `cash_events(type='tax')` units=<whole ₽>, nano=0 (db-schema §2, §3.3).

This contract feeds: the M2 pytest tax oracle (tax-and-dividends §8; TZ §18), the after-tax Layer-B PnL journal
(TZ §12), the `cash_events(type='tax')` / `cash_events(type='dividend')` rows (db-schema §3.3), and the
resident-guard rate selection (tax-open-questions O2).

---

## 1. Fixture parameters (the assumptions every number below depends on)

All four fixtures use **one configured parameter set**, stated here so the oracle is reproducible. None of these
are decided in this doc — they cite their frozen / default source.

| Parameter | Value | Source |
| --- | --- | --- |
| `tariff` | `investor` → `costs.investor_commission_bps = 30` (0.30%/side, no monthly fee) | config §2.8 [verified]; binding tariff owner-pending (M3) |
| Slippage | **excluded from this fixture** (tax-method oracle only; the full Layer-B verdict adds `costs.slippage_bps`, tax-and-dividends §1) | tax-and-dividends §8; config §2.8 |
| Min commission | `costs.min_commission_units/nano = 0,10000000` (0.01 ₽ floor); not binding in these fixtures (all commissions ≥ 0.01 ₽) | config §2.8 |
| Cost basis | **FIFO**, commissions in base both sides | tax-and-dividends §2 (research whq6u1gxe) |
| НДФЛ rate set | **resident 13/15% (15% above 2.4M; flat 13% at the pilot)**; 15% only on the annual base above 2.4M ₽ (unreachable at the 10k pilot → flat 13%) | tax-and-dividends §2 (research whq6u1gxe); O2 default `tax.resident=true` |
| `tax_rounding_direction` | `math_round_half_up` to whole ₽ (НК РФ ст. 52 п. 6) **[verify before M2 — O1]** | tax-open-questions O1 (DEFAULT) |
| `fifo_partial_lot` | `proportional_exact` (no intermediate float; round only at the final tax step) **[verify — O3]** | tax-open-questions O3 (DEFAULT) |
| `loss_netting` | `per_realization_accrual + period_aggregate_reconcile`; sale & dividend bases **separate** **[verify — O4]** | tax-open-questions O4 (DEFAULT) |
| `lot` (shares per lot) | **`lot = 1`** in these fixtures, so "lots" == "shares" and the numbers match the worked example in tax-and-dividends §8 verbatim | fixture simplification (instrument_reference.lot is per-ticker in reality) |
| Reporting period | **one** period (single tax year) — cross-year carry-forward is out of MVP scope (documented exclusion) | tax-open-questions §1; tax-and-dividends §4 |

```text
# Policy enums asserted by this fixture (mirrors tax-open-questions §2,§4,§5 — DEFAULTS, pinned here for M2)
tax_rounding_direction = math_round_half_up     # [verify before M2 — O1]; floor | bankers_round rejected as default
fifo_partial_lot       = proportional_exact     # [verify — O3]; whole_lot_only rejected (cannot represent partial fills)
loss_netting           = per_realization_accrual + period_aggregate_reconcile   # [verify — O4]
tax.resident           = true                   # [owner-pending — O2]; false => 30% sale / 15% dividend UNMODELED => block live track
# Encoding: operate on Quotation/Decimal base; emit cash_events(type='tax') units=<whole ₽>, nano=0 (db-schema §1).
```

> **Rounding helper (the operation O1 pins):** `НДФЛ_whole_₽ = round_half_up(base_₽ × rate)` computed on the
> Decimal/Quotation base, **never** float, applied **once** at the final per-realization (or period-aggregate)
> amount — never on intermediate per-lot basis (O3). НК РФ ст. 52 п. 6: <50 коп dropped, ≥50 коп → 1 ₽.

## 2. Fixture A — whole-lot single round trip (the canonical example) [PROVISIONAL]

Mirrors tax-and-dividends §8 exactly (single ticker, FIFO, Инвестор 0.30%/side), restated as an asserted oracle.

**Inputs (explicit prices / qty / dates):**

| Step | Date (D1 close) | Action | Qty (lot=1) | Price ₽ | Commission ₽ (0.30%) |
| --- | --- | --- | --- | --- | --- |
| 1 | 2026-02-02 | BUY | 10 | 100.00 | 3.00 |
| 2 | 2026-02-16 | SELL | 10 | 106.00 | 3.18 |

**Worked computation:**

| Quantity | ₽ | Note |
| --- | --- | --- |
| Buy gross | 1000.00 | 100.00 × 10 |
| Buy commission | 3.00 | 1000.00 × 0.0030 |
| **Buy cost basis** | **1003.00** | gross + commission (commission in base) |
| Sell gross | 1060.00 | 106.00 × 10 |
| Sell commission | 3.18 | 1060.00 × 0.0030 |
| **Sell proceeds** | **1056.82** | gross − commission |
| **Realized gain (sale base)** | **53.82** | 1056.82 − 1003.00 |
| НДФЛ 13% (raw) | 6.9966 | 53.82 × 0.13 |
| **НДФЛ (rounded, half-up)** | **7.00** | `round_half_up(6.9966)` — **PROVISIONAL (O1)**; floor would give **6.00** |
| **Layer-B net result** | **46.82** | 53.82 − 7.00 |
| Layer-A economic (pre-cost, pre-tax) | +60.00 | (106.00 − 100.00) × 10 — diagnostic only |

```text
# Oracle the M2 pytest asserts (Fixture A) — units/nano per db-schema §1
sale_base            = 53.82   ->  cash basis: units=53,   nano=820000000
ndfl_raw             = 6.9966  (Decimal, never stored as float)
ndfl_rounded_half_up = 7       ->  cash_events(type='tax')   units=7,  nano=0   # PROVISIONAL O1
layer_b_net          = 46.82   ->  units=46,  nano=820000000
# If O1 verifies to `floor`: ndfl_rounded=6, layer_b_net=47.82 — the test's expected value flips. [verify before M2]
```

## 3. Fixture B — partial-lot FIFO split (`proportional_exact`, O3) [PROVISIONAL]

Exercises the **multi-lot FIFO split** path: two buys form the FIFO queue, one sell consumes all of lot 1 plus
**part** of lot 2; the residual basis stays with the unsold remainder. Required because `max_open_positions = 1`
with single-attempt entries makes **partial fills** (db-schema `fills.qty`, `orders.state='partially_filled'`)
the realistic source of multi-lot FIFO — the happy-path whole-lot case (Fixture A) does not cover it.

**Inputs:**

| Step | Date | Action | Qty (lot=1) | Price ₽ | Commission ₽ (0.30%) |
| --- | --- | --- | --- | --- | --- |
| 1 | 2026-03-02 | BUY (lot 1) | 5 | 100.00 | 1.50 |
| 2 | 2026-03-09 | BUY (lot 2) | 5 | 110.00 | 1.65 |
| 3 | 2026-03-20 | SELL | 8 | 120.00 | 2.88 |

**FIFO basis (commission in base, exact Decimal — no intermediate rounding):**

| Lot | Qty | Gross ₽ | Commission ₽ | **Lot basis ₽** | Per-share basis ₽ |
| --- | --- | --- | --- | --- | --- |
| lot 1 | 5 | 500.00 | 1.50 | **501.50** | 100.30 |
| lot 2 | 5 | 550.00 | 1.65 | **551.65** | 110.33 |

**Sell of 8 consumes lot 1 (5, all) + lot 2 (3 of 5, proportional):**

| Quantity | ₽ | Note |
| --- | --- | --- |
| Sell gross | 960.00 | 120.00 × 8 |
| Sell commission | 2.88 | 960.00 × 0.0030 |
| **Sell proceeds** | **957.12** | gross − commission |
| Consumed basis | 832.49 | lot1 501.50 + lot2 × (3/5) = 501.50 + 330.99 |
| Residual basis (unsold 2 of lot 2) | 220.66 | lot2 × (2/5); **stays with the open remainder** |
| basis split check | 832.49 + 220.66 = 1053.15 = 501.50 + 551.65 | no basis lost to rounding (O3) |
| **Realized gain (sale base)** | **124.63** | 957.12 − 832.49 |
| НДФЛ 13% (raw) | 16.2019 | 124.63 × 0.13 |
| **НДФЛ (rounded, half-up)** | **16.00** | `round_half_up(16.2019)`; floor **also 16.00** here (not a discriminator) |
| **Layer-B net result** | **108.63** | 124.63 − 16.00 |

```text
# Oracle (Fixture B) — proportional_exact FIFO, round ONLY at the final tax amount
consumed_basis = 832.49   ->  units=832, nano=490000000
residual_basis = 220.66   ->  units=220, nano=660000000   # remains on the open position lot
sale_base      = 124.63   ->  units=124, nano=630000000
ndfl_rounded   = 16       ->  cash_events(type='tax') units=16, nano=0   # half-up == floor here
layer_b_net    = 108.63   ->  units=108, nano=630000000
# INVARIANT: intermediate per-lot basis is exact Decimal/Quotation; NEVER round per-lot (would desync the sum). [O3]
```

## 4. Fixture C — within-period gain-then-loss netting (O4) [PROVISIONAL]

Exercises **within-period loss netting** of the **sale-gain base** (frozen: gains/losses net inside the period;
cross-year carry-forward out of scope). Two round trips in one tax year: a gain then a loss. Demonstrates that a
realized **loss reduces** the accrued tax (per-realization accrual on the **running netted** base) and that the
**period-end aggregate** reconciliation number matches `netted_base × rate`.

**Inputs:**

| Trade | Buy date | Buy qty/px | Sell date | Sell qty/px | Realized ₽ |
| --- | --- | --- | --- | --- | --- |
| 1 (gain) | 2026-04-02 | 10 @ 100.00 | 2026-04-16 | 10 @ 106.00 | **+53.82** (= Fixture A) |
| 2 (loss) | 2026-05-04 | 10 @ 100.00 | 2026-05-18 | 10 @ 97.00 | **−35.91** |

Trade 2 detail: buy basis = 1000.00 + 3.00 = 1003.00; sell proceeds = 970.00 − 2.91 = 967.09; realized =
967.09 − 1003.00 = **−35.91**.

**Per-realization accrual on the running netted sale base (O4 default):**

| Event | Running netted base ₽ | Accrued tax (half-up) ₽ | Tax delta booked ₽ |
| --- | --- | --- | --- |
| after trade 1 | 53.82 | `round_half_up(53.82 × 0.13)` = **7.00** | +7.00 |
| after trade 2 (loss nets in) | 53.82 − 35.91 = **17.91** | `round_half_up(17.91 × 0.13)` = `round_half_up(2.3283)` = **2.00** | **−5.00** (loss reduces accrued tax) |

**Period-end aggregate reconciliation:** `round_half_up(17.91 × 0.13)` = **2.00 ₽** — matches the running
accrual end-state (both land on 2 ₽). Accrued tax **never goes below 0** within the period (if the netted base
were ≤ 0, accrued tax = 0; cross-year carry-forward of the unused loss is **out of MVP scope**).

```text
# Oracle (Fixture C) — netting reduces accrued tax; bases stay separate from dividends
trade1_base        = 53.82  ; accrued_tax_after_t1 = 7   # PROVISIONAL O1
trade2_base        = -35.91 ;                            # units=-35, nano=-910000000 (units/nano share sign, db-schema §1)
netted_sale_base   = 17.91  ; accrued_tax_after_t2 = 2   # tax DELTA at t2 = -5 (a refund of over-accrual)
period_aggregate_tax = 2    ->  cash_events(type='tax') units=2, nano=0
# INVARIANT (frozen): the sale-gain base is netted within ONE period; it is NEVER netted against the dividend base.
```

> The mid-period equity-curve honesty (per-realization accrual) is the frozen choice (tax-and-dividends §1) —
> the loss must reduce Layer-B tax **at realization**, not be deferred to year-end, else mid-period equity is
> overstated and Sharpe flattered (backtest-honesty). The period-end number is the broker-report reconciliation
> anchor.

## 5. Fixture D — dividend (SEPARATE base, NET cashflow) [PROVISIONAL]

Exercises the **separate dividend base**: dividend income is taxed **independently** (NOT netted against trading
losses), withheld at source, so the Layer-B cashflow is **NET**. This fixture is the cleanest **O1 discriminator**
(half-up vs floor differ here by 1 ₽).

**Inputs:** hold 10 shares across the record date; gross dividend 5.00 ₽/share (the T-Invest `GetDividends`
field `dividend_net` is **GROSS** — db-schema `dividends.gross_units/nano`, tax-and-dividends §5). Ex-date is
derived = `last_buy_date` + 1 trading day (no entry block conflict — fixture holds an existing position).

| Quantity | ₽ | Note |
| --- | --- | --- |
| Shares held over record date | 10 | from an open position |
| Gross dividend / share | 5.00 | `dividends.gross_*` (API `dividend_net` = GROSS) |
| **Gross dividend total** | **50.00** | 5.00 × 10 |
| Dividend НДФЛ 13% (raw) | 6.50 | 50.00 × 0.13 |
| **Dividend НДФЛ (rounded, half-up)** | **7.00** | `round_half_up(6.50)` — **PROVISIONAL (O1)**; floor would give **6.00** |
| **Net dividend (Layer-B cashflow)** | **43.00** | 50.00 − 7.00 |

```text
# Oracle (Fixture D) — separate dividend base, net cashflow
gross_dividend = 50.00  ->  units=50, nano=0
div_ndfl_raw   = 6.50   (exactly .50 -> the half-rule decides the whole ₽)
div_ndfl       = 7      ->  cash_events(type='tax')      units=7,  nano=0   # PROVISIONAL O1 (floor => 6)
net_dividend   = 43     ->  cash_events(type='dividend') units=43, nano=0
# INVARIANT (frozen): dividend base is taxed SEPARATELY; never netted against the sale-gain base (Fixture C).
# Benchmark note: strategy NET dividends are compared to the GROSS MCFTR (documented asymmetry, tax-and-dividends §5).
```

> **Why Fixture D is the O1 test discriminator:** `6.50` sits exactly on the half boundary, so `math_round_half_up`
> (→ 7) and `floor` (→ 6) diverge by a full ₽. The M2 pytest should keep this case so the verified rounding
> direction is unambiguously asserted once O1 closes.

## 6. Combined-period assertion (two separate bases co-exist) [PROVISIONAL]

If Fixtures C and D occur in the **same** reporting period, the M2 oracle must keep the bases **separate**:

| Base | Taxable base ₽ | НДФЛ ₽ (half-up) | Source fixture |
| --- | --- | --- | --- |
| Sale-gain base (netted) | 17.91 | 2.00 | Fixture C |
| Dividend base | 50.00 | 7.00 | Fixture D |
| **Total withheld (sum of separate computations)** | — | **9.00** | C + D |

The total is the **sum of two independently-computed bases**, never `round_half_up((17.91 + 50.00) × 0.13)`
(= `round_half_up(8.8283)` = 9 here by coincidence, but computing on a merged base is the **forbidden** path —
it would net the dividend against a trading loss, violating the frozen separate-bases rule).

> **HARD requirement (the coincidence trap):** the merged-base total (9 ₽) and the separate-bases total (9 ₽)
> are **numerically equal here by accident** — so asserting the **total alone would let a merged-base bug pass**.
> The M2 oracle MUST therefore assert **two distinct `cash_events(type='tax')` rows** — `units=2, nano=0`
> (sale-gain base, Fixture C) **and** `units=7, nano=0` (dividend base, Fixture D) — **not** a single merged
> `units=9` row and **not** only the 9 ₽ sum. A merged computation produces one row and would still match the
> total, so the two-row assertion is the only check that catches it.

## 7. 13/15% progressive bracket — pinned as a config constant, unreachable at pilot scale

The fixtures above all sit in the flat-13% region. To keep the bracket **modeled, not hard-coded away** (frozen:
"keep the bracket as a config constant"), the oracle includes a **scale check** asserting the two-step formula —
run on a synthetic large annual base, never reachable by the 10k pilot:

```text
# Bracket formula (annual taxable securities base, resident) — asserted on a synthetic base
THRESH = 2_400_000 ₽
tax(base) = base * 0.13                              if base <= THRESH
          = THRESH * 0.13 + (base - THRESH) * 0.15  if base >  THRESH
# Scale-check assertions (synthetic, NOT a pilot path):
tax(2_400_000) = 312_000.00          # exactly at the boundary -> flat 13%
tax(2_500_000) = 327_000.00          # 2.4M*0.13 + 0.1M*0.15 = 312_000 + 15_000
# Pilot reality: at risk.capital_rub = 10_000 the annual base can never approach 2.4M -> effectively flat 13%.
```

> This check guards against a future refactor silently dropping the 15% step. The numbers are **frozen-LAW
> arithmetic** (13/15% @ 2.4M is frozen), so they are NOT marked provisional — only the *rounding* of the
> per-realization amounts (O1) and the *rate-set selection* (O2 residency) are provisional.

## 8. Frozen invariants honored

- **НДФЛ FIFO** — Fixtures A/B/C compute realized gain per closed lot via FIFO; Fixture B exercises the
  partial-lot split with commission-in-base. (tax-and-dividends §2; research whq6u1gxe)
- **Separate bases** — the sale-gain base (A/B/C) and the dividend base (D) are computed and taxed
  **independently**; §6 forbids merging them; a trading loss never reduces dividend tax.
  (tax-and-dividends §4–§5; research whq6u1gxe)
- **Broker = tax agent** — every fixture is labelled the **bot's estimate**; the broker tax report is the legal
  source of truth and the reconciliation anchor (the period-end number in C exists for exactly this).
  (tax-and-dividends §2)
- **Net dividends** — Fixture D's Layer-B cashflow is NET (withheld at source); the gross figure is only the
  pre-tax input. (tax-and-dividends §5)
- **No ЛДВ** — the 2–6 week (max 8) horizon is far below the 3-year threshold; no long-term exemption is applied
  in any fixture. (tax-and-dividends §3)
- **No-float money** — every base and amount is Decimal/Quotation; rounding to whole ₽ happens **only** at the
  final НДФЛ amount (never per-lot); stored as `cash_events(type='tax')` units, nano=0 (units/nano share sign,
  see Fixture C's negative loss). (db-schema §1, §2; tax-open-questions O1, O3)
- **Realize-on-close / accrue-at-realization** — tax accrues on **sell** (A/B) and per realization on the running
  netted base (C); dividends accrue when credited (D); nothing accrues on unrealized marks. (tax-and-dividends §1)
- **Backtest honesty** — commissions applied **both sides**; the fixture excludes slippage **by design** (it is
  the tax-method oracle; the full Layer-B verdict adds `costs.slippage_bps`, tax-and-dividends §1); no fixture flatters the result
  (the loss in C genuinely reduces tax, not the reverse); no lookahead (all dates are D1-close, sells follow buys).
  (backtest-honesty; frozen-decisions costs-both-sides)
- **Cross-year carry-forward = out-of-MVP documented exclusion** — netting in C is **within one period** only;
  an unused net loss is not carried forward. (tax-open-questions §1; tax-and-dividends §4)
- **Same-change rule** — no fixture changes a frozen value; if a re-verification (O1 direction, O2 residency)
  ever contradicts a frozen rate/base, that is an owner decision + ADR + `docs/frozen-decisions.md` update — the
  provisional numbers here are then re-pinned in the same change, not edited silently.

## 9. Open questions / owner-pending

- **O1 [verify before M2; re-verify 2026 before live]** — RUB rounding direction. Fixtures pin
  `math_round_half_up`; **Fixture A** (7 vs 6) and **Fixture D** (7 vs 6) are the discriminators. Until verified,
  every rounded НДФЛ and Layer-B net above is **PROVISIONAL**. (tax-open-questions O1)
- **O2 [owner-pending] + [verify]** — resident/non-resident rate set. Fixtures assume `tax.resident = true`
  (13/15%). If `false`, the 30%/15% non-resident path is **UNMODELED** → block the live track; this fixture does
  not cover it. Needs the owner's residency confirmation + runtime guard. (tax-open-questions O2)
- **O3 [verify before M2; re-verify before live]** — partial-lot FIFO. Fixture B pins `proportional_exact`
  (no intermediate float). Reconcile against a real partial-fill sequence in the broker tax report.
  (tax-open-questions O3)
- **O4 [verify before M2; re-verify before live]** — within-period netting. Fixture C pins per-realization
  accrual + period-aggregate reconcile; bases kept separate. Confirm the broker nets the same way within a period.
  (tax-open-questions O4)
- **Withholding-timing caveat (not a PnL difference)** — the broker withholds at year-end / on withdrawal, but
  the fixtures **accrue at realization** for the equity curve. The fixture asserts accrual, not the cash-withdrawal
  timing; reconcile the actual withholding against the broker report. (tax-and-dividends §2)
- **Lot-size simplification** — fixtures use `lot = 1` so "lots" == "shares"; the real `instrument_reference.lot`
  is per-ticker (e.g. 10) and the implementation must scale qty by `lot`. The **method** (FIFO, commission-in-base,
  13%, separate bases, round-only-at-tax) is lot-size-independent; M2 may add a `lot > 1` variant.
- **Config keys not yet folded into config-and-secrets** — `tax.resident` (O2) and the policy enums
  `tax_rounding_direction` / `fifo_partial_lot` / `loss_netting`; fold into config-and-secrets §2 **only after**
  this fixture's defaults are verified/owner-confirmed. (tax-open-questions §8)
- **General [verify]** — re-verify the entire 2026 tax law (rates, 2.4M boundary, residency rule, rounding)
  before any live trading; every computed number stays an **estimate** until reconciled against the broker tax
  report. (tax-and-dividends §9; tax-open-questions §8)

## 10. Cross-references

- Companion (resolved method + §8 worked example this oracle expands): [tax-and-dividends.md](tax-and-dividends.md).
- Companion (open questions + recommended defaults this oracle pins): [tax-open-questions.md](tax-open-questions.md).
- Frozen LAW: `docs/frozen-decisions.md` (no-float money, costs both sides, backtest honesty). **Tax rules**
  (НДФЛ FIFO, separate bases, broker = tax agent, no ЛДВ) → [tax-and-dividends.md](tax-and-dividends.md) §2 +
  research whq6u1gxe (frozen-decisions.md lists taxes as an open issue, not a frozen rule).
- Schema: [db-schema.md](db-schema.md) (`cash_events.type ∈ {tax,dividend}`, `fills.qty` partial,
  `dividends.gross_*`, units/nano sign rule, `orders.state='partially_filled'`).
- Config: [config-and-secrets.md](config-and-secrets.md) (`tariff`/`costs.*`, proposed `tax.resident`,
  `risk.capital_rub`).
- Spec: `docs/TZ.md` §12.1 (tax rules), §13 (backtest tax/costs), §18 (tax-fixture honesty test), §20 (verify).
- Skill: `backtest-honesty`. Auditor: `lookahead-auditor` + `risk-invariant-auditor` (gate the tax/backtest surface).
- Research: `whq6u1gxe` (`2026-06-27-tinvest-moex-tax-verify`) — resolved facts; anything beyond it stays open.
