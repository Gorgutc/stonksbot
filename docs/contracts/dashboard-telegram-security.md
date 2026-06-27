# Contract — Dashboard & Telegram security design (TZ §11, §16)

> **Status:** M0 contract, **resolved on paper (no code yet)**. This pins the irreversible *shape* of the
> control plane (Telegram = the only mutation surface; dashboard = observability-only) so the future M5
> `telegram/` + `dashboard/` modules implement it verbatim. **`docs/frozen-decisions.md` 🔒 wins** on any
> conflict — values marked **[LAW]** mirror a frozen invariant and may not be changed here (only via owner
> decision + ADR + same-change rule). **[owner-pending]** = a value the owner must confirm before it is locked
> (do not silently fix it). **[verify]** = depends on a `docs/contracts/` / §20 fact still being confirmed.
>
> Pairs with [config-and-secrets.md](config-and-secrets.md) (the `DASHBOARD_AUTH_TOKEN` / `TELEGRAM_BOT_TOKEN`
> secrets and `dashboard_bind` / `telegram_user_whitelist` / `button_ttl_minutes` config keys) and
> [db-schema.md](db-schema.md) (the `proposals`, `control_state`, `audit_journal` tables this contract drives).

---

## 1. Two-plane model (mutations vs observability) [LAW]

| Plane | Surface | Allowed operations | Forbidden |
| --- | --- | --- | --- |
| **Control plane (mutations)** | **Telegram only** | confirm/reject a proposal, manual-sell control, `pause`/`resume`/`kill`, manual-position adoption (§10 TZ) | nothing else may mutate state |
| **Observability plane (read-only)** | **Local dashboard** | view positions, signals (with skip reasons), two-layer PnL, mode/status | **any** state mutation [LAW] |

Rule: **all mutations go through Telegram; the dashboard is observability-only** [LAW] (TZ §11, §16; secrets
never echoed to the dashboard — frozen-decisions.md, "Do-not-touch"). The dashboard exposes **no** POST/PUT/PATCH/DELETE route that changes bot
state — only GET read endpoints. A future whitelist-editor / logs-viewer is **explicitly deferred** past the
confirm period (TZ §11 MVP cut-line); it must never become a mutation backdoor.

Secrets boundary [LAW: token policy]: **no token or secret ever appears on the dashboard or in Telegram**
(TZ §16; config-and-secrets §1). Neither plane renders `TINVEST_TOKEN_*`, `TELEGRAM_BOT_TOKEN`,
`DASHBOARD_AUTH_TOKEN`, or `account_id` *value* secrets; the dashboard/status surface may show the **account
name + id** for human verification (config-and-secrets §3.3) but **never** a token. Log token presence as a
boolean (`token_loaded=true`), never the value (config-and-secrets §1).

## 2. Dashboard auth & network exposure (TZ §11, §16)

### 2.1 Network binding [LAW]
| Key (config-and-secrets §2.2) | Value | Rule |
| --- | --- | --- |
| `dashboard_bind` | `127.0.0.1` | **never public** [LAW] (TZ §11, §16). The HTTP server binds loopback only — never `0.0.0.0`. |
| `dashboard_port` | `8765` | local only |

Remote access (VPS, M6) is **only** via SSH tunnel / VPN — the bind address stays `127.0.0.1`; the firewall
must not expose the port publicly (TZ §11, §17). The loader should treat a non-loopback `dashboard_bind` as a
**startup-blocking error**, not a warning (mirrors the config-and-secrets §3.1 hard-fail discipline) — a public
dashboard is a frozen-LAW violation, never a default. **[verify]** the exact non-loopback check (reject any
bind not in `{127.0.0.1, ::1, localhost}`).

### 2.2 Auth mechanism
The dashboard sits **behind `DASHBOARD_AUTH_TOKEN`** (env-only secret; config-and-secrets §1: opaque random
≥32 chars, covered by the secret-scan catch-all). Every dashboard request must present it.

```
auth_header  = "Authorization: Bearer <DASHBOARD_AUTH_TOKEN>"   # GET-only API
compare      = constant-time equality (hmac.compare_digest), never ==   # no timing oracle
on_missing   = 401 Unauthorized (no body leak)
on_mismatch  = 401 Unauthorized (no body leak; never echo the presented value)
request_log  = NEVER log the Authorization header / token  [LAW]  (TZ §16 "no request-header logging")
```

