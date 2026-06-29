# Contract — VPS deployment & disaster-recovery runbook (TZ §17)

> **Status:** M0 contract, **resolved on paper (no infra code yet)**. This pins the irreversible *shape* of
> the M6a infrastructure so the future Docker Compose / systemd / migration / backup code implements it
> verbatim. **`docs/frozen-decisions.md` 🔒 wins** on any conflict — values marked **[LAW]** mirror a frozen
> invariant and may not be changed here (only via owner decision + ADR + same-change rule).
> **[owner-pending]** = a value the owner must confirm before it is locked (do not silently fix it).
> **[verify]** = an empirical fact to confirm at provisioning time; not asserted here.
>
> **Milestone:** **M6a** — VPS provisioning + Postgres migration + secret store + secret-scan gate + DR
> runbook — runs **in parallel** with the local (Windows) ≥30-day paper window (ROADMAP M6; TZ §17). M6b
> (pre-live gate check + owner approval) is **not** in this contract. No live token is connected during M6a.
> Pairs with [config-and-secrets.md](../contracts/config-and-secrets.md), [db-schema.md](../contracts/db-schema.md),
> and [tax-and-dividends.md](../contracts/tax-and-dividends.md).

---

## 1. Two-host topology (local Windows → VPS)

The bot lives on exactly **one** active host at a time. The VPS is provisioned during M6a but does **not**
trade until M6b owner approval. Never run two instances against the same `account_id` (duplicate-order risk —
the client order id is frozen LAW ([frozen-decisions.md](../frozen-decisions.md) "Order & risk rules": every
order carries a client order id / idempotency key) and enforced by `orders.order_id` being the PK in
[db-schema.md](../contracts/db-schema.md) §3.2, with the Idempotency invariant in
[db-schema.md](../contracts/db-schema.md) §4).

| Host | Role | DB backend | Mode reached here | Window |
| --- | --- | --- | --- | --- |
| **Local (Windows)** | paper / sandbox during the ≥30-day window | SQLite (`db_path`) | `paper`, `sandbox` | M1–M6a |
| **VPS (Linux)** | live confirm host (after M6b only) | Postgres (DSN in `db_path`) | `confirm` | M6b → live |

Rule (TZ §17): the ≥30-day paper window runs on the **local** host; the VPS is **prepared in parallel**
before the live gate. M6a provisions and dry-runs the VPS in `paper`/`sandbox` only.

## 2. Docker Compose (VPS service topology) [LAW: dashboard never public]

The VPS runs the bot under **Docker Compose** (TZ §17). Services and their network exposure:

```yaml
# services (shape, not a final compose file — secrets are injected, never baked)
services:
  bot:            # FastAPI internal API + scheduler + trading engine (one process group)
    ports: []     # NO published ports — dashboard reached via SSH tunnel/VPN only [LAW]
    restart: unless-stopped
    # secrets injected at runtime from the VPS secret store (§7), NEVER from a committed file
  db:             # Postgres (M6 switch-point §5); SQLite needs no service
    ports: []     # bound to the compose network only; never published to 0.0.0.0
    volumes: [ db_data ]     # named volume on an encrypted-at-rest disk [verify]
volumes:
  db_data:
```

Invariants encoded here:
- **No `bot` port is published** to the host's public interface. `dashboard_bind = 127.0.0.1`
  ([config-and-secrets.md](../contracts/config-and-secrets.md) §2.2) is honored *inside* the container; the
  port is reached only via an SSH tunnel or VPN (§6) [LAW: dashboard never public].
- **No secret is baked into an image or committed compose file.** Tokens/DSN come from the VPS secret store
  at runtime (§7) [LAW: token policy].
- **Postgres is not exposed** beyond the compose network (no `5432` on `0.0.0.0`).
- `restart: unless-stopped` is the container-level auto-restart; the host-level supervisor is §3.

## 3. Auto-restart, watchdog & time sync (systemd) [LAW: kill/pause survive restart]

Host-level supervision on the VPS uses **systemd** (TZ §17: "systemd/auto-restart"). On the local Windows
host the equivalent is a process supervisor/watchdog + prevent-host-sleep (TZ §17) — same intent, different
mechanism.

