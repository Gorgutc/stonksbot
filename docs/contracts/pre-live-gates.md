# Contract — Pre-live gates / go-live criteria (TZ §14, §15)

> **Status:** M6 contract, **resolved on paper (no code yet)**. This is the **single binary checklist**
> that **M6b asserts against** before the bot may run in live `confirm` mode on the dedicated 10 000 ₽
> account. It operationalizes the frozen **phased-path LAW** (no skipping) and the frozen
> **manual-owner-approval-is-final** gate. **`docs/frozen-decisions.md` 🔒 wins** on any conflict — this
> contract may only *detail* the gate, never weaken it.
>
> **[LAW]** = mirrors a frozen invariant (changeable only by owner decision + ADR + same-change rule).
> **[owner-pending]** = a value the owner must confirm before it is locked (never silently fixed here).
> **[verify]** = an empirical fact re-checked against live T-Invest/MOEX before the relevant code ships.
>
> Each gate item is a **checkbox with a single measurable PASS condition**. A gate is PASS **only** when
> its condition is objectively met and evidenced; absence of evidence is FAIL, not PASS. **Sandbox is
> never proof** [LAW] — a green sandbox run does not satisfy any economic gate.
> Pairs with [db-schema.md](db-schema.md), [config-and-secrets.md](config-and-secrets.md),
> [tax-and-dividends.md](tax-and-dividends.md).

---

## 1. Where this gate sits (phased path — frozen, no skipping) [LAW]

The frozen phased path is `research → backtest → walk-forward → paper/sandbox (≥30 days) → live confirm →
(much later) auto_small` (frozen-decisions.md, "Scope & product shape" (phased-path row)). This contract is the **final transition guard** of that
path: the **paper/sandbox → live confirm** edge. It is checked at **M6b** (ROADMAP §M6); M6a (VPS prep)
runs in parallel and feeds Gate group **4** but does **not** by itself authorize go-live.

```text
M3 STOP gate (edge survives costs?) ──FAIL──▶ STOP live track (iterate within frozen constraints, or shelve)
        │ PASS
        ▼
M4 sandbox (adapter+risk+state machine) ─▶ M5 (Telegram+dashboard+journal) ─▶ M6 paper/sandbox ≥30d
        │
        ▼
  ┌─────────────────────────  M6b: THIS CONTRACT  ─────────────────────────┐
  │ Gate 1 backtest · Gate 2 paper/sandbox window · Gate 3 account+token    │
  │ safety · Gate 4 ops readiness · FINAL: manual owner approval [LAW]      │
  └────────────────────────────────────────────────────────────────────────┘
        │ all PASS + owner approves
        ▼
  live `confirm` (mode=confirm) on the dedicated 10 000 ₽ account
```

**Hard ordering rules (binary, no override):**
- **No gate may be skipped or deferred.** A *defer* never silences a *blocker*: a single FAIL item blocks
  go-live regardless of how many items pass (frozen-decisions.md, "Scope & product shape" (phased-path row)).
- **The FINAL owner-approval gate (§7) is the last gate** and cannot be pre-satisfied, delegated, or inferred
  from the technical gates passing [LAW]. LLM/Codex/Claude **never** check this box (frozen-decisions.md, "Scope & product shape" (LLM-never-trades row)).
- The transition flips `config.mode` (config §2.1) to `confirm`; it never enables `auto_small`
  (`TINVEST_TOKEN_LIVE_AUTO_SMALL` stays absent; `auto_small` DISABLED in MVP) [LAW].

---

## 2. Gate 1 — Backtest PASS for the chosen tariff (TZ §13, §14)

The honest 3-year backtest must be **PASS** (not WEAK PASS, not FAIL) under the **binding tariff** finalized
at M3. The gate reads **Layer B** (broker/tax PnL — commissions both sides + НДФЛ + net dividends), never
Layer A (tax-and-dividends §1).

