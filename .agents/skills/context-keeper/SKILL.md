---
name: context-keeper
description: Use to capture and carry the few load-bearing facts about a project across a long task or multiple steps — canonical files, frozen decisions, the verify command, do-not-touch zones — so later steps don't re-derive or contradict them.
---

# Context keeper

Maintain a tight, read-only ledger of the facts that actually constrain the work:

- Canonical files and entry points (from `AGENTS.md` → `PROJECT SPECIFICS`).
- Frozen decisions and do-not-touch zones (see the `frozen-decisions` skill).
- The exact verify command(s) from `.agent-kit.json`.
- Anything the user explicitly fixed earlier in the session.

Rules:

- Prefer current code and tests over older prose when they disagree.
- Re-read the source before acting on a remembered fact — files change.
- Surface contradictions instead of silently resolving them.

Keep it to the handful of facts that constrain decisions; do not summarize the
whole repo.
