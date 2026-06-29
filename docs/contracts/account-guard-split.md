# Contract — `account_id` guard: M0 vs M4 responsibility split (TZ §4.1 / §7 / §9)

> **Status:** M0 design note, **resolved on paper (no code yet)**. This pins **which half of the account guard
> is buildable at M0 and which half realistically needs the live broker adapter (M4)**, so the M0 `config/`
> module is **not falsely claimed complete** while the live-account-list path is still a stub.
> **`docs/frozen-decisions.md` 🔒 wins** — every rule marked **[LAW]** mirrors a frozen invariant and may not be
> weakened here (only via owner decision + ADR + same-change rule). **[owner-pending]** = a value/decision the
> owner must confirm before lock; **[verify]** = empirical, depends on the live API/account (M4).
>
> **Verifier note (gate=none, no toolchain):** this is a design note, not executable code. Its purpose is the
> split itself — the **pure-validation** half (presence, exact-match against the configured id, refuse-start) is
> M0; the **live `GetAccounts` round-trip** (enumerate broker accounts, resolve name, exact-match the configured
> id) requires the broker adapter and is **M4**. The M0 guard must therefore be shipped as *partial-but-honest*,
> never as "guard complete". Anchors the same five-step guard already pinned in
> [config-and-secrets.md](config-and-secrets.md) §3 / §3.1.

This contract feeds: the M0 `config/` loader, the M4 `broker/` adapter (`GetAccounts`), the M4 `risk/` engine
pre-checks (TZ §7 (risk engine, pre-checks / item 1)), reconciliation (TZ §8), and the row-level account scoping in [db-schema.md](db-schema.md)
(`orders.account_id`, `positions.account_id`, `cash_events.account_id`, `audit_journal.account_id`).

---

## 1. The frozen guard, restated faithfully (decomposed from the two Account & access rows) [LAW]

From `docs/frozen-decisions.md` (Account & access) — the **complete** five obligations the guard must satisfy.
This contract does **not** add, drop, or soften any of them; it only assigns each to a milestone.

```text
G1  Require an explicit account_id — refuse to start in {sandbox, confirm} (any live/trading mode) if missing/blank.
G2  Refuse to start if multiple broker accounts exist and none EXACTLY matches the configured account_id
    (no "pick the first" / no fuzzy fallback).
G3  Show the account NAME + id on startup (log + dashboard status) for human verification.
G4  Require manual confirmation on any account_id CHANGE between runs.
G5  Trade ONLY the one configured account_id; migrating to the main account needs manual approval.
```

> `paper` mode opens **no real orders** and needs **no broker account** (config §3): G1/G2 do not block startup
> in `paper`, but the configured `account_id` (if any) is still surfaced (G3) and a change still prompts (G4).
> For read-only market data in `paper`, reuse a read-only/sandbox-scoped token — never require a trade-scoped
> token to read candles ([config-and-secrets.md](config-and-secrets.md) §3).

## 2. The split — pure validation (M0) vs live round-trip (M4)

The single ordered enum below is the guard's stage vocabulary. **The names are the contract** (reuse verbatim;
never rename). Each stage states the milestone that can honestly satisfy it.

```text
guard_stage (ordered; each must pass before the next):
  S1_present          -> account_id is set & non-blank when mode ∈ {sandbox, confirm}   [M0  pure]
  S2_change_confirmed -> account_id unchanged since last run, OR change manually OK'd    [M0  pure]
  S3_enumerated       -> broker GetAccounts returned the live account set                [M4  live]
  S4_exact_match      -> exactly one live account.id == config.account_id (no fallback)  [M4  live]
  S5_named_shown      -> account NAME resolved from the live row + shown (log+dashboard)  [M4  live]*
  S6_scope_ok         -> active-mode token scope covers exactly this account (or guarded) [M4  live; ties to token-scope check]
```

\* **S5 caveat:** at **M0** only the *configured id* can be echoed (no live name exists yet) — G3's **name** half
is unmet until S3. The M0 startup banner shows `account_id=<id>  account_name=<unresolved — pending GetAccounts (M4)>`
so the gap is **visible**, never papered over.

### 2.1 Mapping each frozen obligation to a stage + milestone

| Frozen obligation | Stage(s) | M0 (pure validation) | M4 (live `GetAccounts`) |
| --- | --- | --- | --- |
| **G1** require explicit `account_id` | `S1_present` | ✅ **complete** — config presence check, hard-fail | — |
| **G2** refuse on multiple accounts / no exact match | `S3_enumerated`, `S4_exact_match` | ⚠️ **stub** — no live list to compare against | ✅ **completes** — enumerate, exact-match, refuse-start |
| **G3** show account **name** + id | `S5_named_shown` | ⚠️ **partial** — id only; name `unresolved` | ✅ **completes** — name from the live row |
| **G4** manual confirm on `account_id` change | `S2_change_confirmed` | ✅ **complete** — compare configured id vs last-run id (persisted) | — (still applies; unchanged) |
| **G5** trade only the configured account | row-level assert (§4) | ✅ **shape pinned** — DDL scopes every order/cash/audit row | ✅ **enforced** — assert `==config.account_id` at submit + reconcile |