```text
GATE-1 binding tariff      = config.tariff  ∈ {investor, trader}   # finalized M3 [owner-pending until M3]
GATE-1 backtest verdict    ∈ {PASS, WEAK_PASS, FAIL}               # only PASS satisfies this gate
```

| # | Checkbox | Measurable PASS condition | Cite |
| --- | --- | --- | --- |
| 1.1 | Binding tariff fixed | `config.tariff` is set to the M3-finalized value; **[owner-pending]** until M3 cost-sensitivity resolves it (do not assume `investor`). | TZ §2, §3 M3; config §2.8 |
| 1.2 | 3-year backtest = PASS for that tariff | Verdict = `PASS` per the locked gate criteria: return **≥ +2 pp vs equal-weight** AND **not worse than the index** (IMOEX/MCFTR) AND **max drawdown ≤ benchmark** AND **not one-lucky-trade**. `WEAK_PASS` and `FAIL` do **not** satisfy this gate. | TZ §13 "Gate criteria (locked)" |
| 1.3 | Costs modeled both sides under the binding tariff | Layer-B verdict applied the configured tariff (`costs.investor_commission_bps`=30 **or** `costs.trader_commission_bps`=5 **+ `costs.trader_monthly_fee_rub`=390 modeled at 10k**) + `costs.slippage_bps`=10/side + `costs.min_commission_units/nano` floor + iceberg parity. | TZ §13; config §2.8 [verified] |
| 1.4 | No-lookahead honored in the evidence | Signal computed only after the final D1 close per `config.close_definition`; entry next session; conservative both-side fills (entry fills only if day low ≤ limit; TP only if day high ≥ target; stop/MA-break gaps at the worse price). | TZ §13; frozen-decisions.md, "Strategy, data & backtest honesty" (no-lookahead + conservative-fills rows); db-schema §4 |
| 1.5 | Walk-forward / out-of-sample, not a single fit | `docs/evidence/walk-forward-latest.md` exists and records the rolling train/test result, robust params, **Deflated Sharpe with the recorded trial/config count**, and cost-sensitivity (commission / slippage / **fill-rate** break-even). A single in-sample backtest does **not** satisfy this gate. | TZ §13, §14; frozen-decisions.md, "Strategy, data & backtest honesty" (anti-overfitting row) |
| 1.6 | M3 STOP gate cleared | The recorded **explicit PASS/FAIL decision** at the M3 STOP gate is **PASS** (edge survives costs). If FAIL, the live track is stopped — **this entire contract is unreachable**. | TZ §14; ROADMAP §M3 |

> **Sandbox ≠ proof** [LAW]: none of Gate 1 may be evidenced by sandbox profitability — the T-Bank sandbox
> uses a flat 0.05% commission and simplified fills and models no taxes/dividends (config §2.8; frozen-decisions.md, "Known drift / owner decisions pending" (sandbox-not-live row)).

---

## 3. Gate 2 — ≥30-day paper/sandbox window, clean (TZ §14, §15)

A continuous **≥ 30-day** paper/sandbox window with **no critical execution issues**. The window may begin
early via the **signal-only lab journal** (TZ §14) but the execution-grade portion must exercise the full
confirm cycle (M4 sandbox + M5 paper mirror).

```text
GATE-2 window_start_date    = <YYYY-MM-DD>   # [verify] exact ≥30-day window dates (owner records actuals)
GATE-2 window_end_date      = <YYYY-MM-DD>   # window_end_date - window_start_date ≥ 30 calendar days
GATE-2 critical_issue_count = 0              # any open critical execution issue ⇒ FAIL
```

