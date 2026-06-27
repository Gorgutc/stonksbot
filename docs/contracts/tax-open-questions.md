# Contract — Open tax questions & recommended defaults (companion to tax-and-dividends.md) (TZ §12.1, §18)

> **Status:** M0 contract, **open-question register (no code yet)**. This is the **companion** to
> [tax-and-dividends.md](tax-and-dividends.md): that file pins the **resolved** tax method; this file enumerates the
> **unresolved** tax questions, gives each a **recommended DEFAULT**, and marks every unverified item so the M2
> worked НДФЛ fixture (§8 of the tax contract) is built on **stated assumptions**, not silent guesses.
> **`docs/frozen-decisions.md` 🔒 wins.** **The verb of this doc is DOCUMENT + DEFAULT — it does NOT pin a rule.**
> A wrong pinned rule here would yield a wrong M2 fixture that the tests then assert against; therefore every
> open item carries **[verify before M2; re-verify 2026 tax law before live]** and the M2 fixture, not this doc,
> is the place the value is finally locked.
>
> **[owner-pending]** = the owner must confirm before locking. **[verify]** = empirical / external-fact check
> still required (research `whq6u1gxe` is the resolved-facts source; anything not in it is still open).
> Money is always **Quotation** `units`/`nano` integers — never float (db-schema §1).

This contract feeds: the M2 worked НДФЛ fixture (tax-and-dividends §8), the after-tax Layer-B PnL journal
(TZ §12), the `cash_events(type='tax')` rows (db-schema §3.3), and the resident/non-resident startup guard.

---

## 1. What is already FROZEN / RESOLVED (do not reopen here)

These are resolved by research `whq6u1gxe` and pinned in tax-and-dividends.md (frozen-decisions.md lists taxes
as an open issue, not a tax source) — listed so this register cannot be read as reopening them. They were
resolved on paper; re-verify before live. Cite the real source; do **not** restate a value as if it were still
open. **`docs/frozen-decisions.md` 🔒 wins** as the risk/safety precedence, but it contains no tax rules.

| Resolved rule | Source | TZ |
| --- | --- | --- |
| НДФЛ **13%**, **15%** on the annual taxable portion above **2.4M ₽** (at the 10 000 ₽ pilot → effectively flat 13%, bracket kept as a config constant) *(resolved on paper; still subject to the §8 pre-live re-verify of 2026 tax law)*. | research `whq6u1gxe`; tax-and-dividends §2 | §12.1, §20 |
| **Separate tax bases** — sale gains and dividends are taxed **independently** (dividends NOT netted against trading losses). | tax-and-dividends §4–§5; `whq6u1gxe` | §12.1 |
| **Cost basis = FIFO** (RU broker convention); commissions reduce the taxable base (buy + sell). | tax-and-dividends §2; `whq6u1gxe` | §12.1 |
| **Broker = tax agent** (НК РФ ст. 214.1) — T-Bank computes/withholds/remits; the bot number is an **estimate**, the broker tax report is the legal source of truth. | tax-and-dividends §2; `whq6u1gxe` | §12.1 |
| **ЛДВ (3-year exemption) NOT modeled** — the 2–6 week (max 8) horizon [LAW] is always far below 3 years. | tax-and-dividends §3 | §12.1 |
| **Realize-on-close** — tax accrues on **sell**, not on open/unrealized marks; accrued into Layer B at realization. | tax-and-dividends §1–§2 | §12 |
| **Tax stored as `cash_events(type='tax')`** units=<rubles>, nano per the pinned rounding, currency RUB. | db-schema §2, §3.3 | §5.1 |

> **Cross-year loss carry-forward = OUT of MVP scope (documented exclusion).** The pilot runs short (a single
> reporting period), so multi-year loss carry-forward (НК РФ ст. 220.1) is **intentionally not modeled**. Noted
> for completeness only; flagged here so its absence is a **stated exclusion**, never an accidental omission.
> (Mirrors tax-and-dividends §4 and §9.)

## 2. Open question O1 — RUB rounding DIRECTION of the НДФЛ amount

The НДФЛ amount is computed as `base × rate` and stored as whole-ruble `cash_events(type='tax')` units (nano=0).
The arithmetic direction is **not yet verified** and it changes the M2 fixture by up to 1 ₽.

