# Profile: execution-confirm  (status: dormant)

Governs the **live confirm-mode loop** — the first real-money product. The bot
*proposes* an entry, the human *confirms* in Telegram, and protective exits are
automated. This profile is activated last, only after research-backtest and
broker-adapter are green and a paper/sandbox month has passed.

## Scope
- Risk engine: capital / limit / lot / daily-loss checks; refuses anything that
  violates the frozen risk invariants (`docs/frozen-decisions.md`).
- Proposal service: one-shot `proposal_id` with a TTL, bound to an allowed
  Telegram user id; re-runs preflight after the user confirms.
- Telegram control plane (NOT the trading engine): proposal buttons
  Подтвердить / Отклонить, `/status` `/pause` `/resume` `/kill`, alerts, reports.
- Order gateway + position manager: place the confirmed limit order, manage exits
  (hard stop, trend-break, target_then_trailing, time exit) automatically.
- Controls: `pause` blocks new entries but keeps monitoring/exits; `resume` needs
  extra confirmation + preflight; `kill` stops the bot and cancels active orders
  **only** — it does NOT sell positions.
- Audit journal + reconciliation + dashboard (observability only; localhost-bound).

## Status rule
- **dormant** — rules exist; NO toolchain / dependency / live order code may be
  introduced until an explicit request flips status to `active`, and only after
  the pre-live gates pass. `component-guardian` enforces.

## Active toolchain (when active)
Leave empty while dormant. Intended: FastAPI (dashboard backend), python-telegram-bot
(control plane), APScheduler/cron (daily workflow), SQLite → Postgres (journal).

## Decision checklist (fill when activated)
- [ ] order/position state machine implemented with full audit trail
- [ ] Telegram user-id whitelist + button TTL + replay protection
- [ ] kill/pause/resume semantics implemented exactly as frozen
- [ ] dashboard auth + bind to 127.0.0.1 decided and implemented

## Explicit defers
- `auto_small` (semi-autonomous) and full-auto — architected but DISABLED in the
  MVP; revisit only after a successful confirm-mode live period.
- Partial sells, multi-position portfolios beyond the frozen limits.

## Verification
State-machine + risk-engine unit tests; the `state-machine-discipline`,
`risk-policy-guardian`, and `secrets-token-policy` skills gate this layer.