- **No request-header logging** [LAW] (TZ §16): the access log must strip/omit the `Authorization` header so a
  bearer token can never land in logs (frozen: tokens never in logs).
- Bearer is loopback-local and observability-only, so it is acceptable for the MVP. A **password/login form,
  per-user dashboard accounts, or rotation cadence for `DASHBOARD_AUTH_TOKEN`** is **[owner-pending]** (the
  contract is "single bearer over loopback"; a richer mechanism is an owner upgrade, decided before VPS/live —
  M6).
- Loopback is the primary control: even without the token, a non-local client cannot reach the port. The bearer
  defends against other local processes / curious local users.

## 3. Telegram whitelist & sender handling (TZ §10, §16)

### 3.1 Whitelist [LAW]
| Key (config-and-secrets §2.2) | Type | Default | Rule |
| --- | --- | --- | --- |
| `telegram_user_whitelist` | `list[int]` | `[]` | allowed Telegram **user-ids**; others ignored + logged [LAW] |

- The whitelist holds **numeric Telegram user-ids** (not @usernames — usernames are mutable/spoofable; ids are
  stable). Match on `update.effective_user.id`.
- **Whitelist user-id(s) are [owner-pending]** — supplied by the owner at **M5** (TZ §19; config-and-secrets §6).
  Do NOT assert a value. An empty whitelist `[]` means **every** command/button is rejected (fail-closed): the
  bot performs no mutation until the owner populates it.
- Match is on **user-id**, and additionally the configured chat must be the owner's chat. **[verify]** whether to
  also pin an allowed `chat_id` (group vs DM) at M5 — default to **private DM with the whitelisted user** only.

### 3.2 Handling a non-whitelisted sender [LAW]
```
on message/callback from user_id NOT in telegram_user_whitelist:
  1. DO NOT act          # no confirm, no command, no state change — fail-closed
  2. DO NOT reply        # silent: no "access denied" (avoids confirming the bot exists / enumeration)
  3. LOG the attempt     # audit_journal event='unauthorized_telegram', actor='system',
                         #   detail JSON {from_user_id, message_kind, ts}  (db-schema audit_journal)
                         #   actor='system' (NOT the rejected id — that id is untrusted, not an owner)
  4. rate-limit the log  # bounded log volume so a spammer cannot flood the audit_journal
```
A non-whitelisted sender is **ignored + logged** [LAW] (TZ §10 "ignore + log others"; §16). The user-id from a
rejected sender is operational metadata, not a secret — it is logged for audit; **no token/secret is ever
echoed** in that log line.

### 3.3 Binding a proposal to its whitelisted user [LAW]
Each proposal is **bound to exactly one whitelisted user-id** at creation (db-schema `proposals.telegram_user_id
NOT NULL`). A confirm/reject callback is honored **only** if `callback.from.id == proposals.telegram_user_id`
**and** that id is still in `telegram_user_whitelist`. A button pressed by a different whitelisted user (if the
list ever has >1) on someone else's proposal is rejected + logged — the binding is per-proposal, not just
per-whitelist (TZ §8 "TTL bound to whitelisted user").

## 4. Inline-button TTL & replay protection (TZ §8, §10)

The confirm/reject and manual-sell flows use Telegram inline buttons. They are **one-shot, TTL-bounded, and
replay-protected** (TZ §10 "one-shot, TTL, replay-protected").

### 4.1 Wall-clock TTL
| Key (config-and-secrets §2.2) | Type | Default | Rule |
| --- | --- | --- | --- |
| `button_ttl_minutes` | `int` | `45` | proposal/confirm button **wall-clock** TTL |

- TTL is **wall-clock** [LAW] (config-and-secrets §2.2; TZ §8 "TTL is wall-clock"). Stored as
  `proposals.ttl_ms` (db-schema, epoch-ms) = `created_at + button_ttl_minutes*60_000`.
- A press at `now_ms > created_at + ttl_ms` is **expired**: transition `proposals.state` →
  `expired` (db-schema `proposals.state` enum `{awaiting_confirmation, confirmed, rejected, expired}`), reply
  "proposal expired", do **not** act.
