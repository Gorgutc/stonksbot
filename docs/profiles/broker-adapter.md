# Profile: broker-adapter  (status: dormant)

Governs the **T-Invest (T-Bank) API adapter** — the only component that owns
broker-account, portfolio, order-state, and order-placement API calls. Its job is
not "to trade" but to prove the system can talk to the broker *safely and
deterministically*.

## Scope
- Broker-account read paths: list accounts, read portfolio / positions / orders /
  trading status; instrument normalization by `instrument_uid` (not FIGI as the
  primary key).
- Market-data exception while dormant: the active `research-backtest` profile may
  keep read-only market-data persistence and may later add read-only T-Invest
  market-data access only under the secrets/token policy and without order
  capability. Any broker-account read path, order path, full-access/live token
  handling, or trading loop still belongs here and requires activation.
- Write paths: place / cancel **limit** orders in the **sandbox** first; map
  external order statuses deterministically into the internal state machine.
- Pre-order checks: instrument is tradable, trading status (`NORMAL_TRADING` vs
  `DISCRETE_AUCTION` / `SESSION_CLOSE` / …), last price, `min_price_increment`,
  lot size — every order math result must pass through these.
- Operational discipline: token scopes (read-only vs full-access vs sandbox;
  prefer account-scoped), rate limits (~50 req/s total, `postOrder` ~15 req/s),
  reconnect / retry / backoff, idempotency, audit log of every request.
- Reconciliation: sync positions + active orders from the broker on
  startup/restart before trading.

## Status rule
- **dormant** — rules exist; NO broker-account SDK / dependency / network code may
  be introduced until an explicit request flips status to `active`. The narrow
  M1 read-only market-data exception above is the only carve-out.
  `component-guardian` enforces.

## Active toolchain (when active)
Leave empty while dormant. Intended: official T-Invest Python SDK over the
**sandbox** endpoint first, then live with a separate account-scoped token.

## Decision checklist (fill when activated)
- [ ] exact token scopes + per-mode tokens (sandbox / live_confirm) recorded
- [ ] instrument normalization layer (uid/figi/lot/min_increment) implemented
- [ ] rate-limit + retry/backoff policy implemented and tested against mocks
- [ ] startup reconciliation flow + retry policy implemented

## Explicit defers
- **Sber is phase 2** and out of the MVP (its retail path is QUIK-based, not a
  comparable public trading API) — do not start a Sber integration here.
- HFT / second-resolution streaming, non-display exchange data feeds.

## Verification
Adapter contract tests against mock responses; the `broker-api-contract` skill
gates this layer. Sandbox is for plumbing only — it is **not** proof of real-market
execution quality.
