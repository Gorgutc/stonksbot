# stonksbot — Roadmap / Milestone tracker

> **Source of truth for progress.** Milestones from `docs/TZ.md`. The Second Brain mirror
> (`1-Projects/stonksbot/Roadmap.md`, Russian) is the human view — keep both in sync (same change).
> Legend: `[ ]` todo · `[~]` in progress · `[x]` done. Each milestone names the `.agent-kit.json`
> profile it activates (`dormant → active` when the milestone starts).

## Current state (2026-07-01)
- **Phase:** **M0 complete; M1 in progress** — CI shipped in PR #7 (merged `main@14dadb4`);
  PR #9 landed M1.1 schema/ISS data work; PR #10 fixed ISS pagination/cursor
  fail-closed behavior and `signals.reason` checks; PR #11 added the versioned
  data-store, latest-as-of read path, and `data_conflict` gating; PR #12 enforced the
  frozen pilot risk band at `RiskSettings` construction (ADR-0008); PR #13 made
  `data_conflict` re-detection idempotent (partial UNIQUE on open rows) at SQLite
  `SCHEMA_VERSION = 4`; PR #14 synced the status surfaces + removed a dead
  `install-hooks.mjs` binding; PR #15 added the MOEX trading-calendar loader,
  so current main is `335485c`.
  Verify + harness gates run on PR/main.