| Concern | VPS (Linux) | Local (Windows) | Rule |
| --- | --- | --- | --- |
| Auto-restart | `systemd` unit `Restart=on-failure` running `docker compose up` | process supervisor/watchdog (auto-restart) | crash → restart |
| Prevent sleep | (server; n/a) | prevent host sleep | bot must stay live for exits |
| Time sync | NTP (`chrony`/`systemd-timesyncd`) | NTP/time sync | epoch-ms UTC must be accurate (db-schema §1) |
| Boot order | start after Docker + network online | start on login/boot | reconcile before any action |

**Restart safety [LAW]:** on **every** start the bot reads `control_state.mode`
([db-schema.md](../contracts/db-schema.md) §3.3) — `running` / `paused` / `killed` /
`blocked_reconciliation_mismatch` — **before any action**, so `kill`/`pause` and a reconciliation-mismatch
block **survive a restart**. A restart must **never** auto-resume a `killed`/`paused`/`blocked` bot, and
**`kill` never sells positions** [LAW]. Startup runs the reconciliation gate
([db-schema.md](../contracts/db-schema.md) §3.3 `reconciliations`) and asserts `orders.account_id ==
config.account_id` (the account guard, [config-and-secrets.md](../contracts/config-and-secrets.md) §3) before
trading. The auto-restart loop must **not** mask a crash-loop: cap restarts (e.g. `StartLimitIntervalSec` /
`StartLimitBurst`) and alert the owner via Telegram on repeated failure rather than restarting forever
**[owner-pending]** exact thresholds.

## 4. SQLite → Postgres migration switch-point [LAW: switch-point explicit]

The SQLite→Postgres switch is **explicit and one-directional**, never automatic (TZ §17: "keep the SQLite→
Postgres switch point explicit"; [config-and-secrets.md](../contracts/config-and-secrets.md) §2.9
`db_switch_point`; [db-schema.md](../contracts/db-schema.md) §1 Postgres-compat).

```text
# DB backend is selected ONLY by config.db_path (config-and-secrets §2.1) — no auto-detect:
db_path = "./stonksbot.db"            -> SQLite   (MVP / local paper window)
db_path = "postgresql://.../stonksbot" -> Postgres (VPS, M6)
```

- **Switch-point (WHEN):** the SQLite→Postgres migration runs as a **discrete M6a step** — *after* the local
  paper window's data is final and *before* M6b live approval. The **exact trigger point** (e.g. on VPS
  cutover vs at first live-confirm start) is **[owner-pending]** — do not assume an automatic or time-based
  trigger; it is a deliberate, owner-gated operation.
- **Type mapping (HOW)** — applied verbatim from [db-schema.md](../contracts/db-schema.md) §1 Postgres-compat:

```text
INTEGER epoch-ms / *_units  -> BIGINT          # never lose precision on time/money integers
INTEGER PRIMARY KEY         -> identity column  # autoincrement equivalent
TEXT holding JSON           -> JSONB            # data_conflicts.detail, signals.features, exit_rules, audit detail
INTEGER 0|1 (booleans)      -> BOOLEAN
Money/price                 -> stays units/nano INTEGER pair (NEVER float) [LAW: no-float money, db-schema §1]
```

- **Enum parity preserved:** the frozen CHECK enums ([db-schema.md](../contracts/db-schema.md) §2) must map
  to equivalent Postgres constraints (CHECK or enum type) **without renaming any value** — a divergent enum
  silently weakens an invariant.
- **append-only audit preserved:** `audit_journal` keeps its UPDATE/DELETE block (triggers in SQLite → rules
  or triggers in Postgres) so the audit trail stays tamper-evident across the migration.
- **Migration safety:** the migration is **copy-then-verify**, not move — keep the source SQLite file
  (backed up per §8) until a **row-count + checksum** parity check on every table passes; only then does the
  VPS read Postgres. A failed parity check aborts the cutover (the bot does not start trading on a partial DB).

## 5. Firewall (deny-by-default) [LAW: dashboard never public]

VPS firewall is **deny-inbound by default**; open the minimum.

