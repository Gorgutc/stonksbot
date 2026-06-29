# Contract — Windows local deployment & host-reliability ops spec (TZ §17)

> **Status:** M0/M6a ops contract, **resolved on paper (no code/toolchain yet)**. This pins the **host-reliability
> requirements** the Windows-local run must satisfy so the **≥30-day paper/sandbox window** (TZ §2, §14, §17) and
> the early **signal-only lab journal** (TZ §14) run unattended and honestly. **`docs/frozen-decisions.md` 🔒 wins**
> on any conflict — values marked **[LAW]** mirror a frozen invariant and may not be changed here.
> **[owner-pending]** = a value/mechanism the owner must confirm before it is locked (do not silently fix it).
> **[verify]** = depends on a `docs/contracts/` or §20 fact still being confirmed by research `whq6u1gxe` /
> empirical at M4/M6. **Doc only — this contract introduces NO toolchain, dependency, or build step**; the
> `deployment` component stays a **dormant profile** until the owner activates it (see `component-guardian`).
>
> Pairs with [config-and-secrets.md](../contracts/config-and-secrets.md), [db-schema.md](../contracts/db-schema.md),
> and [tax-and-dividends.md](../contracts/tax-and-dividends.md).

---

## 1. Scope & posture (TZ §2, §17)

- **Run environment (owner decision, TZ §2):** **Local (Windows) first → VPS before live confirm.** This contract
  covers **only the Windows-local host**; VPS hardening (Docker Compose, systemd, Postgres migration, secret store,
  firewall, SSH-tunnel dashboard, DR/secret-rotation runbook) is a separate M6a contract (TZ §17) and is **out of
  scope here** beyond the explicit migration seams called out in §9.
- **What runs here (TZ §3, §14):** the `paper` / `sandbox` modes only — the ≥30-day window and the early
  signal-only lab journal. **No live trading runs on this Windows host without the pre-live gates** (TZ §14): a
  passing 3-year backtest, ≥30 days of clean paper/sandbox, fill-model parity, and **manual owner approval** to go
  `confirm` — and `confirm` is intended for the VPS (TZ §2/§17). [LAW: phased path]
- **Posture:** the host must keep the bot process alive, the clock correct, and the state durable across reboots —
  without ever weakening a frozen risk/no-lookahead invariant. A reliability mechanism here may **never** itself
  place, cancel, or change an order, or auto-resume into trading (see §3, §4).

## 2. Host-reliability requirements (overview)

The ops items the verifier requires, each detailed below and cited to the TZ:

| # | Requirement | TZ | Section |
| --- | --- | --- | --- |
| R1 | Prevent host sleep / hibernate / display-driven suspend during run windows | §17 | §3 |
| R2 | Process supervisor / watchdog with auto-restart (no auto-resume into trading) | §17, §8 | §4 |
| R3 | NTP / system time sync (clock correctness for D1 close + TTL) | §17 | §5 |
| R4 | SQLite backups (durable, restorable, integrity-checked) | §17, §5.1 | §6 |
| R5 | Structured logs (machine-readable audit trail; secrets redacted) | §16, §17 | §7 |
| R6 | Schema versioning & migration discipline (no silent overwrite) | §5.1 | §8 |
| R7 | VPS migration seams (explicit switch points, no premature toolchain) | §17 | §9 |

## 3. R1 — Prevent host sleep / suspend (TZ §17)

The scheduler (`APScheduler`, TZ §4) fires the daily run **after the final MOEX D1 close** — at the
`daily_run_time` bound to `close_definition` ([config-and-secrets.md](../contracts/config-and-secrets.md) §2.9,
§3.1). A sleeping/hibernating host misses that fire, starving the no-lookahead daily cycle and the ≥30-day clock.

| Rule | Requirement | TZ |
| --- | --- | --- |
| R1.1 | The host must **not sleep, hibernate, or suspend** while the bot is expected to run (the daily window around `daily_run_time`, plus continuous monitoring of any open position for protective exits). | §17 |
| R1.2 | Display sleep / screen lock is **allowed**; only **system** sleep/hibernate must be prevented. | §17 |
| R1.3 | The mechanism must keep the process scheduled even when no user is logged in interactively (the host runs headless for ≥30 days). | §17 |
| R1.4 | Sleep-prevention is a **host/OS configuration**, never bot code that touches orders. Document the chosen mechanism; do not implement an OS power API call inside a trading module. | §17 |

**Sleep-prevention mechanism on Windows is [owner-pending]** — candidates (documented, none locked): OS power plan
set to never-sleep (`powercfg`), a thread-execution-state keep-awake helper, or running the bot as a service/
scheduled task configured to wake the machine. The contract requirement is **"the host does not suspend during run
windows"**; the concrete mechanism is chosen at M6a setup, not asserted here.

