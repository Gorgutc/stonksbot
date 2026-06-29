---
name: session-bootstrap
description: Use at the very start of a non-trivial task in a fresh session — read the instructions, check git state, classify the task as narrow vs broad, and pick the right skills/subagents before editing anything.
---

# Session bootstrap

Run this before any substantial work in a new session.

1. Read `AGENTS.md` (the single source of truth) and its `PROJECT SPECIFICS`
   section. Note the verify command(s) in `.agent-kit.json`.
2. Check state: `git status -sb` and the current branch. Preserve unrelated changes.
3. Classify the task:
   - **Narrow** (one file / one focused change) → do it directly, verify, done.
   - **Broad** (audit / multi-file / "across everything" / parallelizable) → use the
     `fanout-orchestrator` skill (decompose → parallel Codex agents) and/or the
     read-only subagents in `.codex/agents` / `.claude/agents`.
4. Pick the skills/subagents and state the plan in one line before editing.
5. Emit a short `bootstrap: ready` summary: project, branch, task class, chosen
   skills/agents, verify command.