| Port / service | Inbound | Rule |
| --- | --- | --- |
| SSH (22 or custom) | allow from owner IP / key-only | management + dashboard tunnel transport (§6) |
| Dashboard (`dashboard_port` 8765) | **DENY public** [LAW] | reached only via SSH tunnel / VPN (§6); bound to `127.0.0.1` in-container |
| Postgres (5432) | **DENY public** | compose network only (§2) |
| All other inbound | **DENY** | deny-by-default |
| Outbound | allow T-Invest API, MOEX ISS, Telegram API, NTP, secret store | the bot must reach the broker/data/control plane |

SSH hardening: **key-only auth** (password auth disabled), root login disabled, optional fail2ban. The
dashboard auth token (`DASHBOARD_AUTH_TOKEN`,
[config-and-secrets.md](../contracts/config-and-secrets.md) §1) is **defense-in-depth on top of** the tunnel,
never a substitute for it.

## 6. Dashboard access via SSH tunnel / VPN — never public [LAW]

The dashboard is **never** exposed to the public internet (TZ §16, §17;
[config-and-secrets.md](../contracts/config-and-secrets.md) §2.2 `dashboard_bind = 127.0.0.1`). The owner
reaches it by forwarding the localhost-bound port over an encrypted channel:

```bash
# SSH local-forward: owner laptop :8765  ->  VPS 127.0.0.1:8765 (dashboard stays bound to loopback)
ssh -L 8765:127.0.0.1:8765 <user>@<vps-host>
# then open http://127.0.0.1:8765 on the owner's machine; auth with DASHBOARD_AUTH_TOKEN (Bearer)
```

- The VPS firewall **denies** inbound `8765` (§5); the only path in is the tunnel (or a VPN to the VPS).
- VPN-instead-of-SSH is an allowed alternative **[owner-pending]** (which one) — the invariant is "encrypted
  private channel, never a public listener", not the specific tool.
- **No request-header logging** (TZ §16) — never log the `Authorization` header or anything that could echo
  `DASHBOARD_AUTH_TOKEN` or a broker token [LAW: token policy / logging hygiene].

## 7. Secret store on the VPS [LAW: token policy] — backend [owner-pending] (decision 4)

Secrets are injected at runtime, **never** committed, baked into an image, logged, or shown in the dashboard /
Telegram (TZ §16; [config-and-secrets.md](../contracts/config-and-secrets.md) §1; `secrets-token-policy`
skill). The env keys are **exactly** those in [config-and-secrets.md](../contracts/config-and-secrets.md) §1:

```text
TINVEST_TOKEN_SANDBOX          # required when mode = sandbox  (sandbox-scoped)
TINVEST_TOKEN_LIVE_CONFIRM     # required when mode = confirm  (account-scoped where the product type allows [verify])
TELEGRAM_BOT_TOKEN             # control plane (always)
DASHBOARD_AUTH_TOKEN           # dashboard bearer (always); opaque random >=32 chars
# TINVEST_TOKEN_LIVE_AUTO_SMALL is intentionally ABSENT — auto_small is DISABLED in the MVP [LAW]
```

- **The secret-store backend on the VPS is [owner-pending] (decision 4).** The *contract* is "secrets via a
  secret store / env, never a repo file" ([config-and-secrets.md](../contracts/config-and-secrets.md) §1
  marks the storage backend owner-pending, "decided before live (M6)"). The specific mechanism (e.g. a
  managed secret manager, an encrypted env file mounted at runtime, or a credential store) is **not asserted
  here** — do not pick one silently.
- **Separate token per mode [LAW]:** only the token for the **active** `mode` is required at startup; a
  missing token for an inactive mode is not an error. During M6a the VPS runs `paper`/`sandbox` only, so no
  live token is present on the VPS until M6b.
- **Startup scope check BLOCKS trading [LAW]:** refuse to start (not warn) if the active token is missing,
  wrong-mode, read-only for `confirm`, or over-broad when account-scoping is available/required. If
  account-scoping is verified unavailable and owner-recorded, the guard-only full-access fallback relies on the
  `account_id` guard ([config-and-secrets.md](../contracts/config-and-secrets.md) §1).
- **Secret-scan gate [LAW]:** the pre-commit secret-scan gate (`tools/secret-scan.mjs`,
  [config-and-secrets.md](../contracts/config-and-secrets.md) §5; TZ §16) must be green **before any live
  profile activates** — committing any non-placeholder `*_TOKEN` / `DASHBOARD_AUTH_TOKEN` value is a leak.

