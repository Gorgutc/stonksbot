# stonksbot — Roadmap / Milestone tracker

> **Source of truth for progress.** Milestones from `docs/TZ.md`. The Second Brain mirror
> (`1-Projects/stonksbot/Roadmap.md`, Russian) is the human view — keep both in sync (same change).
> Legend: `[ ]` todo · `[~]` in progress · `[x]` done. Each milestone names the `.agent-kit.json`
> profile it activates (`dormant → active` when the milestone starts).

## Current state (2026-06-29)
- **Phase:** preparation complete → ready to start **M0**.
- **Done:** agent harness (`check-kit` currently reports 43 checks / 0 failed), Second Brain folder, frozen invariants, comprehensive TZ
  (`docs/TZ.md` rev.2 — adversarially reviewed, grounded against verified 2026 T-Invest facts).
  Merged to `main` (latest `44022e1`).
- **No bot code yet** — all profiles `dormant`. M0 is the first to write code.
- **Pre-M0 contract layer RESOLVED (2026-06-27 #3):** TZ §4.1/§5.1/§12.1 → `docs/contracts/`
  (config-and-secrets, db-schema, tax-and-dividends); `[verify]` gaps closed by research (index = MOEX ISS,
  SDK = `t-tech-investments` via GitLab, `GetDividends`, auction close, НДФЛ 13/15%); **secret-scan gate added**.
  Contracts passed an adversarial audit (4 majors fixed). **M0 code NOT started** — awaiting owner go-ahead to
  activate `research-backtest`. See ADR-0005 + Second Brain `Improvements.md` §"Решения владельца".
- **M1-M6 design-contract layer DRAFTED (2026-06-27 #4):** 18 contracts in `docs/contracts/` + 2 ops docs in `docs/ops/`
  (data-layer, strategy, backtest, state-machine, reconciliation, broker-adapter, universe-eligibility,
  session-policy, journal-reporting, dashboard-telegram-security, pre-live-gates, testing-strategy,
  account-guard-split, tax-open-questions, tax-fixture, local-deployment, vps-deployment) — design only, no code;
  passed a 20-agent adversarial audit + `/code-review`. Owner-pending / no-lookahead surfaces kept as placeholders.
  M0 code still NOT started — awaiting owner decisions (branch `claude/condescending-ptolemy-57f152`).

## Milestones

### M0 — Foundations  `[ ]`  (activates: research-backtest)
- [ ] pyproject, ruff, pytest, CI; `src/stonksbot/` skeleton (TZ §4)
- [ ] **config/.env contract (TZ §4.1)** — pydantic settings, secret vs config keys, `.env.example` placeholders, **account_id guard** · *(design contract ✅ `docs/contracts/config-and-secrets.md`; code pending)*
- [ ] **SQLite schema with DDL contract (TZ §5.1)** — Quotation units/nano (no float), epoch-ms UTC, PK/FK, CHECK enums (incl. `index` kind) + structured logging (audit-journal base) · *(design contract ✅ `docs/contracts/db-schema.md`; code pending)*
- [ ] set `.agent-kit.json` `verify.fast = "ruff check . && pytest -q"`, `verify.deep = "pytest"`
- **Exit:** `check-kit` green + `pytest` green; profile checklist "data schema recorded" checked.

### M1 — Data layer  `[ ]`  (research-backtest)
- [ ] T-Invest read-only + MOEX ISS fallback/cross-check; `candles` + `instrument_reference` (uid-keyed) **+ index series** (IMOEX/MCFTR — source [verify before M1])
- [ ] **universe registry + status transitions** (managed-registry invariant); eligibility filters
- [ ] snapshot versioning; **`data_conflict` DETECTION/flagging tested** (historical/synthetic; live skip-entry asserted in M4)
- [ ] split adjustment + ticker-history (TCSG→T) + dividend calendar (source [verify])
- **Exit:** reproducible versioned 3y dataset (+ warm-up) incl. index; detection path tested.

### M2 — Strategy + honest backtest  `[ ]`  (research-backtest)
- [ ] strategy contract (TZ §6, MAs pinned to MA20/MA50 live); ranking
- [ ] backtest: conservative fills **both sides** (mirrors §8 entry rule), costs **both tariffs incl. 390 ₽/mo**, slippage
- [ ] taxes (НДФЛ FIFO, **hand-computed tax fixture**) + dividends (net) + dividend-gap block; benchmarks (equal-weight, IMOEX, MCFTR gross, cash)
- [ ] pass/weak/fail gate **reported per tariff (provisional)**
- [ ] **start the early signal-only lab journal** (begins the ≥30-day clock without execution)
- **Exit:** 3y backtest runs; honesty + tax-fixture tests green; per-tariff verdict produced.

### M3 — Walk-forward + validation + STOP gate  `[ ]`  (research-backtest)
- [ ] rolling walk-forward; sensitivity; Deflated Sharpe (record trial count); cost-sensitivity → **binding tariff**
- [ ] robust-params; `docs/evidence/walk-forward-latest.md` (evidence gate)
- [ ] **🛑 STOP gate:** if edge dies after costs (FAIL) → do NOT proceed to M4; iterate within frozen constraints or shelve
- **Exit:** walk-forward evidence exists; **explicit PASS/FAIL decision recorded** before any M4 work.

### M4 — Broker adapter + risk engine + state machine (sandbox)  `[ ]`  (activates: broker-adapter, execution-confirm)
- [ ] T-Invest adapter: per-mode/account-scoped tokens (startup scope **block**), normalization (limit-only/no-margin/no-short at boundary), pre-order checks (exclude DEALER/auction), rate-limit/backoff
- [ ] risk engine (all limits, market-regime, re-entry) + state machine + **idempotency (order_id)** + tick-rounding
- [ ] reconciliation (retries, `blocked_reconciliation_mismatch` semantics) + kill/pause/resume — all **sandbox**
- [ ] **live `data_conflict` → skip-entry / allow-exit assertion**; **fill-model parity check** (backtest vs sandbox/paper)
- **Exit:** full confirm cycle in sandbox; idempotency + reconciliation + fill-parity tested; risk engine blocks bad trades.

### M5 — Telegram + dashboard + journal/reporting  `[ ]`  (execution-confirm)
- [ ] Telegram: proposals + confirm + **Close button** + manual-position 3-button prompt + commands + **user-id whitelist** + replay/TTL (wall-clock)
- [ ] FastAPI dashboard (127.0.0.1, auth) — **MVP cut-line: positions + signals + two-layer PnL + status only** (Telegram confirm flow lands first)
- [ ] two-layer PnL (pre/post tax) + daily status + weekly report + **monthly whitelist-review job** + alerts
- [ ] **full execution-grade paper mirror** (lab diagnostics: all candidate signals logged)
- **Exit:** dashboard + Telegram + journal consistent; paper mode producing honest signal stats.

### M6 — Paper/sandbox ≥30d → live confirm gate  `[ ]`  (execution-confirm)
- [ ] **M6a (parallel):** VPS provisioning + Postgres migration + secret store + secret-scan gate + DR runbook (runs during the local 30-day window)
- [ ] **M6b:** ≥30-day window passes pre-live gates (TZ §14) + account guard + token policy; **owner manual approval** → live confirm on the dedicated 10k account
- **Exit:** all pre-live gates pass + owner approves; live confirm running under all frozen limits.

## Out of scope (until after a successful confirm period)
`auto_small` / full-auto; futures/options/bonds/FX/foreign; multi-broker (Sber phase 2); microstructure/
non-display data; partial sells; multi-position portfolios beyond frozen limits.