> **Honest-completeness rule [LAW: faithful reporting]:** because **G2** and the **name** half of **G3** depend on
> `S3`/`S4`/`S5` (live broker data), the **M0 account guard is `partial`, not `complete`.** M0 ships the
> pure-validation stages and an **explicit stub** for the live stages that **refuses to start** in
> `{sandbox, confirm}` rather than silently passing (§3). Claiming "account guard done" at M0 would be a silent
> frozen-change. The guard reaches `complete` only when M4 wires `S3–S6`.

## 3. M0 behaviour of the unwired live stages (fail-closed) [LAW: account guard]

The M0 loader/guard **must not** treat the absent live-list path as "OK". For `mode ∈ {sandbox, confirm}`:

1. Run S1 (`S1_present`) and S2 (`S2_change_confirmed`) — real, blocking checks (exit non-zero on failure).
2. The live stages `S3–S6` are **not implemented at M0**. The guard therefore returns a distinct
   **`guard_status` = `account_guard_stub_blocked`** and **refuses to start** any `{sandbox, confirm}` mode
   (exit non-zero), logging: *"live account verification (GetAccounts) requires the M4 broker adapter; refusing
   to start in <mode> until S3–S6 are wired."* This is the only safe degradation: **fail-closed**, never
   fail-open. (`paper` mode is unaffected — it needs no broker account.)
3. M0 startup banner shows `account_id` (G3 id half) + `account_name=<unresolved — pending GetAccounts (M4)>`.

```text
guard_status (returned by the guard; reuse verbatim):
  ok                            -> all applicable stages passed (only reachable at M4 for sandbox/confirm)
  refused_missing_account_id    -> S1 failed (G1)                        [M0-reachable]
  refused_account_changed       -> S2 failed, no manual confirm (G4)     [M0-reachable]
  account_guard_stub_blocked    -> S3–S6 unwired; sandbox/confirm blocked [M0-only, removed at M4]
  refused_no_exact_match        -> S4 failed: 0 or >1 live matches (G2)  [M4-reachable]
  refused_scope_mismatch        -> S6 failed: token scope wrong (TZ §9)  [M4-reachable]
  paper_no_account              -> paper mode; broker account not required
```

> `account_guard_stub_blocked` is a **temporary, M0-only** status. M4 deletes it (the live path now exists);
> any code path that could still emit it after M4 is a bug. Removing it is part of the M4 change, not optional.

## 4. M4 live round-trip — the realistic dependency [verify, empirical at M4]

> **M4 builds, M6 gates (no contradiction).** TZ §3 lists "account guard" under **M6** as a pre-live gate; this
> is consistent with placing `S3–S6` at **M4**. The `GetAccounts` round-trip + `S4/S5/S6` wiring lands with the
> **M4** broker adapter (TZ §9) and risk-engine pre-check (TZ §7 (risk engine, pre-checks / item 1)); TZ §3's M6
> "account guard" line is the **final pre-live gate that RE-asserts the already-wired guard** — M4 builds it, M6
> gates on it.

S3–S6 need the broker adapter (TZ §9), which only exists from **M4**:

1. **`GetAccounts`** (T-Invest `UsersService` / SDK `get_accounts`) — enumerate the token's accounts (id + name +
   type + status). This is a **live API round-trip**; it cannot be unit-tested into existence at M0 — it needs a
   token and the real/sandbox account, hence the M4 placement. *(Exact SDK method name [verify] against
   `t-tech-investments`; the package + GitLab index are resolved per [config-and-secrets.md](config-and-secrets.md)
   §6a / ADR-0005.)*
2. **S4 exact-match:** filter the live set to `account.id == config.account_id`. **Refuse to start** if the count
   is `0` or `> 1` matches, or if `> 1` account exists and none matches (G2 — **no "pick the first" fallback**).
3. **S5 name:** take the matched row's display name → log + dashboard banner (G3 name half).
4. **S6 token scope** (couples to the frozen token-scope check, TZ §9 / [config-and-secrets.md](config-and-secrets.md) §1):
   the active-mode token must be present and mode-correct. **Account-scoped tokens are NOT available for
   Инвесткопилка / Счёт под ключ / Смарт-счёт** [verify] — so whether the bot account's **product type**
   supports account-scoping is **empirical, confirmable only with the live account at M4**. **[owner-pending /
   verify — decision 3]:** S6 blocks missing/read-only/wrong-mode scopes, but does not block solely because the
   live token is full-access when account-scoping is verified unavailable. This fallback is allowed only after
   the product type is verified and the owner records the guard-only posture; the `account_id` guard (S4) is then
   load-bearing and must pass before trading.

> **Reconciliation tie-in (TZ §8):** on every startup/restart **and** periodic reconcile, re-assert S4 (the live
> matched account is still the configured one) and the row-level invariant (§5). A drift here → reconciliation
> `mismatch`/`blocked`, not a silent adoption of a different account.