| Field | Value |
| --- | --- |
| Status | **[verify before M2 fixture; re-verify 2026 tax law before live]** |
| Question | Is the per-realization НДФЛ rounded **math-round** (round-half to nearest whole ₽) or **floor/truncate** to whole ₽? НК РФ ст. 52 п. 6 prescribes math-rounding of the *total* tax to whole rubles; whether the broker applies it per-realization and which half-rule it uses is empirical. |
| Recommended DEFAULT | **math-round half-up to a whole ₽** (НК РФ ст. 52 п. 6: amounts <50 коп dropped, ≥50 коп rounded to 1 ₽), applied as a non-float operation on the Quotation base. |
| Why this default | Matches the statutory text and is the least-surprising for reconciliation against the broker tax report; the worked example in tax-and-dividends §8 (`6.9966 → 7.00 ₽`) is consistent with it. |
| What it touches | M2 worked fixture assert value; `cash_events(type='tax')` units; Layer-B net per trade. |
| Re-verify against | The live broker tax report (per-realization vs year-end aggregate rounding) — reconcile, never assume. |

```text
# rounding-direction enum to PIN in the M2 fixture (NOT decided here — default shown)
tax_rounding_direction = math_round_half_up   # default recommendation [verify before M2]
                       | floor                # truncate toward zero (rejected as default)
                       | bankers_round        # round-half-to-even (rejected as default)
# Encoding (db-schema §1): operate on the Quotation base, emit cash_events(type='tax') units=<whole ₽>, nano=0.
```

> **DO NOT** treat `math_round_half_up` as pinned. The M2 fixture pins it; this doc only recommends it. TZ §12.1
> explicitly carries `[verify exact bracket application §20]`, and tax-and-dividends §8 marks the rounding rule
> `[verify]`.

## 3. Open question O2 — Resident / non-resident rate guard (13/15% vs 30%)

A Russian **tax resident** pays 13/15%; a **non-resident** pays **30%** on sale gains (and 15% on dividends).
The tax-and-dividends contract requires a **resident guard** but the runtime mechanism is open.

| Field | Value |
| --- | --- |
| Status | **[owner-pending] + [verify before M2; re-verify 2026 tax law before live]** |
| Question | (a) What is the configured/assumed residency status of the pilot account? (b) How is it asserted at runtime — config flag, or read from the broker — and what happens on mismatch? |
| Recommended DEFAULT | Treat the account as **resident (13/15%)** AND add a config flag `tax.resident` (default `true`) that the resident guard checks; if `tax.resident = false` the after-tax layer is **not validated for MVP** → block the live track and surface the limitation (the bot is built for the resident case). |
| Why this default | The owner is a resident operating a domestic pilot; the 30% non-resident path is out of the proven scope. Failing loud on `false` is fail-safe — it never silently applies the wrong (lower) rate. |
| What it touches | The НДФЛ rate selection feeding Layer B; a startup guard alongside the `account_id` guard (config §3). |
| Re-verify against | Residency is a 183-day-rule fact, not a static setting — re-verify before live and at year boundary; the broker report reflects the rate actually applied. |

```text
# NEW config key to PIN at M2 (companion to config-and-secrets §2.1) — default shown, NOT decided here
tax.resident : bool = true   # [owner-pending]; false => after-tax layer unvalidated => block live track
# Resident => sale base 13/15% @2.4M, dividend base 13/15%. Non-resident => 30% sale / 15% dividend (UNMODELED).
```

> **Frozen-rate note:** the **13/15% @2.4M** numbers themselves are frozen (tax-and-dividends §2) — this question
> is only about *which rate set applies* (the resident guard), never about changing the frozen percentages.

## 4. Open question O3 — Partial-lot FIFO splitting

FIFO is frozen, but the **mechanics of splitting a single buy lot across multiple sells** (and the reverse) are
not yet pinned, and they determine the per-closed-lot realized gain the tax base is built from.