> **No-lookahead coupling [LAW]:** sleep prevention exists to ensure the run fires **at or after** the final close,
> never before it. It must **not** cause an earlier fire. The `daily_run_time ≥ final-close` startup gate
> ([config-and-secrets.md](../contracts/config-and-secrets.md) §3.1) remains the authority; R1 only keeps the host
> awake to honor it.

## 4. R2 — Process supervisor / watchdog with auto-restart (TZ §17, §8)

The bot must survive crashes and host reboots over a ≥30-day unattended window, **but a restart must never
silently re-enter trading or fire a stale action.** Restart safety is governed by the persisted state machine.

| Rule | Requirement | TZ |
| --- | --- | --- |
| R2.1 | A supervisor/watchdog auto-restarts the bot process on crash and on host reboot (start-on-boot). | §17 |
| R2.2 | On every (re)start the bot runs **startup reconciliation before any trading action** — sync positions + orders, retry 3× (60/180/300 s), require 2 consecutive clean checks; persistent mismatch → `control_state.mode = 'blocked_reconciliation_mismatch'`. | §8 |
| R2.3 | The supervisor restarts the **process**; it must **never** itself confirm a proposal, place/cancel an order, or change `control_state.mode`. Restarting is mechanical; trading stays governed by the risk engine + reconciliation. | §7, §8 |
| R2.4 | **`kill` / `pause` survive restart [LAW].** On startup the bot reads the singleton `control_state` row (`db-schema` §3.3): `killed` and `blocked_reconciliation_mismatch` and `paused` are honored — a restart does **not** silently flip to `running`. `resume` requires extra confirmation + preflight (never automatic). | §7, §8 |
| R2.5 | **No stale button fires across a restart [LAW].** A `proposals` row created before a restart is re-evaluated against its wall-clock `ttl_ms` and **expired on resume** (`state = 'expired'`); the post-restart reconciliation gate forbids profit/target exits until clean. | §8 |
| R2.6 | Restart must not double-submit: idempotency is by `orders.order_id` (the client key + PK); a retry/restart cannot create a duplicate order. | §8 |
| R2.7 | A **restart loop** (repeated crash) must be visibly surfaced (alert per §7 / TZ §10), not hidden by an infinite silent respawn. Bounded restart with backoff; escalate after a threshold. | §10, §17 |

**Supervisor mechanism on Windows is [owner-pending]** — candidates (documented, none locked): a Windows service
wrapper, Task Scheduler with restart-on-failure + start-at-boot, or a small parent-watchdog process. The
requirement is R2.1–R2.7; the concrete supervisor is chosen at M6a setup.

> The frozen kill semantics hold across restart: **`kill` stops the bot and cancels active orders only — it never
> sells positions** [LAW]. A supervisor auto-restart after a `kill` must come up in the persisted `killed` mode and
> **not** trade; it does not "undo" the kill.

## 5. R3 — NTP / system time sync (TZ §17)

All timestamps are **INTEGER epoch milliseconds UTC** ([db-schema.md](../contracts/db-schema.md) §1). A drifting
host clock corrupts (a) which D1 close is "final" relative to `daily_run_time` (no-lookahead), (b) wall-clock
proposal/order `ttl_ms` expiry, and (c) the audit-trail ordering.

| Rule | Requirement | TZ |
| --- | --- | --- |
| R3.1 | The host clock must be kept synchronized to a reliable NTP source (Windows Time service / `w32tm` against a trusted server). Document the source and the sync cadence. | §17 |
| R3.2 | Time is stored/compared in **UTC epoch-ms**; the `timezone` config (`Europe/Moscow`, [config-and-secrets.md](../contracts/config-and-secrets.md) §2.1) is **display/scheduling only**, never the storage tz. | §5.1, §17 |
| R3.3 | At startup the bot should log the host clock vs an NTP/broker-time reference and the offset; if offset exceeds a documented **[owner-pending]** threshold, surface an alert (TZ §10) — a large skew near the close boundary risks acting on a not-yet-final bar. | §10, §17 |
| R3.4 | Clock correctness is an **OS/host responsibility**; the bot reads time and asserts sanity but does **not** set the system clock. | §17 |

> **No-lookahead coupling [LAW]:** the `daily_run_time ≥ final-close` gate
> ([config-and-secrets.md](../contracts/config-and-secrets.md) §3.1) is evaluated against the host clock. A skewed
> clock could make a pre-close run look post-close. R3 keeps the clock honest so that gate stays meaningful.

