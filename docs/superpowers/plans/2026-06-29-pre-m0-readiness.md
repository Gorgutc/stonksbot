# Pre-M0 Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the repo and Second Brain clean enough that the next session can start M0 without rediscovering stale status or unsafe backtest assumptions.

**Architecture:** This is a docs-and-harness readiness change only. It ratifies the approved owner-decision bundle, tightens the contracts that M0 will implement, and updates orientation surfaces; it does not add Python code or activate broker/execution profiles.

**Tech Stack:** Markdown contracts, Node.js gate scripts, `.agent-kit.json`, Second Brain Markdown/TOML notes.

---

### Task 1: Ratify M0 Owner Decisions

**Files:**
- Modify: `.agent-kit.json`
- Modify: `AGENTS.md`
- Modify: `docs/ROADMAP.md`
- Modify: `docs/profiles/research-backtest.md`
- Modify: `docs/contracts/config-and-secrets.md`
- Modify: `docs/contracts/session-policy.md`
- Modify: `docs/TZ.md`

- [x] Record that M0 may start and that only `research-backtest` becomes active.
- [x] Keep `broker-adapter` and `execution-confirm` dormant.
- [x] Ratify `close_definition=auction_close` and `daily_run_time=19:05 Europe/Moscow`.
- [x] Confirm `approved=[SBER,T,GAZP,ROSN,TATN,X5]` and `watch_only=[IRAO,LKOH]`.
- [x] Record M0 `account_id` change detection as a DB guard-state row.
- [x] Keep account-scoped-token feasibility and secret-storage backend as M4/M6 defers.

### Task 2: Tighten Backtest And Session Contracts

**Files:**
- Modify: `docs/contracts/backtest.md`
- Modify: `docs/contracts/session-policy.md`
- Modify: `docs/contracts/state-machine.md`

- [x] Fix entry premium math so `max_entry_premium_pct=0.20` is actually represented.
- [x] Make TTL fill parity explicit: intraday first-TTL-window evidence is required; with D1-only data, fill only if the next open is already at or below the limit.
- [x] Add conservative same-bar ordering: no same-bar TP credit on the entry bar; if stop and target are both possible, stop/worse price wins.
- [x] Move `NORMAL_TRADING` checks out of the post-close signal-selection path and into next-session confirm/preflight/submit.
- [x] Make confirm preflight failure reject the proposal without mutating the already-selected signal to `skipped`.

### Task 3: Harden Harness Readiness

**Files:**
- Modify: `tools/install-hooks.mjs`
- Modify: `tools/check-kit.mjs`
- Modify: `.codex/hooks.json`

- [x] Make unmanaged hook conflicts fail closed in `tools/install-hooks.mjs`.
- [x] Add `.agents/skills` to `check-kit` parity validation against `.claude/skills`.
- [x] Update stale hook wording from `verifyCommand` to the actual `verify.fast` / `verifyCommand` order.

### Task 4: Clean Orientation Surfaces

**Files:**
- Modify: `docs/ROADMAP.md`
- Modify outside repo: `C:\Users\Junior\Desktop\Second_brain\1-Projects\stonksbot\_INDEX.md`
- Modify outside repo: `C:\Users\Junior\Desktop\Second_brain\1-Projects\stonksbot\Roadmap.md`
- Modify outside repo: `C:\Users\Junior\Desktop\Second_brain\1-Projects\stonksbot\Tests.md`
- Modify outside repo: `C:\Users\Junior\Desktop\Second_brain\1-Projects\stonksbot\Conventions.md`
- Modify outside repo: `C:\Users\Junior\Desktop\Second_brain\1-Projects\stonksbot\stonksbot-weekday-project-monitor\automation.toml`

- [x] Update current state to `main@560aa2b`, PR #3/#4 merged, `check-kit` 53/0.
- [x] Update counts to 18 contracts plus 2 ops docs.
- [x] Update local paths from `C:\Users\Maxim\...` to `C:\Users\Junior\...`.
- [x] Point latest-session navigation to `Sessions/2026-06-29-pre-m0-readiness-handoff.md`.
- [x] Keep historical dated session notes unchanged.

### Task 5: Verify And Handoff

**Files:**
- Modify outside repo: `C:\Users\Junior\Desktop\Second_brain\1-Projects\stonksbot\Sessions\2026-06-29-pre-m0-readiness-handoff.md`
- Modify outside repo: `C:\Users\Junior\.codex\memories\extensions\ad_hoc\notes\2026-06-29-pre-m0-readiness.md`

- [x] Run `node tools/check-kit.mjs`.
- [x] Run `node tools/evidence-gate.mjs`.
- [x] Run `node tools/test-gates.mjs`.
- [x] Run `node tools/secret-scan.mjs --all`.
- [x] Run `git diff --check`.
- [x] Record the final state, command evidence, remaining M0 instructions, and any blocked checks.
