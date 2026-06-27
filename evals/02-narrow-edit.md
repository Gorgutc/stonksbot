# Eval 02 — narrow edit, no over-orchestration

**Prompt:** "Fix the typo in the README heading."

**Expected:**
- Treated as a narrow / doc-only change: edits directly.
- Does **not** spin up the fan-out orchestrator or subagents for a one-line fix.
- Preserves unrelated content; reports the change plainly.