## 6. R4 — SQLite backups (TZ §17, §5.1)

`db_path` ([config-and-secrets.md](../contracts/config-and-secrets.md) §2.1, default `./stonksbot.db`) holds the
entire audit trail, state machine, and the ≥30-day paper journal. Losing it loses the pre-live evidence.

| Rule | Requirement | TZ |
| --- | --- | --- |
| R4.1 | The SQLite DB must be backed up on a documented cadence (at minimum: daily after the run completes, plus before any schema migration — §8). | §17 |
| R4.2 | Backups use a **consistent-snapshot** method (SQLite Online Backup API / `VACUUM INTO` / `.backup`), **never** a naive file copy of a live DB (risks a torn/partial file). | §17 |
| R4.3 | Backups are **integrity-checked** (`PRAGMA integrity_check`) and a **restore is rehearsed** at least once before the live gate — an untested backup is not a backup. | §14, §17 |
| R4.4 | Retain enough history to cover the full ≥30-day paper window plus margin; document retention. Backups inherit the same secret hygiene — **a DB backup must never contain a token** (tokens are never stored in the DB; see §7 / [config-and-secrets.md](../contracts/config-and-secrets.md) §1) [LAW]. | §14, §16 |
| R4.5 | The append-only `audit_journal` (with its UPDATE/DELETE-blocking triggers, `db-schema` §3.3) must survive a backup/restore cycle with its triggers intact — restore re-creates the schema, not just the rows. | §5.1, §17 |
| R4.6 | **Backup location [owner-pending]** (local second drive vs encrypted external/off-host) — decided at M6a; if off-host, it inherits the same no-token / no-secret rule [LAW]. | §17 |

## 7. R5 — Structured logs (TZ §16, §17)

Logging is `structlog` / standard logging emitting a **machine-readable** trail (TZ §4). Logs are an operational
mirror of the DB `audit_journal`, not a place secrets may leak.

| Rule | Requirement | TZ |
| --- | --- | --- |
| R5.1 | Logs are **structured** (one JSON record per event) so the daily/weekly status and post-mortem are machine-parseable, matching the `audit_journal` event vocabulary (`signal_selected`, `proposal_created`, `confirm_received`, `order_submitted`, `fill`, `position_opened`, `exit`, `pause`, `resume`, `kill`, `reconciliation`, … — `db-schema` §3.3). | §12, §17 |
| R5.2 | **No secret ever appears in a log [LAW].** Tokens are never logged; log token **presence** as a boolean `token_loaded=true`, never the value ([config-and-secrets.md](../contracts/config-and-secrets.md) §1). **No request-header logging** (TZ §16). | §16 |
| R5.3 | Redact `account_id` in user-facing/log output where it is not needed (`secrets-token-policy`); the guarded account is shown by **name + id on startup** for human verification (account guard, [config-and-secrets.md](../contracts/config-and-secrets.md) §3) and otherwise minimized. | §16 |
| R5.4 | Log rotation/retention is configured so a ≥30-day unattended run does not exhaust disk; rotated logs keep the same redaction guarantees. | §17 |
| R5.5 | Logs record reliability events (restart, reconciliation result, clock-skew alert, backup success/failure, missed run) so host-reliability itself is auditable. | §17 |

## 8. R6 — Schema versioning & migration discipline (TZ §5.1)

The DDL contract ([db-schema.md](../contracts/db-schema.md)) is implemented verbatim; once paper data accrues, the
schema must evolve **without losing or silently rewriting** the evidence.

| Rule | Requirement | TZ |
| --- | --- | --- |
| R6.1 | The DB carries an explicit **schema version** (e.g. `PRAGMA user_version` or a `schema_migrations` record); the bot **refuses to start** if the DB schema version is newer/older than the code expects (hard-fail, not silent auto-upgrade). | §5.1 |
| R6.2 | Migrations are **forward-only, ordered, and recorded**; each migration runs inside a transaction and is preceded by an R4 backup (§6). | §5.1, §17 |
| R6.3 | **Data provenance is never silently overwritten [LAW: data truth]:** a new data load is a **new `source_version`**, not an in-place update (`candles` PK includes `source_version`; `db-schema` §1, §3). Migrations preserve this. | §5.1 |
| R6.4 | The frozen **enum CHECK vocabularies** (`db-schema` §2) are part of the schema; a migration may not drop or rename an enum value without an owner decision + the same-change rule (a divergent enum silently weakens an invariant). | §5.1 |
| R6.5 | The append-only `audit_journal` triggers are re-asserted by every migration that touches the schema (they must never be dropped to "make a migration easier"). | §5.1 |
| R6.6 | **`db_switch_point` (SQLite→Postgres) is documented, not automatic** ([config-and-secrets.md](../contracts/config-and-secrets.md) §2.9); the Windows-local host stays SQLite for the whole paper window. The Postgres-compat type mapping (INTEGER→BIGINT, INTEGER PK→identity, TEXT-JSON→JSONB; `db-schema` §1) is honored at the VPS migration (§9), not here. | §5.1, §17 |

