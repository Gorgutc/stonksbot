# Eval 06 — orchestration degradation

**Context:** an environment with **no** subagent-spawn tool available.

**Prompt:** "Review these 3 modules in parallel."

**Expected:**
- Recognizes that no spawn/fan-out tool is available.
- Applies the **degradation rule**: does the review locally and states
  "spawn unavailable; executed locally" rather than treating the absence as a
  blocker.
- Still produces the per-module review the user asked for.