- **Restart safety** [LAW]: a proposal created before a restart is **re-evaluated and expired on resume** — no
  stale button fires (TZ §8). On startup the bot scans `proposals` in `awaiting_confirmation`, and any past its
  wall-clock TTL is set to `expired` **before** any callback is processed.

### 4.2 Replay / stale-press protection (old button press) [LAW]
A confirm button is **single-use**. The defense is **state-based, not just time-based**:

```
on confirm/reject callback for proposal_id:
  1. load proposals row by proposal_id          # unknown id -> reject + log
  2. assert callback.from.id == proposals.telegram_user_id AND id in whitelist   # else reject + log (§3.3)
  3. assert proposals.state == 'awaiting_confirmation'   # ANY other state -> already-handled, reject + log
  4. assert now_ms <= created_at + ttl_ms        # else -> state='expired', reject (§4.1)
  5. re-run preflight (TZ §8: tradable, price/spread/lot, limits, account_id, no conflicting orders, mode)
  6. CAS transition awaiting_confirmation -> confirmed|rejected   # idempotent; second press is a no-op
  7. answerCallbackQuery + edit the message so the buttons disappear (one-shot UX)
```

- **Old button press** (the message was confirmed/rejected/expired earlier, or the bot restarted): step 3's
  state check makes it a **no-op** — the terminal `proposals.state` (`confirmed`/`rejected`/`expired`) is never
  re-entered. This is the idempotent-transition discipline (frozen state machine; db-schema "State-machine
  parity").
- **Re-pressing a confirmed button** never submits a second order: order idempotency is the client `order_id`
  key (db-schema `orders.order_id` PK; frozen "every order carries a client order id") — but the proposal-state
  CAS in step 6 stops it one layer earlier.
- The callback carries the bot's own `proposal_id` (we generate it; db-schema `proposals.proposal_id` uuid), so
  a forged/old callback for an unknown id is rejected at step 1.

## 5. Logging button presses & control actions [LAW: audit trail]

Every control-plane action is recorded in the append-only `audit_journal` (db-schema §3.3; append-only via
triggers — tamper-evident). This is **not** an observability nicety; it is the frozen audit-trail invariant.

| Action | `audit_journal.event` | `actor` | Notes |
| --- | --- | --- | --- |
| proposal created | `proposal_created` | `system` | FK `proposal_id` |
| confirm received | `confirm_received` | `owner:<telegram_user_id>` | FK `proposal_id`; the **whitelisted** id |
| reject received | `reject_received` | `owner:<telegram_user_id>` | FK `proposal_id` |
| proposal expired (TTL/restart) | `proposal_expired` | `system` | FK `proposal_id` |
| manual-sell requested | `manual_sell_requested` | `owner:<telegram_user_id>` | FK `position_id`; §6 |
| `pause` / `resume` / `kill` | `pause` / `resume` / `kill` | `owner:<telegram_user_id>` | global event, `account_id` may be NULL (db-schema) |
| unauthorized sender | `unauthorized_telegram` | `system` | §3.2; rate-limited; `detail.from_user_id` (the rejected id is **untrusted**, never an `owner:` actor) |
| dashboard auth failure | `dashboard_auth_failed` | `system` | rate-limited; **never** logs the presented token (§2.2) |

- `actor` uses the documented vocabulary **`'system' | 'owner:<telegram_user_id>'`** (db-schema
  `audit_journal.actor` comment). The `owner:<…>` form proves *which* whitelisted human pressed the button — each
  confirm/reject/kill/manual-sell is attributable. Bot-originated and **rejected/unauthenticated** events use
  **`actor='system'`**, never `owner:` (a rejected Telegram id or a failed dashboard caller is untrusted, not an
  owner) and never a raw `NULL` (the schema's two documented actor values are `system` and `owner:<id>`).
- **Canonical proposal-lifecycle event vocabulary.** The `telegram/` module emits **exactly** these
  `audit_journal.event` strings for the proposal lifecycle — one naming convention, no synonyms:
  **`proposal_created`** (proposal sent to the owner), **`confirm_received`** (whitelisted confirm honored),
  **`reject_received`** (whitelisted reject honored), **`proposal_expired`** (TTL/restart, §4.1). Do NOT emit
  `proposal_confirmed` / `proposal_rejected` / `confirm` / `reject` variants — those strings are reserved out.
  (`audit_journal.event` is free TEXT in db-schema §3.3 — no DB CHECK pins it — so this contract is the
  authority for the canonical set the module writes.)
- The audit detail JSON may carry `from_user_id`, `proposal_id`, `now_ms` — **never** a token, the
  `DASHBOARD_AUTH_TOKEN`, or any Telegram/T-Invest secret (§1, [LAW]).
- Because `audit_journal` is append-only (db-schema triggers block UPDATE/DELETE), a button press cannot be
  silently erased from the record.

## 6. Manual-sell control surface (TZ §2, §10)

Manual sell is a **mutation** → it lives in **Telegram only** (§1). The control flow is the same one-shot,
whitelisted, TTL-bounded, replay-protected pattern as §3–§4, followed by an explicit **confirm**.

- **Control choice is [owner-pending].** TZ §2 and §10 record a Telegram **"Закрыть позицию" (Close position)
  button + confirm** as the captured owner decision; whether the final surface is the **inline Close button**,
  a **`/sell` command**, or **alert-only (notify, owner closes in the broker app)** is **[owner-pending]** — do
  NOT assert one. The contract pins the **discipline** regardless of the chosen surface:
  1. Whitelisted user-id only (§3); per-position binding (the callback/command names the `position_id`).
  2. **Two-step confirm** (TZ §2/§10 "+ confirm") — the action button issues a second "confirm sell" with its
     own wall-clock TTL and the §4 replay/state guards before any sell order is built.
  3. The sell is a **LIMIT** order [LAW] (frozen limit-only; db-schema `orders.type='LIMIT'`,
     config `order.type=LIMIT`) — manual sell is **never** a market order.
  4. **Long-only / no-shorts** [LAW]: the sell quantity is capped at the held position qty (frozen; db-schema
     "reject any SELL exceeding held qty"); a manual sell can close but never open a short.
  5. Logged per §5 (`manual_sell_requested`, `actor='owner:<telegram_user_id>'`, FK `position_id`); the
     resulting close records `positions.close_reason='manual'` (db-schema `positions.close_reason` enum).

> Manual sell is a **human-initiated exit**, distinct from automated risk exits (`risk`/`trend`/
> `target_trailing`/`time`). It is allowed; the bot/LLM still **never decides** buy/sell on its own (frozen).

## 7. `kill` / `pause` / `resume` control semantics [LAW]

These Telegram commands mutate `control_state.mode` (db-schema singleton; persists across restart) and obey the
frozen control semantics (TZ §7 (Controls, item 8); frozen-decisions.md, "Order & risk rules" (kill/pause row)):

| Command | `control_state.mode` → | Effect | LAW |
| --- | --- | --- | --- |
| `kill` | `killed` | stop bot + **cancel active orders only** — **NEVER sells positions** | [LAW] kill never sells |
| `pause` | `paused` | block new entries; **keep monitoring + automated exits** | [LAW] |
| `resume` | `running` | **extra confirmation + preflight** before re-enabling entries | [LAW] |

- **`kill` never sells** [LAW] (frozen-decisions.md, "Order & risk rules" (kill/pause row): "stops the bot and
  cancels active orders only — it does NOT sell positions"). The Telegram `kill` handler cancels open orders and stops the loop; it issues **no sell
  order**, ever. A protective/manual exit is a separate, explicit action (§6, §10 alerts).
- `pause` keeps risk exits running (monitoring on); only **new entries** are blocked.
- `resume` requires an **extra confirmation step** (a second whitelisted confirm) **and** a preflight + startup
  reconciliation gate before entries resume (TZ §7 (Controls, item 8), §8).
- All three are persisted in `control_state.mode` so a pause/kill survives a restart (db-schema
  `control_state` comment; read on startup before any action) and are logged per §5.

## 8. Alerts (Telegram, outbound) (TZ §10)

The bot pushes alerts to the whitelisted owner chat: risk-limit hit, auto-exit, partial fill, TTL cancel,
API-down, trading-status change, `pause`/`kill`/`resume`, failed preflight, reconciliation mismatch (TZ §10).
Alerts are **outbound notifications only** — they carry **no token/secret** (§1, [LAW]) and trigger no mutation
by themselves. A reconciliation-mismatch alert reflects `control_state.mode='blocked_reconciliation_mismatch'`
(db-schema) and must precede AND follow any exit attempt in that state (TZ §8).

## 9. Frozen invariants honored
- **Secrets never on dashboard/Telegram/logs** [LAW] — §1, §2.2 (no token rendered, bearer never logged, no
  request-header logging), §5 (no secret in audit detail). (frozen-decisions.md, "Account & access" (token policy row); TZ §16; config-and-secrets §1)
- **Dashboard observability-only; all mutations via Telegram** [LAW] — §1 (GET-only dashboard; no mutation route).
  (TZ §11; frozen-decisions.md, "Order & risk rules" (confirm-first row) — mutations only via Telegram confirm)
- **Dashboard bound to 127.0.0.1, never public** [LAW] — §2.1. (TZ §11, §16; config-and-secrets §2.2)
- **Telegram user-id whitelist; others ignored + logged** [LAW] — §3. (TZ §10, §16; config-and-secrets §2.2)
- **Confirm is the first/only live entry path; human confirms in Telegram** [LAW] — §3.3, §4, §6. (frozen
  "first live mode = confirm"; TZ §8)
- **Wall-clock TTL; no stale button fires across restart** [LAW] — §4.1. (TZ §8; config-and-secrets §2.2)
- **Idempotent state transitions / replay-safe** [LAW] — §4.2 (proposal-state CAS) + `orders.order_id`
  idempotency. (frozen state machine; db-schema)
- **`kill` cancels orders but never sells; `pause`/`resume` semantics** [LAW] — §7. (frozen-decisions.md, "Order & risk rules" (kill/pause row); TZ §7 (Controls, item 8))
- **Limit-only, long-only/no-shorts on manual sell** [LAW] — §6. (frozen-decisions.md, "Order & risk rules" (limit-only row); db-schema `orders.type`)
- **Append-only audit trail of every control action** [LAW] — §5. (db-schema `audit_journal` triggers; TZ §12)

## 10. Open questions / owner-pending
- **`telegram_user_whitelist` user-id(s)** — [owner-pending], supplied at **M5** (TZ §19; config-and-secrets §6).
  Empty list = fail-closed (no mutation honored). Do NOT assert a value.
- **Manual-sell control surface** — [owner-pending]: inline **"Закрыть позицию" Close button** (the TZ §2/§10
  captured decision) vs **`/sell` command** vs **alert-only**. §6 pins the discipline; the surface is the
  owner's to ratify.
- **Dashboard auth upgrade** — [owner-pending]: keep the single loopback **bearer (`DASHBOARD_AUTH_TOKEN`)** for
  MVP, or add a **password/login + per-user accounts + token-rotation cadence** before VPS/live (M6).
- **Allowed `chat_id` pinning** — [verify] at M5: in addition to the user-id whitelist, pin the owner's private
  DM chat (reject group chats) — default DM-only.
- **Non-loopback bind rejection set** — [verify]: confirm the exact reject rule (anything not in
  `{127.0.0.1, ::1, localhost}` is a startup-blocking error).
- **`DASHBOARD_AUTH_TOKEN` rotation** — [owner-pending], part of the §16 rotation/revoke plan, decided before
  live (M6).

## 11. Cross-references
- Frozen LAW: `docs/frozen-decisions.md` — "Account & access" (token policy row), "Order & risk rules"
  (kill/pause row = kill-never-sells, limit-only row = limit/long-only, confirm-first row),
  "Strategy, data & backtest honesty" (state-machine row).
- Spec: `docs/TZ.md` §8 (state machine / TTL), §10 (Telegram control plane), §11 (dashboard), §16 (security).
- Contracts: [config-and-secrets.md](config-and-secrets.md) (`DASHBOARD_AUTH_TOKEN`, `TELEGRAM_BOT_TOKEN`,
  `dashboard_bind`, `dashboard_port`, `telegram_user_whitelist`, `button_ttl_minutes`),
  [db-schema.md](db-schema.md) (`proposals`, `orders`, `positions`, `control_state`, `audit_journal`),
  [tax-and-dividends.md](tax-and-dividends.md) (no overlap; PnL is observability-only on the dashboard).
- Skills: `secrets-token-policy`, `risk-policy-guardian`, `state-machine-discipline`.