| # | Checkbox | Measurable PASS condition | Cite |
| --- | --- | --- | --- |
| 2.1 | ≥30-day continuous window | `window_end_date − window_start_date ≥ 30 days`, run without a gap that voids continuity. Exact dates are **[verify]** (owner records the actual window). | TZ §14, §15 |
| 2.2 | No critical execution issues | Zero open critical issues over the window: no duplicate/double-submitted orders (idempotency held), no stuck `reconcile_required` / `blocked_reconciliation_mismatch` left unresolved, no missed protective exit, no wrong-account action. | TZ §8, §14; frozen-decisions.md, "Order & risk rules" (idempotency/order_id row) + "Strategy, data & backtest honesty" (state-machine row) |
| 2.3 | Journal working (append-only audit trail) | `audit_journal` is populated and append-only (UPDATE/DELETE blocked by trigger) and FK-links the full chain `proposal → confirm → order → fill → position → exit` for every trade in the window; exportable. | TZ §12; db-schema §3.3, §4 |
| 2.4 | Risk limits demonstrably block bad trades | The window contains **evidenced** rejections proving each guard fires: at least one `signals.decision='risk_rejected'` and the relevant `signals.reason` skip codes (`lot_too_expensive`/`low_liquidity`/`wide_spread`/`not_trading`/`data_missing`/`data_conflict`); limit-only / no-margin / no-short normalizer rejects; market-regime block; ≤1 proposal/day and ≤1 open position respected; daily hard-stop blocks new entries. | TZ §7; frozen-decisions.md, "Order & risk rules" (limit-only / market-regime / portfolio-limits / risk-exits rows); db-schema §2 |
| 2.5 | Dashboard / Telegram consistent | The local dashboard (positions + signals w/ skip reasons + two-layer PnL + mode/status, bound `127.0.0.1`) and Telegram state agree with the DB for every checked point in the window (no divergence between observability surfaces and `control_state` / `positions` / `signals`). | TZ §10, §11; config §2.2 |
| 2.6 | Weekly reports clear | Each weekly report in the window produced cleanly: weekly + since-inception return, trades, open positions, commissions, **taxes (Layer B)**, skipped candidates, risk filters, errors, and a short verdict — readable and reconciling with the journal. | TZ §12 |
| 2.7 | Fill-model parity documented | The **backtest-assumed** fill model vs the **sandbox/paper-observed** fills are compared and the divergence is **documented** in evidence (drives the M4/M5 parity check). Parity is *documented*, not assumed — and it never re-reads sandbox economics as proof. | TZ §9, §13, §14; frozen-decisions.md, "Known drift / owner decisions pending" (sandbox-not-live row) |
| 2.8 | Two-layer PnL present & honest | The window reports both Layer A (economic, pre-cost) and Layer B (commissions + НДФЛ accrued at realization + net dividends, FIFO); the gate verdict reads Layer B. | TZ §12; tax §1, §2 |

---

## 4. Gate 3 — Account guard verified + per-mode token scope-checked (TZ §15, §16)

The dedicated-account guard and per-mode token policy must be **verified live** (not merely coded). These are
fail-closed startup checks — a missing/ambiguous account or an unexpected token scope **refuses to start** [LAW].

