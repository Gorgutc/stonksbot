---
name: state-machine-discipline
description: Use when designing or reviewing how a signal becomes an order and a position — model it as an explicit state machine with an audit trail, idempotent transitions, startup reconciliation, and adoption of external/manual changes instead of treating them as errors.
---

# State machine discipline

The expensive bugs are not in the MA20/MA50 formula — they are in the transitions:
signal → proposal → confirm → order submitted → partial fill → restart → cancel
remaining → adopt position → exit. Model this explicitly.

1. **Explicit states with an audit trail.** Signal/order/position move through named
   states: `candidate` → `risk_rejected` / `awaiting_confirmation` → `expired` /
   `submitted` → `partially_filled` / `filled` → `cancel_requested` / `cancelled` →
   `reconcile_required`. Every transition is journaled (who/what/when/why).
2. **Idempotent transitions.** Re-processing an event or restarting mid-flight must
   not double-submit or double-count. Use the client order id (see
   `broker-api-contract`).
3. **Reconcile on startup/restart** before trading: what orders are active, what
   positions are really open, does the local journal match the broker? Retry on
   transient mismatch; on persistent mismatch block new entries but allow protective
   exits only.
4. **Stale proposals die.** A `proposal_id` has a TTL and is invalidated after a
   reboot; an old Telegram button must not fire a trade.
5. **Adopt external/manual changes** via reconciliation, not as errors: a manual buy
   of an approved ticker is adopted and managed; a manual sell updates state; a cash
   deposit/withdrawal recomputes limits. A manual position outside the approved list
   defaults to observe-only until the owner chooses `managed_only` / `approved`.
6. **Two-layer PnL.** The journal must be able to separate economic strategy PnL from
   broker/tax PnL once deposits, adopted positions, and external cash mix.

Pair with the `risk-invariant-auditor` subagent.
