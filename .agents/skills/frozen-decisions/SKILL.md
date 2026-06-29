---
name: frozen-decisions
description: Use before editing anything that touches a durable architectural decision, contract, or invariant — check the project's frozen list first, and when a frozen decision genuinely changes, update its documentation and its verifier in the same change.
---

# Frozen decisions

Some decisions are deliberately frozen (architecture, public contracts, file
layout, dependency pins). Before editing near them:

1. Find the frozen list — `docs/frozen-decisions.md` if present, else the
   `Do-not-touch` line in `AGENTS.md` → `PROJECT SPECIFICS`.
2. If your change touches a frozen item, STOP and confirm intent with the user
   unless they already authorized it.
3. **Same-change rule:** when a frozen decision genuinely changes, update BOTH its
   documentation AND the check/verifier that enforces it, in the same change —
   never let the doc and the guard drift apart.
4. Record new durable decisions in the frozen list with a one-line rationale.