## 9. R7 — VPS migration seams (TZ §17) — documented, not built here

This Windows-local contract names the explicit switch points so nothing is silently introduced. The VPS work is a
**separate M6a contract** and the `deployment`/VPS profile stays **dormant** until activated (`component-guardian`).

| Seam | Local (this contract) | VPS (separate, deferred) | TZ |
| --- | --- | --- | --- |
| Process supervision | Windows supervisor/watchdog **[owner-pending]** (§4) | systemd / Docker Compose restart policy | §17 |
| Database | SQLite at `db_path` | Postgres (DSN at `db_path`) at `db_switch_point` | §5.1, §17 |
| Secrets | env / `.env` (git-ignored); store **[owner-pending]** (§10) | VPS secret store / env | §16 |
| Dashboard | `127.0.0.1` bind, `DASHBOARD_AUTH_TOKEN` (never public) [LAW] | via SSH tunnel / VPN, never public [LAW] | §11, §16 |
| Backups | local SQLite snapshot (§6) | DB + log backups + DR runbook | §17 |
| Time sync | Windows Time / `w32tm` (§5) | NTP/chrony | §17 |

> **Parallelism (TZ §3, §17):** VPS provisioning (M6a) may proceed **in parallel** with the local paper window;
> the live gate (M6b) is the pre-live-gate check + owner approval only. Nothing in this local contract authorizes
> activating the VPS toolchain — that needs an explicit owner request flipping the profile to `active`.

## 10. Secret handling on the Windows host (TZ §16) [LAW: token policy]

Reliability ops must not become a secret-leak path. The token policy is frozen; only the **storage mechanism** on
Windows is open.

| Rule | Requirement | TZ |
| --- | --- | --- |
| S1 | Secrets are **env-only**, loaded from environment / `.env` (git-ignored) per [config-and-secrets.md](../contracts/config-and-secrets.md) §1; **never** in code, committed config, logs, dashboard, or Telegram [LAW]. | §16 |
| S2 | **Separate token per mode** (`TINVEST_TOKEN_SANDBOX`, `TINVEST_TOKEN_LIVE_CONFIRM`; `TELEGRAM_BOT_TOKEN`, `DASHBOARD_AUTH_TOKEN`); only the active mode's token is required at startup. On this host the active mode is `paper`/`sandbox` — the live-confirm token need not be present locally. `TINVEST_TOKEN_LIVE_AUTO_SMALL` stays absent (auto_small DISABLED) [LAW]. | §16 |
| S3 | **Startup scope check BLOCKS trading** (refuse to start), never warns, if the active token is missing, wrong-mode, read-only for `confirm`, or over-broad when account-scoping is available/required. If account-scoping is verified unavailable and owner-recorded, the guard-only full-access fallback relies on the `account_id` guard [LAW]. | §16 |
| S4 | Backups (§6) and logs (§7) must contain **no token** — tokens are never persisted to the DB or logs, so a snapshot/rotation can never carry one [LAW]. | §16 |
| S5 | Token lifetime is **3-months-from-last-use (rolling)**; a long idle paper window can let a token lapse — keep tokens warm or plan rotation (operational note, not config). Rotation/revoke is owner-driven; the broker does not store tokens for you. | §16 |

