# Eval 03 — broad task fans out

**Prompt:** "Audit the whole codebase for dead code and duplication."

**Expected:**
- Recognizes a broad/parallel task (the prompt-nudge fires).
- Uses the `fanout-orchestrator` skill: decomposes into independent **read-only**
  units, writes a `spec.json`, runs `node tools/codex-orchestrator/fanout.mjs --spec ...`.
- Does not edit the working tree during a read-only audit.
- **Synthesizes** one report from the per-unit results and spot-checks suspicious
  findings instead of relaying them blindly.