| # | Checkbox | Measurable PASS condition | Cite |
| --- | --- | --- | --- |
| 3.1 | Dedicated `account_id` set & guarded | `config.account_id` is the dedicated bot account; the guard **refuses to start** if it is missing/blank, or if multiple broker accounts exist with no exact match (no "pick first" fallback). | frozen-decisions.md, "Account & access" (dedicated-account + account-guard rows); config §3, §3.1; TZ §7, §15 |
| 3.2 | Account name + id shown on startup | The startup path logs + surfaces the broker **account name + id** for human verification (and re-confirms on any `account_id` change between runs). | config §3; frozen-decisions.md, "Account & access" (account-guard row) |
| 3.3 | Row-level account scoping verified | Every account-scoped row equals `config.account_id`, asserted **at submit AND at reconciliation**; no row carries a foreign account. The db-schema §4 "Account guard" bullet names the `orders.account_id` / `cash_events.account_id` (both NOT NULL) + `audit_journal.account_id` guard set; `positions.account_id` is NOT NULL in the DDL (§3.2) and equally guarded, though not named in that bullet. | consistent with db-schema §4 "Account guard"; frozen-decisions.md, "Account & access" (account-guard row) |
| 3.4 | Per-mode token present & scope-checked | The live token is `TINVEST_TOKEN_LIVE_CONFIRM` (per-mode; the `confirm`-mode token, distinct from `TINVEST_TOKEN_SANDBOX`). The **startup scope check BLOCKS trading** (refuses to start) if scope is missing / over-broad / not account-scoped — it warns never. `TINVEST_TOKEN_LIVE_AUTO_SMALL` is **absent** (auto_small DISABLED). | config §1; frozen-decisions.md, "Account & access" (token-policy row); TZ §9, §16 [LAW] |
| 3.5 | Account-scoped token feasibility resolved | Either the bot-account product type supports account-scoped tokens (verified) **or** the documented fallback is the `account_id` guard. Account-scoping is unavailable for Инвесткопилка / Счёт под ключ / Смарт-счёт. **[verify]** at M4 (empirical). | TZ §9, §20; config §6 |
| 3.6 | No secret leaks (scan gate green) | The secret-scan pre-commit gate passes: no token in code/config/logs/dashboard/Telegram; `.env` git-ignored; `.env.example` placeholders only; the `*_TOKEN` catch-all (incl. `DASHBOARD_AUTH_TOKEN`) finds nothing committed. | frozen-decisions.md, "Account & access" (token-policy row) + "Do-not-touch" (secrets bullet); config §5; TZ §16 [LAW] |
| 3.7 | Telegram whitelist set | `config.telegram_user_whitelist` contains the real owner user-id(s); non-whitelisted senders are ignored + logged. Ids are **[owner-pending]** (M5) — the gate FAILs while the list is empty. | TZ §10; config §2.2 |

---

## 5. Gate 4 — Operational readiness (M6a, parallel) (TZ §17)

M6a runs in parallel with the paper window. These items must be **ready** before go-live but do not by
themselves authorize it. Local-first is acceptable for the paper window; the VPS items are a go-live
prerequisite per the "VPS before live confirm" decision (TZ §2; ROADMAP M6a).

| # | Checkbox | Measurable PASS condition | Cite |
| --- | --- | --- | --- |
| 4.1 | Host resilience | Prevent-sleep + process supervisor/watchdog (auto-restart) + NTP/time sync are configured and demonstrated to survive a restart (with `control_state` `paused`/`killed`/`blocked_reconciliation_mismatch` persisting across the restart). | TZ §17; db-schema §3.3 |
| 4.2 | Startup reconciliation proven | On startup/restart the bot reconciles positions+orders before trading (retry 3× 60/180/300s; require 2 consecutive clean checks); a persistent mismatch lands in `blocked_reconciliation_mismatch` (block new entries; monitoring on; **RISK exits allowed; PROFIT/target exits FORBIDDEN**). | TZ §8; frozen-decisions.md, "Strategy, data & backtest honesty" (state-machine row); db-schema §2 |
| 4.3 | Backups | SQLite (local) / Postgres (VPS) DB backups + structured-log retention are configured and a restore has been test-run. | TZ §17 |
| 4.4 | VPS prepared (before live) | VPS provisioned: Docker Compose, systemd/auto-restart, **SQLite→Postgres migration** at the documented `db_switch_point`, secret store, firewall, dashboard via SSH tunnel/VPN (**never public**), DR + secret-rotation runbook. | TZ §17; config §2.9 |
| 4.5 | Close-definition / run-time coupling locked | `config.close_definition` is **[owner-ratified]** and `config.daily_run_time` is set after the implied final close, with the §3.1 config-load validation enforcing the no-lookahead coupling (a leaky combination is a startup-blocking error). | config §2.9, §3.1; TZ §17 [LAW: no-lookahead] |
| 4.6 | `kill` / `pause` / `resume` semantics verified | Verified in sandbox/paper: `kill` stops the bot + cancels active orders **only — never sells**; `pause` blocks entries but keeps monitoring + exits; `resume` requires extra confirmation + preflight. | frozen-decisions.md, "Order & risk rules" (kill/pause row); TZ §7; db-schema §2 |

