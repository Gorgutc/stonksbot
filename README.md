# stonksbot

A cautious **trading laboratory** (not an "autonomous trader") for the
**T-Invest (T-Bank) API** on **MOEX Russian stocks**. It researches a formal
rule-based strategy, backtests it honestly, paper/sandbox-tests it, then trades a
tiny **dedicated** account in **confirm mode** — the bot proposes an entry, the human
confirms in Telegram, and protective exits are automated. Codex/Claude build, review,
and document the system; they never decide buy/sell.

> **Status: M0 complete.** M0 closed in PR #7 (`main@14dadb4`); current main after
> PR #9 M1 schema/ISS data merge is `549eb68`. The active `research-backtest` profile has
> the Python skeleton — config loader, SQLite DDL/bootstrap, and a fail-closed
> account-guard stub with pytest coverage — plus CI (`.github/workflows/ci.yml`,
> verify + harness gates on PR/main). M1.1 has landed with schema hardening and a
> read-only MOEX ISS data leg; broader M1 data work remains in progress. Broker/execution profiles remain dormant.

## Read first
- `AGENTS.md` — single source of truth for every agent harness (see PROJECT SPECIFICS).
- `docs/frozen-decisions.md` — the locked risk/safety invariants (LAW).
- `GETTING-STARTED.md` — how to activate the harness in a session.
- Second Brain (cross-session memory): `1-Projects/stonksbot/_INDEX.md` →
  `Conventions.md` 🔒 → the latest `Sessions/` note. The repo's machine-local
  `CLAUDE.local.md` points there.

## Harness
- Multi-harness: `AGENTS.md` (canonical) + `CLAUDE.md` (+ Claude skills/agents) +
  `GEMINI.md` (stub). Shared hooks live in `.codex/hooks/`.
- Domain skills: `risk-policy-guardian`, `backtest-honesty`, `broker-api-contract`,
  `secrets-token-policy`, `state-machine-discipline` (plus the kit's generic skills).
- Domain subagents: `risk-invariant-auditor`, `lookahead-auditor` (plus the kit's 7).
- Profile status lives in `.agent-kit.json`: `research-backtest` is active for M0;
  `broker-adapter` and `execution-confirm` remain dormant until explicitly
  activated (`component-guardian` enforces).
- Project verify: `ruff check . && pytest -q`.
- Harness self-check: `node tools/check-kit.mjs`.
- Gate regression tests: `node tools/test-gates.mjs`.

## Safety (non-negotiable — see `docs/frozen-decisions.md`)
Dedicated account + `account_id` guard · confirm-mode first · limit orders only ·
no margin / shorts / market orders · strict portfolio limits · automated risk exits ·
`kill` never sells positions · no intraday lookahead · honest backtest with full costs ·
secrets never in code / logs / Telegram.

**This is not financial advice and guarantees no profit.** Backtest results do not
guarantee future returns; API failures, gaps, partial fills, commissions, and taxes
are real.
