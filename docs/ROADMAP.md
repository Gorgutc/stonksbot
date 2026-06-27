# stonksbot — Roadmap / Milestone tracker

> **Source of truth for progress.** Tracks the milestones from `docs/TZ.md`: what's done, in
> progress, and remaining. The Second Brain mirror (`1-Projects/stonksbot/Roadmap.md`, Russian) is
> the human-facing view — keep the two in sync (update both in the same change).
>
> Status legend: `[ ]` todo · `[~]` in progress · `[x]` done. Each milestone names the
> `.agent-kit.json` profile it activates (flip `dormant → active` when the milestone starts).

## Current state (2026-06-27)
- **Phase:** preparation complete → ready to start **M0**.
- **Done:** agent harness (check-kit 36/36), Second Brain folder, frozen invariants, comprehensive
  TZ (`docs/TZ.md`) grounded against verified 2026 T-Invest facts. Branch `claude/agent-harness-bootstrap` pushed.
- **No bot code yet** — all component profiles `dormant`. M0 is the first to write code.

## Milestones

### M0 — Foundations  `[ ]`  (activates: research-backtest)
- [ ] pyproject.toml, ruff, pytest, CI; `src/stonksbot/` skeleton per TZ §4
- [ ] pydantic config + `.env.example` + secrets loading + **account_id guard**
- [ ] SQLite store + schema (TZ §5) + structured logging (audit journal base)
- [ ] set `.agent-kit.json` `verify.fast = "ruff check . && pytest -q"`, `verify.deep = "pytest"`
- **Exit:** `node tools/check-kit.mjs` green + `pytest` green on the skeleton.

### M1 — Data layer  `[ ]`  (research-backtest)
- [ ] T-Invest read-only client + MOEX ISS fallback/cross-check
- [ ] `candles` + `instrument_reference` (instrument_uid keyed) + snapshot versioning + stale/`data_conflict`
- [ ] split adjustment + dividend calendar; 3-year D1 load for approved + index
- **Exit:** reproducible, versioned dataset; data-conflict path tested.

### M2 — Strategy + honest backtest  `[ ]`  (research-backtest)
- [ ] strategy contract (TZ §6); ranking; eligibility filters
- [ ] backtest engine: conservative fills **both sides**, costs **both tariffs incl. 390 ₽/mo**, slippage
- [ ] taxes (НДФЛ) + dividends + dividend-gap entry block; benchmarks (equal-weight, IMOEX, MCFTR, cash)
- [ ] pass/weak/fail gate criteria implemented
- **Exit:** 3-year backtest runs; honesty tests (no-lookahead, fills, costs) green; verdict produced.

### M3 — Walk-forward + validation  `[ ]`  (research-backtest)
- [ ] rolling walk-forward; sensitivity; Deflated Sharpe; cost-sensitivity (tariff decision)
- [ ] robust-params selection; `docs/evidence/walk-forward-latest.md` (evidence gate)
- **Exit:** walk-forward evidence artifact exists; edge survives costs or is honestly rejected.

### M4 — Broker adapter + risk engine + state machine (sandbox)  `[ ]`  (activates: broker-adapter, execution-confirm)
- [ ] T-Invest adapter: per-mode/account-scoped tokens, normalization, pre-order checks, rate-limit/backoff
- [ ] risk engine (all limits, market-regime, re-entry) + order/position **state machine** + **idempotency (order_id)**
- [ ] reconciliation (startup retries) + kill/pause/resume — all in **sandbox**
- **Exit:** full confirm cycle works in sandbox; idempotency + reconciliation tested; risk engine blocks bad trades.

### M5 — Telegram + dashboard + journal/reporting  `[ ]`  (execution-confirm)
- [ ] Telegram: proposals + confirm + **Close button** + commands + **user-id whitelist** + replay protection
- [ ] FastAPI dashboard (127.0.0.1, auth, read-only) per TZ §11
- [ ] two-layer PnL (pre/post tax) + daily status + weekly report + alerts
- [ ] **paper mode** parallel to the live market (lab diagnostics: all candidate signals logged)
- **Exit:** dashboard + Telegram + journal consistent; paper mode producing honest signal stats.

### M6 — Paper/sandbox ≥30d → live confirm gate  `[ ]`  (execution-confirm)
- [ ] ≥30 days paper/sandbox polish; pre-live gates (TZ §14); secret-scan gate; VPS deploy (TZ §17)
- [ ] **owner manual approval** → live confirm on the dedicated 10k account
- **Exit:** all pre-live gates pass + owner approves; live confirm running under all frozen limits.

## Out of scope (until after a successful confirm period)
`auto_small` / full-auto; futures/options/bonds/FX/foreign; multi-broker (Sber phase 2); microstructure/
non-display data; partial sells; multi-position portfolios beyond frozen limits.
