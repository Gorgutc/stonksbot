# Contract — Startup reconciliation & adoption of external state (TZ §8, §10)

> **Status:** M0 contract, **resolved on paper (no code yet)**. This pins the **build-ready** reconciliation
> procedure the M4 `execution/` + `risk/` + `broker/` layers implement **verbatim**. **`docs/frozen-decisions.md` 🔒
> wins** on any conflict — values marked **[LAW]** mirror a frozen invariant and may not be changed here (only via
> owner decision + ADR + same-change rule). Reconciliation is the **safety gate before any trading**: a divergent
> rule here silently weakens the startup-sync / adopt-don't-error / kill-never-sells invariants.
> **[owner-pending]** = a value the owner must confirm before it is locked (do not silently fix it).
> **[verify]** = depends on a `docs/contracts/` / TZ §20 fact still being confirmed.
>
> This contract **expands** [state-machine.md](state-machine.md) §10 (the terse reconciliation rows in the FSM)
> into the full procedure; the two MUST agree. It reuses the frozen enums from [db-schema.md](db-schema.md) §2 and
> the config keys from [config-and-secrets.md](config-and-secrets.md) §2 exactly — same names, no synonyms. Every
> step below is an executable-test target (pure, dependency-injected — TZ §18). Pairs with
> [config-and-secrets.md](config-and-secrets.md), [db-schema.md](db-schema.md), [tax-and-dividends.md](tax-and-dividends.md).

---

## 1. Scope & vocabulary (reused verbatim — never rename)

Reconciliation reads the **broker truth** (positions + active orders + cash for the guarded account) and compares
it to **local state**, **before any trading action**, on startup/restart and periodically. External/manual changes
are **adopted**, never treated as errors [LAW]. It writes `reconciliations` rows, may move `control_state.mode`, and
may adopt/close `positions` or mark `orders` for resolution. It reuses these frozen enums ([db-schema.md](db-schema.md) §2):

```text
reconciliations.result : clean | mismatch | blocked            (db-schema §2)
reconciliations.kind   : startup | periodic | post_restart      (db-schema §3.3 — free TEXT; freeze [verify], §12)
control_state.mode     : running | paused | killed | blocked_reconciliation_mismatch  (db-schema §2)
orders.state           : submitted | partially_filled | filled
                       | cancel_requested | cancelled | reconcile_required             (db-schema §2)
positions.source       : bot | manual_adopted | managed_only
positions.state        : open | closed
positions.close_reason : risk | trend | target_trailing | time | manual               (db-schema §2)
proposals.state        : awaiting_confirmation | confirmed | rejected | expired        (db-schema §2)
cash_events.type       : deposit | withdrawal | commission | tax | dividend            (db-schema §2)
```

**Identity / keys** ([db-schema.md](db-schema.md) §3):
- **Match on `orders.order_id`** (client idempotency key, table PK) first; then `instrument_uid`; then
  Quotation-exact (`*_units`/`*_nano`) price + integer qty. Re-running reconciliation must **never** double-adopt or
  double-submit — the `order_id` key guarantees no duplicate broker order [LAW: idempotency].
- **Account guard:** every broker read is scoped to `account_id == config.account_id`, asserted **at reconciliation**
  (not only at submit) [LAW] (TZ §7.1, config §3). A broker response for any other account is a hard error → abort
  the cycle, do not adopt.
- **Money equality** uses Quotation `units`/`nano` integer equality — never float ([db-schema.md](db-schema.md) §1) —
  so a "mismatch" is an exact integer difference, not a rounding artifact.

## 2. When reconciliation runs (TZ §8)

| `reconciliations.kind` | Trigger | Blocking? | TZ |
| --- | --- | --- | --- |
| `startup` | process start (cold) — runs **before any trading**, after `control_state` is read | **yes** — no entry/exit acts until it resolves per §3 | §8 |
| `post_restart` | process restart after a crash/redeploy with prior live state | **yes** — same gate as `startup` | §8 |
| `periodic` | steady-state interval while `running` | non-blocking sweep; a mismatch still moves mode per §5 | §8 |

