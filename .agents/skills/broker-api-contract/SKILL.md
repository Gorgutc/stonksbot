---
name: broker-api-contract
description: Use when writing or reviewing anything that talks to the T-Invest (T-Bank) API — token scopes, instrument normalization, pre-order checks, rate limits, idempotency, reconciliation, and sandbox/live separation.
---

# Broker API contract (T-Invest / T-Bank)

The adapter's job is to talk to the broker *safely and deterministically*, not "to
trade". Discipline:

1. **Tokens by scope.** Use read-only for data/monitoring, a separate full-access
   (ideally account-scoped) token for trading, and a sandbox token for tests. Check
   the token scope on startup; block trading if it is unexpected. (See
   `secrets-token-policy`.)
2. **Normalize instruments by `instrument_uid`**, not FIGI as the primary key. Keep a
   reference table: uid, ticker, lot size, `min_price_increment`, currency, trading
   status, whitelist status.
3. **Pre-order checks, every time.** Before building an order: instrument tradable,
   trading status (`NORMAL_TRADING` for new entries — not `DISCRETE_AUCTION` /
   `SESSION_CLOSE` / dealer/weekend), last price, `min_price_increment`, lot size. The
   strategy's risk math must pass through this mechanics layer. Consider
   `GetOrderPrice` for pre-trade cost estimation.
4. **Idempotency.** Every `postOrder` carries a client order id; never submit a
   duplicate after a restart/retry.
5. **Rate limits.** Stay within ~50 req/s total across accounts/tokens, `postOrder`
   ~15 req/s, and the stream connection/subscription limits; layer reconnect / retry
   / backoff. This is client-side engineering, not HFT.
6. **Reconciliation.** On startup/restart sync positions + active orders from the
   broker before trading; retry on transient mismatch; block new entries on a
   persistent mismatch (but still allow protective exits).
7. **Sandbox vs live are separate contours** and the sandbox is **not** proof of
   real-market execution quality.
8. **Sber is phase 2** (QUIK-based) — do not start a Sber integration in the MVP.