| Field | Value |
| --- | --- |
| Status | **[verify before M2 fixture; re-verify 2026 tax law before live]** |
| Question | When a sell consumes only part of an open FIFO lot (or spans several lots), how is cost basis apportioned, and is per-share cost carried as an exact rational or a rounded Quotation? Does residual basis stay with the unsold remainder of the lot? |
| Recommended DEFAULT | **Quantity-proportional FIFO** with **exact (integer Quotation / Decimal) basis arithmetic, no intermediate float**: the consumed quantity carries its share of `(cost_units, cost_nano)` pro-rata; the unconsumed remainder retains the residual basis; rounding to whole ₽ happens **only at the final НДФЛ amount** (O1), never on intermediate per-lot basis. |
| Why this default | Matches FIFO + commissions-in-base (frozen) and the no-float money rule (db-schema §1). Rounding only at the tax step avoids accumulating per-lot rounding drift that would desync from the broker report. |
| What it touches | Realized-gain-per-closed-lot in the journal + backtest; the M2 fixture must include a **partial-lot** case, not only the whole-lot example in tax-and-dividends §8. |
| Re-verify against | A real partial-fill sequence reconciled to the broker tax report. |

```text
# FIFO partial-lot policy to PIN in the M2 fixture — default shown, NOT decided here
fifo_partial_lot = proportional_exact   # consumed qty takes pro-rata basis; remainder keeps residual
                 | whole_lot_only       # (rejected) cannot represent partial fills (db-schema fills.qty allows partial)
# Intermediate basis: exact Quotation/Decimal, NO float. Round to whole ₽ ONLY at the final НДФЛ amount (O1).
```

> The strategy is `risk.max_open_positions = 1` (config §2.5) with single-attempt entries, so partial fills are the
> realistic source of multi-lot FIFO (db-schema `fills.qty` allows a partial fill; `orders.state` has
> `partially_filled`). The fixture must still exercise the split path so the implementation is correct under
> partial fills, not only the happy path.

## 5. Open question O4 — Within-period loss-netting rule

Within-period netting of sale gains and losses is frozen (tax-and-dividends §4), but the **exact aggregation**
(per-trade vs period-aggregate base, and ordering vs the 2.4M bracket) is not pinned.

| Field | Value |
| --- | --- |
| Status | **[verify before M2 fixture; re-verify 2026 tax law before live]** |
| Question | Is the sale-gain base netted **per reporting period in aggregate** (sum of all realized gains − sum of all realized losses, taxed once) or accrued **per realization** for the equity curve? How does netting interact with the 2.4M progressive boundary? Are the sale base and dividend base **kept strictly separate** during netting (frozen: yes)? |
| Recommended DEFAULT | **Two-track:** (1) for the **Layer-B equity curve**, accrue НДФЛ **per realization** on the running netted sale-gain base (consistent with tax-and-dividends §1 "accrue at each realization"), allowing a realized **loss to reduce** the accrued tax within the period; (2) the **period-end reconciliation** number is the aggregate netted base × rate. Sale base and dividend base are **never netted against each other** (frozen). The 2.4M bracket applies to the **aggregate annual** taxable base only (unreachable at the 10k pilot → flat 13% in practice). |
| Why this default | Keeps mid-period equity honest (per-realization accrual is the frozen choice) while making the period-end number match the broker's aggregate-base computation; preserves the frozen separate-bases rule. |
| What it touches | Layer-B running equity/drawdown/expectancy; the period-end reconciliation; the M2 fixture should include a **gain-then-loss** sequence to show netting reduces accrued tax. |
| Re-verify against | The broker tax report's period aggregation; confirm the broker nets within the period the same way before trusting the bot estimate. |

```text
# Loss-netting policy to PIN in the M2 fixture — default shown, NOT decided here
loss_netting = per_realization_accrual + period_aggregate_reconcile   # default recommendation
             | period_aggregate_only                                  # (rejected: flatters mid-period equity)
# INVARIANT (frozen, not open): sale-gain base and dividend base are netted SEPARATELY, never against each other.
# Cross-year carry-forward: OUT of MVP scope (documented exclusion) — netting is WITHIN one period only.
```

## 6. Recommended-defaults summary (all DEFAULT, none PINNED)

| ID | Open question | Recommended DEFAULT | Status flag |
| --- | --- | --- | --- |
| O1 | RUB rounding direction of НДФЛ | `math_round_half_up` to whole ₽ (НК РФ ст. 52 п. 6) | [verify before M2; re-verify 2026 before live] |
| O2 | Resident / non-resident rate guard | `tax.resident = true` (13/15%); `false` → block live track | [owner-pending] + [verify] |
| O3 | Partial-lot FIFO splitting | `proportional_exact`, no intermediate float, round only at tax step | [verify before M2; re-verify before live] |
| O4 | Within-period loss-netting rule | per-realization accrual + period-aggregate reconcile; bases stay separate | [verify before M2; re-verify before live] |