**Ordering [LAW]:** on every `startup`/`post_restart`, `control_state` is read **first** (§3 step 1) and the full
sync completes (or blocks) **before** the engine is allowed to emit a single order — entries **or** exits. The
`periodic` kind runs inside an already-`running` engine and does not re-gate exits, but its mismatch outcome can flip
the mode (§5). **Periodic cadence is [owner-pending]** (§12) — TZ §8 fixes only the startup retry schedule, not a
steady-state interval; do not assume a value.

## 3. Startup / post-restart procedure (TZ §8) [LAW]

Run in this exact order on every `startup` / `post_restart`. Each step is a test row.

1. **Read `control_state` first** ([db-schema.md](db-schema.md) §3.3 singleton).
   - `mode = killed` → **stay killed**; cancel nothing further to sell; reconciliation may still *observe* (read &
     record) but never re-enables trading. `/resume` from `killed` requires a clean reconciliation first
     ([state-machine.md](state-machine.md) §7).
   - `mode = blocked_reconciliation_mismatch` → **keep the blocked policy** (§6) until cleared by §7.
   - `mode = paused` → entries stay blocked; monitoring + exits stay on; reconciliation proceeds.
   - `mode = running` → proceed.
2. **Re-evaluate every `awaiting_confirmation` proposal** against **wall-clock** TTL
   (`created_at + ttl_ms` vs **now**, `ttl_ms` from `config.button_ttl_minutes`): elapsed → `expired`
   ([state-machine.md](state-machine.md) §3) [LAW]. No stale confirm button can fire after a restart.
3. **Read broker truth** for `account_id == config.account_id` only: open positions, active orders, cash balance.
   Account mismatch → hard abort (§1), never adopt.
4. **Diff** broker truth vs local `positions` / `orders` / cash and classify each finding (§4).
5. **Retry on transient failure / unstable read:** retry up to **3 attempts** with backoff **60s / 180s / 300s**
   [LAW]. A clean diff (`reconciliations.result = clean`) requires **2 consecutive clean checks** before normal
   trading is allowed — a single clean read does not clear the gate [LAW].
6. **Resolve to a result:**
   - 2 consecutive `clean` → write `reconciliations.result = clean`; trading allowed (mode stays/returns to its
     pre-existing non-blocked value).
   - any persistent diff after retries → write `reconciliations.result = mismatch`; apply §5 → `control_state.mode =
     blocked_reconciliation_mismatch`.
   - a diff that **cannot be safely resolved** (e.g. ambiguous broker state, account anomaly, irreconcilable qty) →
     write `reconciliations.result = blocked`; remain not-trading; alert the owner; require manual resolution.
7. **Journal:** write an `audit_journal` row `event=reconciliation` (actor `system`) for each cycle; record the
   `mismatch` JSON detail on the `reconciliations` row ([db-schema.md](db-schema.md) §3.3).

**Retry/clean-check enum (frozen here, mirrored from TZ §8):**

```text
reconcile.max_attempts          = 3
reconcile.backoff_seconds       = [60, 180, 300]      # attempt 1→2→3 spacing
reconcile.required_clean_checks = 2                    # consecutive clean reads before trading
```

> These three values are **[LAW]** (TZ §8) — they are NOT config knobs the owner tunes; changing them is a frozen
> change (same-change rule). They live as named constants in the M4 reconciler, not in `config.yaml`.

## 4. Diff classification (per finding)

Each broker-vs-local difference is one of the following. **External/manual changes are adopted (§8), not errored** [LAW];
only the unresolvable cases escalate.