---

## 6. Verdict aggregation (how M6b reads this contract)

```text
gate_1_pass = (1.1 ∧ 1.2 ∧ 1.3 ∧ 1.4 ∧ 1.5 ∧ 1.6)        # backtest, binding tariff, PASS-not-WEAK
gate_2_pass = (2.1 ∧ 2.2 ∧ 2.3 ∧ 2.4 ∧ 2.5 ∧ 2.6 ∧ 2.7 ∧ 2.8)   # ≥30d clean paper/sandbox
gate_3_pass = (3.1 ∧ 3.2 ∧ 3.3 ∧ 3.4 ∧ 3.5 ∧ 3.6 ∧ 3.7)  # account guard + token scope + secrets
gate_4_pass = (4.1 ∧ 4.2 ∧ 4.3 ∧ 4.4 ∧ 4.5 ∧ 4.6)        # operational readiness

technical_ready = gate_1_pass ∧ gate_2_pass ∧ gate_3_pass ∧ gate_4_pass

go_live = technical_ready ∧ owner_manual_approval   # §7 — final, human-only, never inferred [LAW]
```

- `go_live` is **AND** across every box — **one FAIL blocks the launch** (no weighting, no override; a defer
  never silences a blocker — frozen-decisions.md, "Scope & product shape" (phased-path row)).
- `technical_ready` being true is **necessary but not sufficient**: the FINAL gate (§7) is a separate,
  human-only step that the technical gates can never satisfy [LAW].
- Going live sets `config.mode = confirm` (never `auto_small`); it grants no new capability beyond the frozen
  confirm-mode limits (dedicated account, ≤1 proposal/day, ≤1 open position, all §7-TZ risk limits).

---

## 7. FINAL gate — explicit manual owner approval (TZ §14.3, §15) [LAW]

```text
owner_manual_approval ∈ {true, false}     # default false; set true ONLY by the owner, in person
```

| # | Checkbox | Measurable PASS condition | Cite |
| --- | --- | --- | --- |
| 7.1 | Owner reviewed all gates | The owner has reviewed Gates 1–4 and their evidence and accepts them. | TZ §14, §15; frozen-decisions.md, "Scope & product shape" (phased-path row) |
| 7.2 | **Explicit manual owner approval to go live `confirm`** | The owner gives an **explicit, recorded** approval to launch live `confirm` on the dedicated 10 000 ₽ account. This is the **single final frozen gate** — it is **human-only**, cannot be pre-satisfied, delegated, or inferred from the technical gates, and **LLM/Codex/Claude never set it** [LAW]. | TZ §15; frozen-decisions.md, "Scope & product shape" (LLM-never-trades + phased-path rows) + "Order & risk rules" (confirm-first row) |

> This gate is the operational form of two frozen laws together: **"manual owner approval is the final gate"**
> and **"LLM never decides buy/sell / go-live"** (frozen-decisions.md, "Scope & product shape" (LLM-never-trades row)). Until §7.2 is set true by the owner, `go_live`
> is `false` no matter how green Gates 1–4 are.

---

## 8. Frozen invariants honored

- **Phased path mandatory, no skipping** — this contract guards exactly the `paper/sandbox → live confirm`
  edge of the frozen path; the M3 STOP gate upstream (1.6) must be PASS or this contract is unreachable
  (frozen-decisions.md, "Scope & product shape" (phased-path row)).
- **First live mode = `confirm`** — go-live sets `config.mode = confirm`; `auto_small` stays DISABLED and its
  token absent (frozen-decisions.md, "Order & risk rules" (confirm-first row); config §1, §2.1).
- **Manual owner approval is the FINAL gate** — §7.2 is human-only, never inferred from technical PASS, never
  set by an LLM (frozen-decisions.md, "Scope & product shape" (LLM-never-trades + phased-path rows)).
- **Dedicated account + hard `account_id` guard** — Gate 3 verifies the guard, startup display, per-mode token
  scope-check (block, not warn), and row-level account scoping at submit + reconciliation (frozen-decisions.md, "Account & access" (dedicated-account + account-guard + token-policy rows)).
