# Agent Rules

This file is the **single source of truth** for every agent harness in this
project: OpenAI **Codex** (reads `AGENTS.md` natively), **Claude Code** (imports
it through `CLAUDE.md` via `@AGENTS.md`), and **Gemini** (`GEMINI.md` redirect
stub). Additional harnesses get a **redirect stub only**. Harness-specific skill
files may exist only as exact mirrors for tooling compatibility; `check-kit`
enforces those mirrors so they are not independent rule sets.

> This file ships from a reusable **agent-kit** starter. Fill in the
> `PROJECT SPECIFICS` section per project; the rest is harness scaffolding that
> works the same everywhere.

## Working rules

- Read before editing. Prefer ripgrep (`rg`) for search.
- Keep edits scoped to the requested task; preserve unrelated user changes.
- Before deleting or overwriting something you did not create, inspect it and
  surface any contradiction instead of proceeding.
- Report outcomes faithfully: if a check fails, say so with the output.
- State intent when behavior changes; verify after shipped-code edits.

## Orchestration — fan out to Codex agents

This project ships a dependency-free **fan-out orchestrator** at
`tools/codex-orchestrator/fanout.mjs`. It runs many `codex exec` turns in
parallel. Use it (or per-harness subagents) for substantial work: broad audits,
dead-code / duplication search, multi-file reviews, codebase-wide research.

How to use it:

1. Decompose the task into independent **units** (one prompt each).
2. Write a `spec.json` (see `tools/codex-orchestrator/README.md`).
3. Check prerequisites once: `node tools/codex-orchestrator/fanout.mjs --doctor`.
4. Run: `node tools/codex-orchestrator/fanout.mjs --spec spec.json`.
5. Read `runs/<runId>/summary.md`, then **synthesize and verify** — the
   orchestration is not done until findings are checked.

### Subagent prompt contract

Every delegated unit/subagent prompt must stand alone and specify these 7 fields:

1. **Documentation** — what to read first (AGENTS.md + the relevant files).
2. **Selected skills** — which skills apply.
3. **Selected agents** — which subagent role this is.
4. **Write zone** — exactly which files it may touch (read-only by default).
5. **Verification** — how its result will be checked.
6. **Stop rules** — when to stop / escalate instead of guessing.
7. **Expected output** — the precise shape of the return (findings list, verdict, …).

### Decomposition / routing matrix

For multi-stream work, decide each stream explicitly:

| Stream | Goal | Agent | Write zone | Mode (parallel / sequential / local) | Reason |
| --- | --- | --- | --- | --- | --- |
| … | … | … | … | … | … |

- **parallel** only when write zones are disjoint (or all read-only).
- **sequential** when a stream depends on another's output.
- **local** when no spawn tool is available — and say so (see degradation rule).

### Result contract

Each unit returns **PASS / FAIL + evidence + explicit defers**. A *defer*
(something intentionally left for later) must never silently override a
**blocker** (something that makes the result unsafe to ship).

### Degradation rule

If no subagent-spawn tool is available in the current environment, **do the work
locally and document the fallback** ("spawn unavailable; executed locally")
rather than treating the absence as a blocker.

### Harness rules

- **Default to `sandbox: "read-only"`.** Read-only units fan out wide safely — no
  working-tree conflicts.
- **Parallel writes need isolation.** Only use `sandbox: "workspace-write"` when
  each unit has its **own git worktree** (per-unit `cwd`); otherwise parallel
  writers clobber each other. Merge the worktrees afterward.
- **Concurrency is capped by your Codex plan's rate limits**, not the script.
  Start at `concurrency` 4–6 and raise empirically.

Single-shot alternative: the Codex CLI's own `codex exec "<prompt>"`, or (if
installed) the `codex@openai-codex` Claude Code plugin for a single review/rescue.

## Skills

Generic, stack-agnostic skills in `.claude/skills/` and `.agents/skills/`
(mirrored for harness compatibility; `tools/check-kit.mjs` enforces parity):

- `session-bootstrap` — start-of-task: read instructions, check git, classify
  narrow vs broad, pick skills/agents.
- `fanout-orchestrator` — when/how to fan work out to parallel Codex agents.
- `context-keeper` — carry the few load-bearing facts across a long task.
- `frozen-decisions` — respect the frozen list; change the doc and its verifier
  together (`docs/frozen-decisions.md`).