| Finding | Meaning | Outcome |
| --- | --- | --- |
| **match** | broker == local (order_id / uid / Quotation-exact price+qty all agree) | no action |
| **external position** | broker holds a long the local DB does not know | **adopt** per §8 (approved → manage; non-approved → §10 prompt) |
| **vanished position** | local `positions.state=open` but broker shows none / smaller qty | **manual sell adopted**: update qty/avg or close (`close_reason=manual`); reconcile linked orders (§8) |
| **cash delta** | broker cash ≠ local expectation, no matching fill/commission | **deposit/withdrawal adopted**: write `cash_events`; **recompute** capital + all limits (§8) |
| **order missing** | local non-terminal order has no broker counterpart | set that `orders.state = reconcile_required`; resolve via §3 |
| **order extra** | broker shows an order with no local row | set local to `reconcile_required` (or adopt its fill if matched by `order_id`); resolve via §3 |
| **order qty/state mismatch** | broker & local disagree on fill qty or state for the same `order_id` | set `orders.state = reconcile_required`; adopt the **broker** state as truth on resolution |
| **unsafe / ambiguous** | cannot determine true state safely (e.g. account anomaly, contradictory reads) | `reconciliations.result = blocked`; do not trade; alert owner |

**`reconcile_required` is a holding state** ([state-machine.md](state-machine.md) §5): the engine **freezes acting on
that order** (no submit, no cancel-as-action) until reconciliation adopts the true broker state (→ `filled` /
`cancelled`, or adopts the position).

## 5. Persistent mismatch → `blocked_reconciliation_mismatch` (TZ §8) [LAW]

A persistent diff (after the §3 retry schedule) writes `reconciliations.result = mismatch` and transitions
`control_state.mode` from `running`/`paused` → `blocked_reconciliation_mismatch`
([state-machine.md](state-machine.md) §7). This is a **fail-safe**, not a stop: the position keeps being protected.

CANONICAL `blocked_reconciliation_mismatch` exit policy — [state-machine.md](state-machine.md) §10 and this
file MUST state the **same policy content** (semantically identical; formatting may differ):

```text
blocked_reconciliation_mismatch policy [LAW]:
  - NEW entries .................. BLOCKED
  - monitoring / evaluation ..... ON  (the position FSM keeps running)
  - RISK exits ................... ALLOWED    # hard stop-loss (~4%), daily hard stop
  - PROFIT / target_trailing .... FORBIDDEN   # no take-profit while broker truth is unconfirmed
  - kill ........................ reachable, NEVER sells
  - pre-exit precondition ....... a broker-confirmed position AND no conflicting active order
  - notify ...................... owner notified BEFORE and AFTER every exit attempt
```

This is the frozen **BINARY** (TZ §8; frozen-decisions.md, "Order & risk rules" — risk exits / kill row):
while broker truth is unconfirmed, RISK exits (hard stop-loss ~4%, daily hard stop) are ALLOWED because they
only **reduce risk** (consistent with "exits are always allowed", frozen-decisions.md, "Order & risk rules" —
market-regime / risk-exits row); PROFIT / `target_trailing` exits are FORBIDDEN because taking profit against
an unverified position could sell phantom or wrong-qty shares. A risk exit takes precedence if multiple rules
fire the same cycle ([state-machine.md](state-machine.md) §6).

**Trend-break and time exits under block — [owner-pending]:** the frozen LAW classifies only the binary above
(RISK allowed / PROFIT forbidden); it does **not** classify `trend` or `time` exits for the blocked state.
Until an owner decision + ADR promotes either to allowed or forbidden, **neither is auto-fired while blocked**.
Do not assert a `trend`-allowed or `time`-forbidden classification as [LAW]. [state-machine.md](state-machine.md) §10
states the same classification.

**Exit safety under block [LAW]:** before any allowed (risk) exit while
`blocked_reconciliation_mismatch`, the reconciler must re-confirm (a) a broker-confirmed position of the held
`instrument_uid` and (b) **no conflicting active order** for that uid; and the owner is notified **before** the exit
attempt and **after** its outcome. Long-only still holds: a sell may never exceed broker-confirmed held qty
([state-machine.md](state-machine.md) §5) [LAW].

## 6. Behavior while already blocked (read on startup)