> Each DEFAULT is a **recommendation to pin in the M2 worked fixture**, not a decision made in this doc. The M2
> fixture (tax-and-dividends §8) is the single place these become asserted values; until then the implementation
> must read them as open with the defaults above.

## 7. Frozen invariants honored

- **13/15% @ 2.4M, separate bases, FIFO, broker = tax agent, no ЛДВ** — restated as resolved (§1), **not**
  reopened; every open question (§2–§5) is explicitly scoped to a mechanism *around* these frozen values, never
  to the values themselves. (`whq6u1gxe`; tax-and-dividends §2–§5 — frozen-decisions lists taxes as open)
- **No-float money** — every default operates on **Quotation `units`/`nano`** integers / Decimal; rounding to
  whole ₽ happens **only** at the final НДФЛ amount and is stored as `cash_events(type='tax')` units, nano=0.
  (db-schema §1, §2; backtest-honesty)
- **Realize-on-close / accrue-at-realization** — O4's default keeps the frozen per-realization accrual for the
  Layer-B equity curve. (tax-and-dividends §1)
- **Backtest honesty** — defaults never flatter the result: the resident default fails loud on non-resident
  (O2), loss-netting never overstates mid-period equity (O4), and intermediate rounding never silently shrinks
  the tax base (O1, O3). No default introduces lookahead. (backtest-honesty; `lookahead-auditor`)
- **Cross-year carry-forward = out-of-MVP documented exclusion** — stated, not silently dropped (§1, §5).
- **Broker report is the legal source of truth** — every default carries a "re-verify against the broker tax
  report" line; the bot number stays an estimate. (tax-and-dividends §2)
- **Same-change rule** — none of these defaults change a frozen value; if a re-verification ever contradicts a
  frozen rate/base, that is an owner decision + ADR + `docs/frozen-decisions.md` update, not an edit here.

## 8. Open questions / owner-pending

- **O1 [verify before M2; re-verify 2026 before live]** — RUB rounding direction (math-round vs floor); default
  `math_round_half_up`. Pinned in the M2 fixture, not here.
- **O2 [owner-pending] + [verify]** — resident/non-resident guard (13/15% vs 30%); default `tax.resident=true`,
  `false` blocks the live track. Needs the owner's residency confirmation and a runtime guard mechanism.
- **O3 [verify before M2; re-verify before live]** — partial-lot FIFO splitting; default `proportional_exact`,
  no intermediate float. The M2 fixture must add a partial-lot case.
- **O4 [verify before M2; re-verify before live]** — within-period loss-netting; default per-realization accrual
  + period-aggregate reconcile, bases kept separate. The M2 fixture must add a gain-then-loss case.
- **New config keys proposed (not yet in config-and-secrets):** `tax.resident` (O2), and the M2-fixture policy
  enums `tax_rounding_direction` (O1), `fifo_partial_lot` (O3), `loss_netting` (O4) — fold into
  config-and-secrets §2 only **after** the M2 fixture pins them.
- **Out of MVP scope (documented exclusions):** cross-year loss carry-forward; net-MCFTR reconstruction
  (tax-and-dividends §5).
- **General [verify]:** re-verify the entire 2026 tax law (rates, bracket boundary, residency rule, rounding)
  before any live trading; reconcile every computed number against the broker tax report.

## 9. Cross-references

- Companion: [tax-and-dividends.md](tax-and-dividends.md) (resolved tax method; §8 M2 worked fixture; §9 verify summary).
- Frozen LAW: `docs/frozen-decisions.md` (backtest honesty, costs both sides, no-float money).
- Schema: [db-schema.md](db-schema.md) (`cash_events.type='tax'`, `fills.qty` partial, `positions.close_reason`).
- Config: [config-and-secrets.md](config-and-secrets.md) (proposed `tax.resident`; §2.1 account; §2.8 tariff/costs).
- Spec: `docs/TZ.md` §12.1 (tax rules), §13 (backtest tax/costs), §18 (tax-fixture honesty test), §20 (verify).
- Skill: `backtest-honesty`. Auditor: `lookahead-auditor` + `risk-invariant-auditor` (gate the tax/backtest surface).
- Research: `whq6u1gxe` (`2026-06-27-tinvest-moex-tax-verify`) — resolved facts; anything beyond it stays open here.
