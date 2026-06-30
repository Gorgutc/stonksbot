# Claude Code Entry Point

This project uses a multi-harness setup: OpenAI Codex, Claude Code, and Gemini
share one canonical rule set. The full rules are imported below from `AGENTS.md`
(the single source of truth for all harnesses).

@AGENTS.md

## Claude Code specifics

- **Skills** in `.claude/skills/**` are **all** auto-loaded. The canonical list
  lives in `AGENTS.md` → Skills — the generic kit skills plus the stonksbot domain
  skills (`risk-policy-guardian`, `backtest-honesty`, `broker-api-contract`,
  `secrets-token-policy`, `state-machine-discipline`). Don't re-enumerate them here;
  a hard-coded list would drift from the directory.
- **Subagents** in `.claude/agents/**` mirror the Codex contracts in
  `.codex/agents/*.toml`. Spawn them with the Agent tool for read-only audits,
  reviews, exploration, research, harness-drift audits, and final verification.
- **Hooks** in `.claude/settings.json` reuse the shared Node scripts in
  `.codex/hooks/` — the same scripts Codex registers in `.codex/hooks.json`. Both
  harnesses get the same session context, prompt nudge, and post-edit
  verification. The post-edit hook supports optional path scoping via
  `.agent-kit.json` `verifyPaths`; this checkout leaves `verifyPaths` empty, so
  it verifies broadly after edits and blocks (exit 2) on failure. Separately, the git
  **pre-commit/pre-push gates** (`tools/install-hooks.mjs`) are installed and live
  in this checkout — linked worktrees should verify hooks or use a shared hooksPath.
- This kit keeps `.codex/` and `.claude/` **independent** (hand-authored). Run
  `node tools/check-kit.mjs` to confirm the two harness layers stay consistent
  (every Codex agent has a read-only Claude mirror, the stubs point at AGENTS.md,
  the hooks exist).

## Multi-harness notes

- `GEMINI.md` is a **redirect stub** pointing at `AGENTS.md`. Additional harnesses
  get a stub only. Harness-specific skill files may exist only as exact mirrors
  for tooling compatibility; `tools/check-kit.mjs` enforces parity so they are
  not independent rule sets.
- `CLAUDE.md` is richer than a stub because Claude Code has real `.claude/skills`
  and `.claude/agents` directories it auto-loads; Gemini does not, so it reads its
  rules from `AGENTS.md` prose.

## Orchestrating Codex from Claude Code

You can act as the **orchestrator**: decompose a task, then drive
`tools/codex-orchestrator/fanout.mjs` over Bash to run many parallel `codex exec`
agents on the user's Codex/ChatGPT plan, while you (Claude) handle decomposition,
synthesis, and verification. See the Orchestration section of `AGENTS.md` and
`tools/codex-orchestrator/README.md`.

## Optional: cross-review with the Codex plugin

For an independent second review of Claude-authored changes you can install the
Codex Claude Code plugin (one-time, global):

```
/plugin marketplace add openai/codex-plugin-cc
/plugin install codex@openai-codex
/codex:setup
```

Then `/codex:review` per change. This is optional and separate from the fan-out
orchestrator, which needs only the standalone Codex CLI.