### 7.1 Secret rotation & revoke [LAW: token policy]
- **Token lifetime** is 3-months-from-last-use (rolling); plan rotation before expiry
  ([config-and-secrets.md](../contracts/config-and-secrets.md) §1; TZ §16; `secrets-token-policy` skill #4).
- **Rotation procedure:** issue the new token in the broker UI → write it to the VPS secret store → restart
  the `bot` service so it reloads from the store → **revoke the old token** at the broker. Tokens live in
  process memory only; a restart is the rotation boundary. Log only `token_loaded=true` (a boolean), never the
  value ([config-and-secrets.md](../contracts/config-and-secrets.md) §1).
- **Revoke is owner-driven:** the broker does not store tokens for you; a compromised token must be revoked at
  the broker immediately, then rotated as above. `DASHBOARD_AUTH_TOKEN` rotates the same way (regenerate ≥32
  random chars → store → restart).
- **No token in the migration or backup path:** secrets are excluded from DB dumps and log archives (§8); a
  backup must never carry a live token.

## 8. Backups — DB + logs

Both the DB and the structured audit log are backed up; the **`audit_journal` append-only audit trail**
([db-schema.md](../contracts/db-schema.md) §3.3) and `cash_events` / `positions` / `orders` are the
load-bearing state for reconciliation and the two-layer PnL ([tax-and-dividends.md](../contracts/tax-and-dividends.md) §1).

| Artifact | Source | Method | Frequency | Retention |
| --- | --- | --- | --- | --- |
| **DB (SQLite)** | local `db_path` file | online backup (`sqlite3 .backup` / VACUUM INTO — never a raw copy of a live file) | daily [owner-pending] | [owner-pending] |
| **DB (Postgres)** | VPS `db` service | `pg_dump` (logical) + optional WAL/PITR [verify] | daily [owner-pending] | [owner-pending] |
| **Structured logs** | structlog/JSON audit log | rotate + archive (machine-readable audit trail, TZ §11) | rolling | [owner-pending] |

Rules:
- **Backups are secret-free [LAW]:** strip / never include tokens or `Authorization` headers from any DB dump
  or log archive (§7). A backup file is not a place a secret may leak into.
- **Money integers preserved:** dumps keep the `units`/`nano` integer pairs exactly (no float coercion) so a
  restore is byte-exact for reconciliation/idempotency ([db-schema.md](../contracts/db-schema.md) §1).
- **Off-host copy:** at least one backup copy lives off the trading host (encrypted in transit + at rest
  **[verify]**) so a host loss is recoverable.
- **Restore is tested, not assumed:** a restore drill is part of the DR procedure (§9) — an untested backup is
  not a backup.

## 9. Disaster recovery (DR) procedure

Recovery order after a host loss / corruption, honoring the frozen invariants:

1. **Stand up a clean host** (Docker Compose per §2, systemd per §3, firewall per §5, NTP synced).
2. **Inject secrets** from the secret store (§7) — never from a backup or repo file. Startup scope check
   must pass [LAW].
3. **Restore the DB** from the latest verified backup (§8); run the row-count + checksum parity check.
4. **Read `control_state.mode` first** ([db-schema.md](../contracts/db-schema.md) §3.3): if it was `killed` /
   `paused` / `blocked_reconciliation_mismatch`, the bot stays in that state — **DR never auto-resumes
   trading** and **never sells positions** [LAW].
5. **Reconcile against the broker** ([db-schema.md](../contracts/db-schema.md) §3.3 `reconciliations`; account
   guard §3 of config): assert `account_id == config.account_id`; adopt external/manual changes via
   reconciliation, not as errors; a `mismatch` → `blocked_reconciliation_mismatch`, owner-resolved, **no
   trading** until clean.
6. **Resume only with owner action:** `resume` needs extra confirmation + preflight [LAW]; live confirm
   resumes only after the account guard, token scope, and reconciliation are all green.

DR objectives (RTO/RPO) are **[owner-pending]** — set the recovery-time / recovery-point targets before live
(M6b); during M6a (paper/sandbox) there is no real-money exposure, so the drill validates the *procedure*, not
an SLA.

## 10. Frozen invariants honored

- **Dashboard never public [LAW]** — `dashboard_bind = 127.0.0.1`, no published port (§2), firewall denies
  `8765` (§5), reached only via SSH tunnel / VPN (§6). (TZ §16, §17;
  [config-and-secrets.md](../contracts/config-and-secrets.md) §2.2)
- **Token policy [LAW]** — secrets via VPS secret store / env only, never in code/config/logs/dashboard/
  Telegram; separate token per mode; `auto_small` token absent; startup scope check blocks trading; rotation
  + revoke planned; secret-scan gate green before any live profile (§7, §7.1).
- **SQLite→Postgres switch-point explicit [LAW]** — selected only by `db_path`
  ([config-and-secrets.md](../contracts/config-and-secrets.md) §2.1) with the explicit, owner-gated
  `db_switch_point` note (config §2.9: "documented, not auto"); a deliberate M6a step, copy-then-verify,
  enum/type/no-float parity preserved (§4).
- **`kill` / `pause` survive restart; `kill` never sells [LAW]** — `control_state.mode` read before any action
  on every start and after DR; auto-restart never auto-resumes a killed/paused/blocked bot (§3, §9).
- **Account guard [LAW]** — `account_id == config.account_id` asserted at startup, submit, reconcile, and DR
  (§3, §9). (config §3; db-schema §4)
- **No-float money [LAW]** — `units`/`nano` integer pairs preserved across migration and backup (§4, §8).
- **Reconciliation on startup [LAW]** — startup + post-restart + DR reconciliation gate; mismatch blocks
  trading (§3, §9).
- **Append-only audit trail [LAW]** — `audit_journal` UPDATE/DELETE block preserved across migration; backed
  up (§4, §8).

## 11. Open questions / owner-pending

- **Secret-store backend on the VPS (decision 4)** — the storage mechanism (managed secret manager vs
  encrypted runtime-mounted env vs credential store) is **[owner-pending]**; the contract fixes only "secret
  store / env, never a repo file" (§7; config §6).
- **Exact SQLite→Postgres migration trigger point** — the precise WHEN within M6a (VPS cutover vs first
  live-confirm start) is **[owner-pending]**; it is a deliberate owner-gated step, never automatic (§4).
- **VPN vs SSH tunnel** for dashboard access — **[owner-pending]**; the invariant is "encrypted private
  channel, never public" (§6).
- **Backup frequency + retention + off-host destination**, and **encryption-at-rest** for the DB volume /
  off-host copy — **[owner-pending]** / **[verify]** at provisioning (§8).
- **DR RTO / RPO targets** — **[owner-pending]**, set before M6b live (§9).
- **Auto-restart crash-loop thresholds** (`StartLimitBurst` etc.) and the Telegram failure alert — **[owner-
  pending]** exact values (§3).
- **Postgres WAL / PITR** beyond logical `pg_dump` — **[verify]** at provisioning (§8).
- **Account-scoped live token feasibility** — empirical: account-scoping is unavailable for some product types
  (TZ §20); **[verify]** at M4, then the §3 account guard is the fallback (config §6).

## 12. Cross-references
- Spec `docs/TZ.md` §16 (security), §17 (deployment local→VPS). Roadmap `docs/ROADMAP.md` M6a/M6b.
- Frozen LAW `docs/frozen-decisions.md` (token policy, account guard, kill never sells, reconciliation,
  no-float money). Config [config-and-secrets.md](../contracts/config-and-secrets.md) (§1 secrets, §2.1/§2.2
  `db_path`/`dashboard_bind`, §3 account guard, §5 secret-scan, §6 owner-pending). Schema
  [db-schema.md](../contracts/db-schema.md) (§1 Postgres-compat / no-float, §2 enums, §3.3 `control_state` /
  `reconciliations` / `audit_journal`). Taxes [tax-and-dividends.md](../contracts/tax-and-dividends.md).
- Skill: `secrets-token-policy`, `state-machine-discipline`. Subagents: `risk-invariant-auditor`.
- **Numbered owner decisions** (e.g. "decision 4", §7/§11) live in the Second Brain `Decisions/ADR-*` register
  (per `docs/frozen-decisions.md` "Same-change rule"); the secret-store backend is also tracked as
  [config-and-secrets.md](../contracts/config-and-secrets.md) §6 "Secret-storage backend … decided before live (M6)".
