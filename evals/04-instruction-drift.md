# Eval 04 — instruction-drift audit

**Prompt:** "Check our agent instructions for anything stale or contradictory."

**Expected:**
- Uses the `instruction-drift` skill / `instruction-drift-auditor` subagent.
- Runs `node tools/check-kit.mjs` for the structural part (mirror parity, hooks,
  stubs).
- Compares AGENTS.md / CLAUDE.md / GEMINI.md / skills / hooks for contradictions
  and stale facts.
- Classifies each finding **KEEP / REWRITE / ARCHIVE / USER-DECISION** with
  file:line evidence; does not edit.
