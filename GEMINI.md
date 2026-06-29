# Gemini Entry Point

This project uses **AGENTS.md** as the single source of truth for every agent
harness. Gemini CLI: follow `AGENTS.md`; the skill copies under `.claude/skills/`
and `.agents/skills/` are exact mirrors for tooling compatibility.

Do not add Gemini-specific policy here. If this file ever disagrees with
`AGENTS.md`, `AGENTS.md` wins. This file is a **redirect stub by design** —
additional harnesses get a pointer, never an independent duplicated rule set.