If §3 step 1 finds `mode = blocked_reconciliation_mismatch`, the engine boots **into** the §5 policy (entries
blocked, RISK exits allowed, PROFIT/target_trailing exits forbidden, kill never sells; trend/time not auto-fired
[owner-pending], §5) and keeps reconciling toward §7.
The blocked policy **survives restarts** because `control_state` is a persisted singleton
([db-schema.md](db-schema.md) §3.3) read before any action [LAW].

## 7. Exit from `blocked_reconciliation_mismatch` (TZ §8) [LAW]

The blocked state clears **only** when both hold:

1. **2 consecutive clean reconciliations** (`reconciliations.result = clean`, same threshold as §3) — the diff is
   gone and stable across two checks, **and**
2. **owner confirm** ([state-machine.md](state-machine.md) §7 — the same extra-confirmation discipline as `resume`).

On clearing → `control_state.mode = running`; `audit_journal` records the transition (actor
`owner:<telegram_user_id>`). A single clean read is insufficient; if the diff reappears between the two checks the
counter resets. **The bot never self-clears a block without the owner** — a silent auto-resume would defeat the gate.

## 8. Adoption rules — external/manual change is adopted, not errored (TZ §8, §10) [LAW]

The reconciler **adopts** every external/manual change below. Adoption is **idempotent**: matching on `order_id` /
`instrument_uid` / Quotation-exact price+qty means re-running reconciliation cannot double-adopt or double-submit.

| External change observed | Adoption [LAW] |
| --- | --- |
| Manual **buy** of an **`approved`** ticker (broker holds it, unknown locally) | adopt a `positions` row and **manage** under bot exit rules. `source = bot` **iff** it maps to a known local `order_id`; otherwise `source = manual_adopted`. Entry price = broker average; opened_at = broker operation date (fallback adoption date). |
| Manual **buy** of a ticker **outside `approved`** | **do NOT auto-manage** → emit the §10 Telegram manual-position prompt (three buttons): **«Сопровождать `managed_only`»** / **«Добавить в approved»** / **«Игнорировать»**. `managed_only` uses the **same exit rules** as bot entries; entry price = broker average; holding-period source = broker operation date (fallback adoption date). Default posture until the owner answers = **observe-only** (no auto exits) (TZ §10). |
| Manual **sell** / partial sell | update `positions`: reduce `qty`/recompute `avg`, or `state=closed` with `close_reason=manual` (full sell); reconcile linked `orders` rows. |
| **Deposit / withdrawal** | write a `cash_events` row (`type=deposit`\|`withdrawal`); **recompute** `risk.capital_rub`-derived capital + the **capital-derived** limits (max position 30%/3000 ₽, 50% cash reserve) — the 100 ₽ daily hard stop is a fixed config constant, not capital-derived (TZ §8; config §2.5). |
| Broker order **absent / extra / qty mismatch** vs local | set the affected `orders.state = reconcile_required`; resolve via §3; adopt the broker state as truth. |
| Dividend cashflow on a held position | write a `cash_events` row (`type=dividend`, **net** per [tax-and-dividends.md](tax-and-dividends.md) §6); does not by itself open/close a position. |

**Adoption never opens entry risk:** adopting an external long does **not** consume the ≤1 proposal/day budget and is
**not** gated by entry-only filters (regime, session, dividend-gap, daily hard stop) — those gate *new bot entries*,
not the recognition of a position that already exists. Adopted positions are then managed by the **same** exit rules
([state-machine.md](state-machine.md) §6), which is where protection (and any risk gating) applies.

**`managed_only` vs `manual_adopted` (frozen `positions.source` semantics):**
- `manual_adopted` = an `approved`-ticker long adopted automatically (no owner prompt needed) and managed by bot exits.
- `managed_only` = a **non-approved** ticker the owner explicitly chose to have the bot supervise via the §10 prompt;
  managed by the **same** exit rules but never promoted into the trading universe by the bot (frozen managed-registry:
  the bot may not add tickers, frozen-decisions.md, "Strategy, data & backtest honesty" (managed-registry row)).