- **Done:** agent harness (`check-kit` currently reports 53 checks / 0 failed), Second Brain folder, frozen invariants, comprehensive TZ
  (`docs/TZ.md` rev.2 — adversarially reviewed, grounded against verified 2026 T-Invest facts).
  Merged to `main` (latest verified `335485c` after PR #15; PR #7 closed M0 with CI,
  PR #6 shipped the M0 skeleton, PR #5 the pre-M0 readiness layer).
- **Initial M0 code shipped** — `research-backtest` is active; `broker-adapter` and
  `execution-confirm` remain dormant. The shipped M0 scope is config, schema, account-guard stub,
  and ruff/pytest verification only.
- **M1.1 landed (2026-06-30, PR #9):** schema hardening rejects negative/impossible quote pairs;
  the first data-leg slice is MOEX ISS read-only candles with injected reader and no order path.
- **M1 blocker fix landed (2026-06-30, PR #10):** MOEX ISS candle fetch paginates
  to cursor `TOTAL`, fails closed on missing cursor/short pages, rejects duplicate
  candle timestamps, and enforces decision-aware frozen `signals.reason` checks.
- **M1 data persistence landed (2026-06-30, PR #11):** versioned data-store helpers add
  insert-only candle/dividend snapshots, latest-as-of read gates, entry-safe
  stale/conflict filters, persistent `data_conflict` skip signals, and versioned
  schema guards for the selected/confirmed proposal-to-order flow.
- **M1 risk-config hardening landed (2026-06-30, PR #12):** the frozen pilot band is now
  enforced at `RiskSettings` model construction in both directions (a relaxed cap or a
  nonsensical value fails closed), closing the `load_settings(validate_startup=False)`
  bypass; see `docs/frozen-decisions.md` + ADR-0008.
- **M1 `data_conflict` idempotency landed (2026-07-01, PR #13):** `data_conflict`
  re-detection is now idempotent — a partial UNIQUE on open `data_conflicts` rows plus an
  `ON CONFLICT ... WHERE resolved = 0` upsert keep the earliest `as_of` and emit at most
  one skip signal per bar; SQLite `SCHEMA_VERSION` bumped 3 → 4 (pre-v4 DBs fail closed).
- **Tier-1 cleanup landed (2026-07-01, PR #14):** repo status surfaces (README, AGENTS,
  ROADMAP, `.agent-kit.json`) synced to `6576f28`/schema v4/PR #12+#13; dead `root`
  binding removed from `tools/install-hooks.mjs` (fail-closed behavior preserved).
- **M1 trading calendar landed (2026-07-01, PR #15):** `src/stonksbot/data/calendar.py` —
  immutable UTC-day-normalized `TradingCalendar` (is/next/previous/add/range) with
  producers deriving trading days from MOEX ISS IMOEX D1 candle dates (printed bar =
  trading day; no-lookahead, fail-closed); wires the previously producerless
  `_next_trading_day` / `store_dividend_snapshot` (+14 tests, 96 total).
- **Pre-M0 contract layer RESOLVED (2026-06-27 #3):** TZ §4.1/§5.1/§12.1 → `docs/contracts/`
  (config-and-secrets, db-schema, tax-and-dividends); `[verify]` gaps closed by research (index = MOEX ISS,
  SDK = `t-tech-investments` via GitLab, `GetDividends`, auction close, НДФЛ 13/15%); **secret-scan gate added**.
  Contracts passed an adversarial audit (4 majors fixed). **M0 code NOT started**; the 2026-06-29 owner bundle
  below now authorizes `research-backtest`. See ADR-0005 + Second Brain `Improvements.md`.
- **M1-M6 design-contract layer DRAFTED (2026-06-27 #4):** 18 contracts in `docs/contracts/` + 2 ops docs in `docs/ops/`
  (data-layer, strategy, backtest, state-machine, reconciliation, broker-adapter, universe-eligibility,
  session-policy, journal-reporting, dashboard-telegram-security, pre-live-gates, testing-strategy,
  account-guard-split, tax-open-questions, tax-fixture, local-deployment, vps-deployment) — design only, no code;
  passed a 20-agent adversarial audit + `/code-review`. Owner-pending / no-lookahead surfaces were kept as
  placeholders until the 2026-06-29 owner bundle below.
- **Owner decisions for M0 start (2026-06-29):** M0 authorized (now shipped in PR #6); `research-backtest` active;
  `close_definition=auction_close`; `daily_run_time=19:05 Europe/Moscow`;
  `universe.approved=[SBER,T,GAZP,ROSN,TATN,X5]`; `universe.watch_only=[IRAO,LKOH]`;
  account/token feasibility is **not** an M0 blocker (fail-closed stubs now, live scope check at M4);
  M0 secrets are env/`.env` placeholders only, with the Windows/VPS secret-storage backend decided before live (M6).

## Milestones

### M0 — Foundations  `[x]`  (activates: research-backtest)
- [x] pyproject, ruff, pytest; `src/stonksbot/` skeleton (TZ §4)
- [x] CI wiring — `.github/workflows/ci.yml` (ruff + pytest + check-kit + test-gates + secret-scan + evidence-gate, on PR/main)
- [x] **config/.env contract (TZ §4.1)** — pydantic settings, secret vs config keys, `.env.example` placeholders, **account_id guard** · *(design contract ✅ `docs/contracts/config-and-secrets.md`)*
- [x] **SQLite schema with DDL contract (TZ §5.1)** — Quotation units/nano (no float), epoch-ms UTC, PK/FK, CHECK enums (incl. `index` kind) + structured logging (audit-journal base) · *(design contract ✅ `docs/contracts/db-schema.md`)*
- [x] set `.agent-kit.json` `verify.fast = "ruff check . && pytest -q"`, `verify.deep = "pytest"`,
  `verify.ship = "pytest --maxfail=1 -q"`
- **Exit:** `check-kit` green + `verify.fast`/`verify.deep`/`verify.ship` green; profile checklist "data schema recorded" checked.

### M1 — Data layer  `[~]`  (research-backtest)
- [~] T-Invest read-only + MOEX ISS fallback/cross-check; `candles` + `instrument_reference` (uid-keyed) **+ index series** (IMOEX/MCFTR — MOEX ISS, ADR-0005). Current slice: MOEX ISS read-only candles with pagination/fail-closed checks + the MOEX trading calendar (PR #15, derived from IMOEX D1 candle dates); no broker/execution SDK, full-access/live token, Telegram, or order path.
- [ ] **universe registry + status transitions** (managed-registry invariant); eligibility filters
- [~] snapshot versioning/read path; **`data_conflict` DETECTION/flagging tested** (PR #11 landed insert-only snapshots, latest-as-of reads, stale/conflict entry skips, dividend `as_of` gating, persistent conflict skip signals; PR #13 made re-detection idempotent — partial UNIQUE on open rows + earliest-`as_of` upsert + one skip signal per bar, `SCHEMA_VERSION` 3 → 4; historical/synthetic divergence fixtures and live skip-entry asserted in M4 remain)
- [ ] split adjustment + ticker-history (TCSG→T) + dividend calendar (T-Invest GetDividends, ADR-0005)
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