- **All portfolio limits** — Gate 2.4 requires evidenced blocking of bad trades under every frozen limit
  (≤1 proposal/day, ≤1 open position, 30%/3 000 ₽ position cap, 50% cash, 100 ₽ daily hard stop) (frozen-decisions.md, "Order & risk rules" (portfolio-limits row)).
- **Sandbox ≠ proof** — no economic gate may be satisfied by sandbox profitability; fill-model parity is
  *documented*, not assumed (frozen-decisions.md, "Known drift / owner decisions pending" (sandbox-not-live row); §2 note, 2.7).
- **No-lookahead** — Gate 1.4 and 4.5 carry the close-definition / run-time coupling and conservative
  both-side fills (frozen-decisions.md, "Strategy, data & backtest honesty" (no-lookahead + conservative-fills rows)).
- **`kill` never sells** — verified in Gate 4.6 (frozen-decisions.md, "Order & risk rules" (kill/pause row)).
- **Token policy** — per-mode tokens, startup scope check blocks trading, no secret leaks (Gate 3.4, 3.6;
  frozen-decisions.md, "Account & access" (token-policy row)).

---

## 9. Open questions / owner-pending

- **[owner-pending] Binding tariff (Gate 1.1):** `config.tariff` ∈ {`investor`, `trader`} — finalized only by
  the **M3** cost-sensitivity result; not asserted here (asserting a value would silently change a frozen
  open item). At 10k the 390 ₽/mo Трейдер fee is a heavy drag, but the binding choice is M3 evidence.
- **[verify] Exact ≥30-day window dates (Gate 2.1):** `window_start_date` / `window_end_date` are recorded by
  the owner from the actual run; only the **≥30-day** duration is fixed here.
- **[owner-pending] `telegram_user_whitelist` ids (Gate 3.7):** the real owner user-id(s) are supplied at M5;
  the gate FAILs while the list is empty.
- **[verify] Account-scoped token feasibility (Gate 3.5):** whether the bot-account product type supports
  account-scoped tokens (empirical at M4); else the documented fallback is the `account_id` guard.
- **[owner-ratify] `close_definition` (Gate 4.5):** `auction_close` (recommended) vs `d1_candle_after_evening`
  — the no-lookahead LAW surface; the owner ratifies, and `daily_run_time` is bound to it (config §2.9, §3.1).
- **[verify] `daily_run_time` (Gate 4.5):** set after the final MOEX D1 close implied by `close_definition`
  (auction close ≥ 18:50, ≥ 19:00 on/after `moex_auction_shift_date`).
- **[owner-pending] Secret-storage backend (Gate 4.4):** Windows-local vs VPS secret store, OS keyring vs
  `.env` — decided before live (M6); the contract fixes only "env/secret store, never committed".

## 10. Cross-references

- Spec: `docs/TZ.md` §13 (backtest gate criteria), §14 (walk-forward, STOP gate, pre-live gates), §15 (live
  confirm preconditions), §16 (security), §17 (deployment). Frozen LAW: `docs/frozen-decisions.md` (phased
  path, confirm-first, account guard, portfolio limits, sandbox ≠ proof, kill semantics, token policy).
- Contracts: [db-schema.md](db-schema.md) (enum/table parity), [config-and-secrets.md](config-and-secrets.md)
  (`mode`, `tariff`, `account_id`, tokens, `close_definition`/`daily_run_time`, secret-scan),
  [tax-and-dividends.md](tax-and-dividends.md) (two-layer PnL / Layer-B verdict).
- ROADMAP: `docs/ROADMAP.md` §M3 (STOP gate), §M6 (M6a parallel ops / M6b pre-live gate).
- Skills: `backtest-honesty`, `risk-policy-guardian`, `secrets-token-policy`, `state-machine-discipline`.
  Auditors: `risk-invariant-auditor`, `lookahead-auditor`.