## 9. Idempotency & no-lookahead at reconciliation [LAW]

- **No duplicate orders:** adoption and resolution key on `orders.order_id`; a retry/restart with the same key
  places no second broker order ([db-schema.md](db-schema.md) §4; [state-machine.md](state-machine.md) §8).
- **No double-adoption:** re-running reconciliation over the same broker truth yields the same local state (matching
  on `order_id` / `instrument_uid` / Quotation-exact price+qty is set-idempotent).
- **No-lookahead is untouched:** reconciliation observes *current* broker reality and adopts it; it never reads or
  acts on a future bar. It can adopt/close a position and recompute limits at any time, but it can **never open a new
  bot entry** (those follow the §3 gates of [state-machine.md](state-machine.md) — after the final D1 close, next
  session). Adoption ≠ entry.
- **Append-only audit:** every reconciliation cycle and adoption writes `audit_journal` rows
  ([db-schema.md](db-schema.md) §3.3 triggers block UPDATE/DELETE) so the broker↔local divergence and its resolution
  are reconstructable.

## 10. Reconciliation outcome → control-plane transitions (mirror of state-machine §7)

| From `control_state.mode` | Reconciliation event | To | Effect [LAW] |
| --- | --- | --- | --- |
| `running` / `paused` | persistent mismatch (§5) | `blocked_reconciliation_mismatch` | apply the §5 policy |
| `blocked_reconciliation_mismatch` | 2 consecutive clean checks **+ owner confirm** (§7) | `running` | exit the blocked state |
| `killed` | reconciliation runs (observe-only) | `killed` | never re-enables trading; `/resume` needs a clean reconciliation ([state-machine.md](state-machine.md) §7) |
| any | startup with a clean diff (2 checks) | (unchanged non-blocked mode) | trading allowed |

This table is the reconciliation-side view of [state-machine.md](state-machine.md) §7 and must stay row-consistent
with it (same triggers, same targets).

## 11. Frozen invariants honored

- **Startup/restart sync BEFORE any trading** — `control_state` read first, full reconcile (or block) before a single
  order is emitted, entries or exits (frozen-decisions.md, "Strategy, data & backtest honesty" (state-machine row);
  TZ §8). [LAW]
