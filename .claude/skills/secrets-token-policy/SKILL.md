---
name: secrets-token-policy
description: Use whenever tokens, API keys, account ids, or any secret are involved — a broker token is the key to a brokerage account, so it never goes in code, config files, logs, the dashboard, or Telegram.
---

# Secrets & token policy

A T-Invest full-access token can place real orders. Treat it as the most dangerous
secret in the project.

1. **Never** put a token in code, a committed config file, logs, the dashboard, or
   any Telegram message. Load only from environment variables or a secret store.
2. **Separate tokens per mode:** sandbox, live_confirm, live_auto_small. The live
   token is connected only after the backtest + a paper/sandbox month + an explicit
   owner decision to go live.
3. **Startup scope check.** Verify the token's scope at startup and block trading if
   it is broader/narrower than expected. Prefer account-scoped tokens.
4. **Rotation / revoke.** Plan for rotating and revoking tokens; the broker does not
   store them for you. A token can be revoked at any time.
5. **Storage.** Local (Windows): environment or OS credential store; VPS: a secret
   store / env, never a repo file. `.env` is git-ignored and never committed.
6. **Logging hygiene.** Never log request headers or anything that could echo a
   token. Redact account ids in user-facing output where not needed.

If you find a secret in tracked content, stop and surface it — do not "fix it later".