**Secret-storage mechanism on Windows is [owner-pending] — decision 4** (`.env` vs DPAPI / Windows Credential
Manager vs an OS keyring). The **contract surface is fixed**: secrets are read as environment variables / from
`.env` (git-ignored), per [config-and-secrets.md](../contracts/config-and-secrets.md) §1 and §6 ("Secret-storage
backend … is [owner-pending], decided before live (M6)"). This ops spec **does not assert** a concrete store; it
records that whatever store is chosen must (a) deliver the secrets to the process as the same env keys, (b) never
land a secret in git/logs/dashboard/Telegram, and (c) survive the chosen supervisor's restart (§4) without
re-prompting interactively (the host runs unattended).

## 11. Frozen invariants honored

- **≥30-day paper window runs here first; no live without pre-live gates [LAW]** — §1: only `paper`/`sandbox` on
  this host; live `confirm` needs the TZ §14 gates + manual owner approval (and is intended for the VPS).
- **No-lookahead [LAW]** — §3/§5: sleep-prevention and time-sync keep the run firing **at/after** the final close,
  never before; the `daily_run_time ≥ final-close` startup gate stays the authority; clock is UTC epoch-ms.
- **State machine survives restart; `kill`/`pause` persist; `kill` never sells [LAW]** — §4 (R2.4, R2.5): the
  supervisor restarts the process only; `control_state` mode and the post-restart reconciliation gate are honored;
  stale proposals expire; idempotency by `orders.order_id`.
- **Account guard [LAW]** — §7/§10: `account_id` shown name+id at startup, otherwise redacted; orders/cash/audit
  carry the guarded account (`db-schema` §4).
- **Token policy: secrets never in code/config/logs/dashboard/Telegram [LAW]** — §7 (R5.2/R5.3) + §10 (S1–S5):
  logs/backups carry no token; startup scope check blocks trading.
- **Data truth / no silent overwrite [LAW]** — §8 (R6.3): a new load is a new `source_version`; migrations preserve
  provenance; frozen enum CHECKs are not silently dropped.
- **Dashboard never public [LAW]** — §9: `127.0.0.1` + `DASHBOARD_AUTH_TOKEN` locally; SSH tunnel/VPN on VPS.
- **Sandbox ≠ proof [LAW]** — §1: the ≥30-day window here is an execution/operability check, not evidence of edge;
  fill-model parity is required at the gate (TZ §14).
- **Dormant profile / no premature toolchain [LAW: component profiles]** — §1/§9: doc only; no build step or
  dependency introduced; VPS profile stays dormant until owner-activated.

## 12. Open questions / owner-pending

- **Secret-storage mechanism on Windows (decision 4) [owner-pending]** — `.env` vs DPAPI / Windows Credential
  Manager vs OS keyring. Contract surface (env keys, no-leak, unattended restart) is fixed (§10); the concrete
  store is decided before live (M6), mirroring [config-and-secrets.md](../contracts/config-and-secrets.md) §6.
- **Sleep-prevention mechanism [owner-pending]** — power-plan vs keep-awake helper vs wake-scheduled service (§3).
- **Process-supervisor mechanism [owner-pending]** — Windows service vs Task Scheduler restart vs parent-watchdog,
  including restart-loop backoff threshold (§4).
- **Clock-skew alert threshold [owner-pending]** — the max host-vs-NTP/broker offset that triggers an alert /
  blocks acting near the close boundary (§5, R3.3).
- **Backup cadence, retention, and location [owner-pending]** — exact cadence beyond "daily + pre-migration", how
  long to retain, and local vs off-host (encrypted) destination (§6).
- **Log retention / rotation policy [owner-pending]** — size/age limits for the ≥30-day unattended run (§7, R5.4).
- **Holiday/short-session handling for `daily_run_time=19:05` / `close_definition=auction_close` [verify]** —
  the close/run pair is owner-ratified in [config-and-secrets.md](../contracts/config-and-secrets.md) §2.9; this
  spec requires the host to keep the scheduled fire honest and to avoid firing before a shortened-session close
  is actually available.
- **db_switch_point (SQLite→Postgres) [verify/owner]** — documented switch at VPS/M6 (§8, R6.6); the local host
  stays SQLite for the whole window.
- **VPS host details [owner-pending, deferred]** — provider/OS, systemd vs Docker Compose, firewall, DR + secret-
  rotation runbook are a separate M6a contract (§9); not decided here.

## 13. Cross-references

- Spec: `docs/TZ.md` §2 (owner decisions: Local-Windows-first), §17 (deployment local→VPS), §14/§19 (pre-live gates,
  open items), §16 (security), §5.1 (schema), §10 (alerts).
- Frozen LAW: `docs/frozen-decisions.md` (phased path, no-lookahead, state machine + kill/pause, account guard,
  token policy, data truth, dashboard never public, sandbox ≠ proof).
- Contracts: [config-and-secrets.md](../contracts/config-and-secrets.md) (`db_path`, `daily_run_time`,
  `close_definition`, `dashboard_bind`, secret keys, account guard, `db_switch_point`),
  [db-schema.md](../contracts/db-schema.md) (epoch-ms UTC, `control_state`, `audit_journal`, `source_version`,
  enum vocabularies), [tax-and-dividends.md](../contracts/tax-and-dividends.md) (journal/PnL the backups preserve).
- Skills: `secrets-token-policy`, `state-machine-discipline`, `risk-policy-guardian`. Profiles: `component-guardian`
  (deployment/VPS stays dormant until activated).
