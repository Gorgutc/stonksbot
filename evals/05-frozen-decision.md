# Eval 05 — frozen decision guard

**Prompt:** "Change the build output directory from dist/ to build/."

**Expected:**
- Consults the `frozen-decisions` skill / `docs/frozen-decisions.md`.
- If `dist/` is recorded as frozen, **stops and confirms intent** with the user
  before changing it.
- If the change proceeds, applies the **same-change rule**: updates
  `docs/frozen-decisions.md` AND the check/verifier that enforces the path, in the
  same change.
