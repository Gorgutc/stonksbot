# Gemini Entry Point

This project uses **AGENTS.md** as the single source of truth for every agent
harness. Gemini CLI: follow `AGENTS.md` and the skills under `.claude/skills/`
exactly as written.

Do not add Gemini-specific policy here. If this file ever disagrees with
`AGENTS.md`, `AGENTS.md` wins. This file is a **redirect stub by design** —
additional harnesses get a pointer, never a duplicated rule set or a per-harness
skill mirror.