- `instruction-drift` — audit the harness itself for staleness/contradictions.

Domain skills (stonksbot — a trading lab; Codex reads their guidance from here):

- `risk-policy-guardian` — before touching sizing / limits / order construction,
  enforce the frozen risk invariants (limit-only, no margin/shorts, account guard,
  confirm-first, kill semantics, portfolio limits).
- `backtest-honesty` — no lookahead, conservative fills, costs both sides,
  walk-forward over global fit, the required validation metrics; sandbox ≠ proof.
- `broker-api-contract` — T-Invest adapter discipline: token scopes, `instrument_uid`
  (not FIGI), pre-order status/`min_price_increment`/lot checks, rate limits,
  idempotency, reconciliation, sandbox/live separation.
- `secrets-token-policy` — tokens never in code/config/logs/dashboard/Telegram;
  env/secret store; separate token per mode; startup scope check; rotation.
- `state-machine-discipline` — model signal/order/position as an explicit state
  machine with audit trail; idempotent transitions; reconcile on startup; adopt
  external/manual changes via reconciliation.

## Subagents

Reusable read-only agent contracts in `.codex/agents/*.toml` with Claude mirrors
in `.claude/agents/*.md`:

- `explorer` — broad read-only code/file search; conclusions, not dumps.
- `code-reviewer` — reviews a diff/branch for correctness bugs.
- `dead-code-auditor` — finds unused/duplicated/unsafe-to-remove code.
- `researcher` — answers a question from the codebase, read-only.
- `instruction-drift-auditor` — audits the harness for drift.
- `verification-reviewer` — runs the verify command, reports PASS/FAIL.
- `component-guardian` — profile-aware guard (see Component profiles).
- `risk-invariant-auditor` — read-only audit that code/config/docs honor the frozen
  risk invariants (limit-only, no margin/shorts, account guard, kill semantics).
- `lookahead-auditor` — read-only audit of strategy/backtest for lookahead bias and
  dishonest cost/fill modeling.

## Component profiles (optional)

For multi-component repos (e.g. backend + addon + desktop), declare each
component as a **profile** in `.agent-kit.json` instead of one flat instruction
set:

```json
"profiles": [
  { "name": "backend",     "status": "active",  "doc": "docs/profiles/backend.md" },
  { "name": "windows-exe", "status": "dormant", "doc": "docs/profiles/windows-exe.md" }
]
```

- **dormant** = the rules exist, but NO toolchain / dependency / build command for
  that component may be introduced until an explicit request flips status to
  `active`. The `component-guardian` subagent enforces this.
- Empty `profiles` (the default) = a single flat project. Templates live in
  `docs/profiles/_TEMPLATE.md`.

## Commands

Set verification commands in `.agent-kit.json` (`verify.fast` runs in the
post-edit hook; `verify.deep` / `verify.ship` are for the orchestrator and
pre-PR/CI; `verifyCommand` is a back-compat alias). Harness tooling:

```bash
node tools/check-kit.mjs                              # harness integrity / parity
node tools/test-gates.mjs                             # regression tests for gate scripts
node tools/codex-orchestrator/fanout.mjs --doctor     # orchestrator prerequisites
node tools/evidence-gate.mjs                          # fail-closed evidence gate (if configured)
node tools/install-hooks.mjs                          # git pre-commit/pre-push gates (installed & live in this checkout; linked worktrees should verify hooks or shared hooksPath)
ruff check . && pytest -q                             # project verify.fast
```

## Done when

- The requested files are changed and unrelated edits are preserved.
- The project's verify command(s) pass after code changes.
- `node tools/check-kit.mjs` passes if harness files changed.
- New instructions stay short and tied to the real code.

---

## PROJECT SPECIFICS