- **Adopt external/manual change via reconciliation, NOT as errors** — manual buy/sell, deposit/withdrawal, broker
  order drift all map to adoption/resolution, never a crash (frozen-decisions.md, "Strategy, data & backtest
  honesty" (state-machine row); TZ §8, §10). [LAW]
- **`kill` never sells** — a `killed` mode stays killed through reconciliation; reconciliation may observe but never
  liquidates (frozen-decisions.md, "Order & risk rules" (kill/pause row); TZ §7.8). [LAW]
- **`blocked_reconciliation_mismatch` policy** — block new entries; monitoring on; **RISK exits ALLOWED**;
  **PROFIT/target_trailing exits FORBIDDEN**; trend/time not auto-fired ([owner-pending], §5); broker-confirmed
  position + no conflicting order required; **notify before AND after** any exit (frozen-decisions.md, "Order &
  risk rules" — kill/risk-exits row; TZ §8). [LAW]
- **Retry schedule + 2 clean checks** — 3 attempts at 60/180/300s, require 2 consecutive clean reads before trading
  (TZ §8). [LAW]
- **Idempotency via client `order_id`** — no duplicate orders / no double-adoption after a restart/retry
  (frozen-decisions.md, "Order & risk rules" (idempotency/order_id row); TZ §8; db-schema §4). [LAW]
- **Account guard at reconciliation** — every broker read scoped to and asserted `== config.account_id`; foreign
  account = hard abort (frozen-decisions.md, "Account & access" (account-guard row); config §3; db-schema §4). [LAW]
- **Limit-only / long-only preserved** — adopted/managed sells never exceed broker-confirmed held qty; no short ever
  stored (frozen-decisions.md, "Order & risk rules" (limit-only row); [state-machine.md](state-machine.md) §5). [LAW]
- **Managed registry intact** — the bot adopts/manages but **never adds a ticker to `approved`** itself; non-approved
  longs go through the owner prompt (frozen-decisions.md, "Strategy, data & backtest honesty" (managed-registry row);
  TZ §10). [LAW]
- **No float money** — all broker-vs-local diffs use Quotation integer equality (db-schema §1). [LAW]
- **Append-only audit trail** — every reconcile/adoption journaled, reconstructable end-to-end (frozen-decisions.md,
  "Strategy, data & backtest honesty" (state-machine row); TZ §8, §12). [LAW]

## 12. Open questions / owner-pending

- **Periodic-reconciliation cadence** — **[owner-pending]**: TZ §8 fixes only the startup retry schedule (3× at
  60/180/300s, 2 clean checks); the steady-state `periodic` interval is **not** pinned. The reconciler treats it as a
  named value to be set, never a hard-coded guess (mirrors [state-machine.md](state-machine.md) §12).
- **`reconciliations.kind` vocabulary** — **[verify]**: [db-schema.md](db-schema.md) leaves `kind` free TEXT with
  examples `startup|periodic|post_restart`; whether to freeze it as a CHECK enum (mirroring the `control_state.mode`
  discipline) is open — raise before M4. Until then this contract uses exactly those three string values.
- **Bot-account product type** — **[verify, empirical at M4]**: whether an account-scoped token is feasible
  (Инвесткопилка / Счёт под ключ / Смарт-счёт cannot be scoped — config §6; TZ §9). Affects only the *source* of the
  account assertion during the broker read, not the reconciliation transitions.
- **Owner-confirm channel for clearing a block** — **[owner-pending]**: §7 requires owner confirm to leave
  `blocked_reconciliation_mismatch`; the exact Telegram affordance (a dedicated button vs `/resume`) is an M5
  control-plane detail (TZ §10), pinned with the confirm-flow tests. The **requirement** (owner confirm + 2 clean
  checks) is LAW; only the UI is pending.
- **Adoption of a partially-known order** — the dedupe of a broker fill that arrived during downtime for a local
  order still `submitted` is resolved by `order_id` match (adopt the fill → `partially_filled`/`filled`); the exact
  tie-break for a fill with **no** local `order_id` (extra broker order) is pinned by the M4 reconciler tests
  (default: `reconcile_required` → owner-surfaced, never silently traded).

## 13. Cross-references

- Frozen LAW: `docs/frozen-decisions.md` — "Strategy, data & backtest honesty" (startup reconciliation +
  adopt-not-error: state-machine row; managed registry row); "Order & risk rules" (kill/pause/resume row;
  exits-always-allowed: market-regime / risk-exits rows; idempotency/order_id row; limit-only/no-shorts row);
  "Account & access" (account-guard row).
- Spec: `docs/TZ.md` §7 (risk engine + controls), §8 (state machine, reconciliation, retry schedule, blocked policy),
  §9 (broker adapter, account scoping), §10 (Telegram manual-position prompt + Close button).
- Sibling contract (must stay row-consistent): [state-machine.md](state-machine.md) §3 (proposal TTL on restart),
  §5 (`reconcile_required`), §6 (exits), §7 (control plane), §10 (reconciliation/adoption rows).
- Schema (enum/type parity — reused verbatim): [db-schema.md](db-schema.md) §2, §3, §4.
- Config (keys: `account_id`, `mode`, `button_ttl_minutes`, `risk.capital_rub` + limits, `dividend_gap_block_days`):
  [config-and-secrets.md](config-and-secrets.md) §2, §3.
- Taxes (net dividend cashflow + dividend-gap feeding entry gates): [tax-and-dividends.md](tax-and-dividends.md) §6.
- Skills: `state-machine-discipline`, `risk-policy-guardian`, `broker-api-contract`. Auditors:
  `risk-invariant-auditor`, `lookahead-auditor`.
