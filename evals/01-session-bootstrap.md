# Eval 01 — session bootstrap

**Prompt:** "Start working on a new feature: add input validation to the signup form."

**Expected:**
- Uses the `session-bootstrap` skill: reads `AGENTS.md` (+ PROJECT SPECIFICS), runs
  `git status -sb`, notes the verify command from `.agent-kit.json`.
- Classifies the task as **narrow** (single focused change), not broad.
- States a one-line plan before editing.
- After editing, the post-edit hook runs `verify.fast` (or no-ops if the file is
  outside `verifyPaths`).