- **Project:** stonksbot — a *cautious trading laboratory* (not an "autonomous
  trader") for the **T-Invest (T-Bank) API** on **MOEX Russian stocks**. It
  researches a formal rule-based strategy, backtests it honestly, paper/sandbox
  tests it, then trades a tiny **dedicated** account in **confirm mode** (bot
  proposes an entry, the human confirms in Telegram; protective exits are
  automated). Codex/Claude BUILD, review, and document — **never** decide buy/sell.
- **Status:** M0 COMPLETE. M0 closed in PR #7 (`main@14dadb4`); current main after
  PR #15 is `335485c`. Owner decision on
  2026-06-29 activated only the `research-backtest` profile after `main@ca0c04e` /
  PR #5 completed readiness; PR #6 landed the Python research/backtest package,
  config loader, SQLite DDL, account-guard stub, and ruff/pytest verification;
  PR #7 wired CI (`.github/workflows/ci.yml`, verify + harness gates on PR/main);
  PR #9 landed M1.1 schema hardening plus the first read-only MOEX ISS data leg;
  PR #10 fixed ISS pagination/cursor fail-closed behavior and `signals.reason` checks;
  PR #11 added the versioned data-store, latest-as-of read path, and `data_conflict`
  gating; PR #12 enforced the frozen pilot risk band at `RiskSettings` construction
  (ADR-0008); PR #13 made `data_conflict` re-detection idempotent (partial UNIQUE on
  open rows) at SQLite `SCHEMA_VERSION = 4`; PR #14 synced the repo status surfaces
  and removed a dead `install-hooks.mjs` binding; PR #15 added the MOEX
  trading-calendar loader (`src/stonksbot/data/calendar.py`, the producer for
  `_next_trading_day`). M0 is done; M1 remains in progress.
  `broker-adapter` and `execution-confirm` remain **dormant** — introduce no broker order placement, Telegram execution,
  live/sandbox trading dependency, full-access/live token handling, or build command for those profiles without an
  explicit activation request (see `component-guardian` + `docs/profiles/`). Active
  M1 may later add read-only T-Invest market-data access only under the secrets/token
  policy and without order capability. The
  Second Brain vault is the cross-session memory: read
  `1-Projects/stonksbot/_INDEX.md` → `Conventions.md` 🔒 → latest session before
  substantial work (the repo's machine-local `CLAUDE.local.md` points there).
- **Stack / runtime (intended, Python-first):** Python 3.12+; FastAPI (internal API +
  dashboard backend); SQLite for MVP → Postgres later; python-telegram-bot (control
  plane only); APScheduler or cron (daily jobs); Docker Compose (local → VPS); pytest
  (+ hypothesis selectively); structlog/standard logging for a machine-readable audit
  trail; official T-Invest Python SDK for the broker adapter.
- **Frozen risk invariants (LAW — `docs/frozen-decisions.md`):** dedicated account +
  hard `account_id` guard; **confirm** is the first live mode (no full-auto);
  **limit orders only** — no market orders, no margin, no shorts, long-only;
  portfolio limits (10 000 ₽ pilot, max 1 position, ≤ 3 000 ₽ / 30% per position,
  50% cash, ≤ 1 proposal/day); automated risk exits; **signal after daily close,
  entry next session, no intraday lookahead**; conservative backtest fills + costs
  both sides; startup reconciliation; `kill` stops the bot + cancels orders but
  **never sells positions**.
- **Verification:** project-code verify covers the Python M0/M1 surface: `.agent-kit.json`
  `verify.fast = "ruff check . && pytest -q"`, `verify.deep = "pytest"`, and
  `verify.ship = "pytest --maxfail=1 -q"`. Harness/gate checks still exist
  (`check-kit`, `test-gates`, `secret-scan`, `evidence-gate`). Any change to a
  strategy/backtest surface must carry walk-forward + cost-sensitivity evidence
  (evidence gate).
- **Do-not-touch:** secrets/tokens (never in code/config/logs/dashboard/Telegram —
  env/secret store only); the frozen risk policy in `docs/frozen-decisions.md`; the
  Second Brain protocol. Record durable decisions in `docs/frozen-decisions.md` AND
  the Second Brain `Decisions/`.
- **What NOT to port:** stonksbot is a Python backend / trading lab. Do not bring in
  rules, skills, or dependencies from sibling agent-kits that target web-frontend,
  desktop-GUI, or 3D/CAD/game tooling. `forbiddenTerms` in `.agent-kit.json` +
  `node tools/check-kit.mjs` enforce this.
- **Branch / publish flow:** work on a `claude/<topic>` or `codex/<topic>` branch;
  never commit to `main` directly; open a PR. Run `node tools/check-kit.mjs` after
  any harness edit.