## 5. Row-level scoping (G5) — pinned at M0, enforced at M4 [LAW]

G5 ("trade only the configured account") has a **schema half** (M0) and an **assertion half** (M4), consistent with
[db-schema.md](db-schema.md) §4 *Account guard (row-level)* — reuse the same column names verbatim:

- **M0 (shape):** `orders.account_id`, `positions.account_id`, `cash_events.account_id` are `NOT NULL`;
  `audit_journal.account_id` carries the guarded account (NULL only for global pause/kill events). The DDL makes
  every order/cash/audit row provably account-scoped.
- **M4 (assertion):** the engine asserts `row.account_id == config.account_id` **at order submit AND at
  reconciliation**. A row for any other account is a hard error (reconciliation `blocked`), never adopted.

This split means the *storage* of G5 is locked at M0 (irreversible DDL choice) while its *runtime enforcement*
arrives with the engine at M4 — neither milestone may claim G5 "done" alone.

## 6. Config keys touched (no new keys; reuse verbatim)

This contract introduces **no new config keys**. It governs the use of keys already pinned in
[config-and-secrets.md](config-and-secrets.md) §2.1 / §3:

| Key | Source | Role here |
| --- | --- | --- |
| `account_id` | config §2.1 (string; required for sandbox/confirm) [LAW] | subject of S1/S4; the one dedicated bot account |
| `mode` | config §2.1 (enum `paper`\|`sandbox`\|`confirm`) [LAW] | gates whether S1–S6 block startup (`paper` exempt from S1/S3–S6) |

The **last-run account_id** needed by S2/G4 is **operational state**, not a config key: persist it in the DB
singleton `guard_state` row (owner decision 2026-06-29), not in a sidecar file. Do **not** introduce a config key.

## 7. Frozen invariants honored

- **Dedicated bot account; trade only the one configured `account_id`** [LAW] — G5, S4, row-level §5; no
  multi-account, no "first account" fallback.
- **Refuse to start in trading/confirm/live if `account_id` missing or no exact match among multiple accounts**
  [LAW] — S1 (M0), S4 (M4); fail-closed stub at M0 (§3).
- **Show account name + id on startup** [LAW] — G3: id at M0, name at M4 (S5); the M0 gap is shown, not hidden.
- **Manual confirm on account change** [LAW] — G4 / S2 (M0).
- **Token policy / startup scope check BLOCKS trading** [LAW] — S6 couples to the §1 scope check; never warns.
- **Secrets never logged** [LAW] — the startup banner shows `account_id` (a non-secret config value) and the
  account name; it **never** echoes a token (log `token_loaded=true` only, per config §1).
- **Faithful reporting / no silent frozen-change** [LAW] — M0 guard is reported `partial`, not `complete`; the
  live path is an explicit fail-closed stub, not a silent pass.
- **State machine + reconciliation** [LAW] — S4 + row-level assert re-run on startup and periodic reconcile (§4).

## 8. Open questions / owner-pending

- **[owner-pending / verify — decision 3]** Bot-account **product type** → whether **account-scoped tokens** are
  feasible (unavailable for Инвесткопилка / Счёт под ключ / Смарт-счёт). **Empirical, confirmable only with the
  live account at M4.** If infeasible, S6 permits a full-access live token only after the owner records the
  guard-only posture; S6 still blocks missing/read-only/wrong-mode scopes, and the `account_id` guard (S4) is
  load-bearing. *(Mirrors [config-and-secrets.md](config-and-secrets.md) §6 last bullet — do not assert a value.)*
- **[verify]** Exact SDK method/shape for `GetAccounts` (id, name, type, status fields) against the pinned
  `t-tech-investments` SDK — confirm at M4 integration.
- **[owner-pending]** Exact wording / channel of the **manual change-confirmation** prompt (G4) — Telegram vs
  startup CLI confirm — lands with M5 control plane; the *requirement* (block until confirmed) is frozen now.
- **Note:** `account_guard_stub_blocked` is M0-only and **must be removed** when M4 wires S3–S6 (same-change rule);
  a lingering emit after M4 is a bug.

## 9. Cross-references

- Frozen LAW: `docs/frozen-decisions.md` (Account & access; token policy; state machine).
- Config: [config-and-secrets.md](config-and-secrets.md) §1 (token scope), §2.1 (`account_id`/`mode`), §3 + §3.1
  (the five-step guard + hard-fail config-load validation).
- Schema: [db-schema.md](db-schema.md) §3.3 / §4 (`guard_state` change detection plus row-level account guard;
  `orders`/`positions`/`cash_events`/`audit_journal`).
- Spec: `docs/TZ.md` §4.1 (config/secrets), §7.1 (risk-engine pre-checks: account guard), §8 (reconciliation),
  §9 (broker adapter: tokens / account-scoping / product types), §15 (live confirm; dedicated account).
- Skills: `risk-policy-guardian`, `secrets-token-policy`. Auditor: `risk-invariant-auditor`.
