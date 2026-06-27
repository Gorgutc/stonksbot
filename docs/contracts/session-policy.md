# Contract — Session policy & daily-workflow (TZ §19, §7, §8, §17)

> **Status:** M0 contract, **resolved on paper (no scheduler/session code yet)**. This pins the
> *session-eligibility* and *daily-workflow* rules the M0 `scheduler/` + risk-engine session-manager
> implement verbatim. **`docs/frozen-decisions.md` 🔒 wins** on any conflict — values marked **[LAW]**
> mirror a frozen invariant and may not be changed here (only via owner decision + ADR + same-change rule).
> **[owner-pending]** = a value the owner must ratify before it is locked (do **not** silently fix it).
> **[verify]** = an empirical fact still pending research / integration.
>
> Two surfaces are deliberately split: **(A) trading-status / session-window eligibility** is fully
> specifiable from frozen LAW and is specified here; **(B) the daily close definition + run time** is the
> **no-lookahead LAW surface** the owner must ratify — it is presented as **placeholders only** (an authored
> `daily_run_time` ≥ 18:50 would be a *de-facto silent frozen-change* and is forbidden here).
> Pairs with [config-and-secrets.md](config-and-secrets.md), [db-schema.md](db-schema.md),
> [tax-and-dividends.md](tax-and-dividends.md). Skills: `risk-policy-guardian`,
> `state-machine-discipline`, `broker-api-contract`.

---

## 1. Scope & vocabulary reuse (read first)

This contract governs **when** the bot acts, never **what** it buys/sells. It reuses the shared vocabulary
verbatim — never rename:

- Config keys (from [config-and-secrets.md](config-and-secrets.md)): `timezone` (`Europe/Moscow`),
  `daily_run_time`, `close_definition`, `moex_auction_shift_date` (`2026-03-23`),
  `risk.allowed_trading_status` (`NORMAL_TRADING`), `risk.market_regime_index_ma`,
  `risk.market_regime_5d_floor_pct`, `risk.max_proposals_per_day`, `data_conflict.recheck_delay_minutes`,
  `order.ttl_minutes`, `button_ttl_minutes`, `dividend_gap_block_days`.
- DB enums (from [db-schema.md](db-schema.md)): `control_state.mode ∈
  {running, paused, killed, blocked_reconciliation_mismatch}`; `signals.decision`;
  `signals.reason` skip codes; `reconciliations.kind ∈ {startup, periodic, post_restart}`.
- Broker trading-status tokens (vocabulary source = [broker-adapter.md](broker-adapter.md) §6.1
  and TZ §9 — **not** config §2.5, which only holds `risk.allowed_trading_status`):
  `NORMAL_TRADING`, `DEALER_NORMAL_TRADING(=14)`, auction states.

No new config key, enum value, or table is introduced by this contract; it only **composes** the existing ones.

## 2. The trading-status / session-window gate [LAW] (TZ §7.1, §9; frozen-decisions.md, "Order & risk rules" (session-gate row))

**Rule (frozen):** *no entries in weekend / evening / dealer sessions; check trading status before EVERY
action; new entries only in `NORMAL_TRADING`.* The session gate is a **pre-check** in the risk engine
(TZ §7 step 1), evaluated per instrument, **immediately before** any order construction — never cached from
an earlier point in the cycle.

### 2.1 Trading-status eligibility table

The broker's live `SecurityTradingStatus` is mapped to two booleans — `entry_eligible` and `exit_eligible`.
Exits are **always allowed** wherever the venue accepts a protective order; entries are admitted **only** in
the single fully-open continuous-trading state.

```text
ENTRY ELIGIBILITY  (config.risk.allowed_trading_status = "NORMAL_TRADING") [LAW]
  NORMAL_TRADING                 -> entry_eligible = TRUE
  DEALER_NORMAL_TRADING (=14)    -> entry_eligible = FALSE   # dealer session — excluded [LAW]
  OPENING_AUCTION                -> entry_eligible = FALSE   # auction state — excluded [LAW]
  CLOSING_AUCTION                -> entry_eligible = FALSE   # auction state — excluded [LAW]
  OPENING_PERIOD / CLOSING_PERIOD-> entry_eligible = FALSE
  DARK_POOL_AUCTION / DISCRETE_AUCTION -> entry_eligible = FALSE
  BREAK_IN_TRADING               -> entry_eligible = FALSE
  NOT_AVAILABLE_FOR_TRADING      -> entry_eligible = FALSE
  TRADING_STATUS_UNSPECIFIED     -> entry_eligible = FALSE   # unknown == not eligible (fail-safe)
  <any other / unrecognized>     -> entry_eligible = FALSE   # default-deny [LAW: fail-safe]
```

- **Default-deny:** any status not exactly equal to `NORMAL_TRADING` (the configured
  `risk.allowed_trading_status`) yields `entry_eligible = FALSE`. The dealer state and all auction states are
  **explicitly** excluded so a future enum addition cannot silently leak an entry (TZ §9: "DEALER and auction
  states excluded").
- **Member-spelling caveat [verify §20]:** only the two members `NORMAL_TRADING` and
  `DEALER_NORMAL_TRADING (=14)` **plus the default-deny RULE itself** are frozen. The other
  `SecurityTradingStatus` member spellings in the table above (`OPENING_AUCTION`, `CLOSING_AUCTION`,
  `OPENING_PERIOD`/`CLOSING_PERIOD`, `DARK_POOL_AUCTION`/`DISCRETE_AUCTION`, `BREAK_IN_TRADING`,
  `NOT_AVAILABLE_FOR_TRADING`, `TRADING_STATUS_UNSPECIFIED`) are **[verify §20]** against the live SDK enum
  (mirror [broker-adapter.md](broker-adapter.md) §6.1). Because the table is default-deny, a wrong
  spelling cannot leak an entry — it would simply fall through to `<any other / unrecognized> -> FALSE`.
- **Exits:** `exit_eligible` is TRUE in `NORMAL_TRADING`; a protective exit may also be **attempted** in other
  venue-accepting states where the order book is live. A status that blocks all order entry blocks exits too
  (nothing can be sent), but the engine **never widens an entry block into an exit block** by policy — the
  block is purely venue-imposed. This honors "exits are always allowed" (frozen-decisions.md, "Order & risk rules" (market-regime row)).

### 2.2 Status freshness & the "check before EVERY action" rule [LAW]

- **Status must be re-read** (live `SecurityTradingStatus`) immediately before **each** order submission — both
  the entry preflight (TZ §8) and **every** protective-exit attempt. A status read used at signal time is
  **never** reused at submit time.
- **Confirm-path re-check:** on a Telegram confirm, the **preflight re-runs the session gate** (TZ §8:
  "re-run preflight … tradable, price/spread/lot, limits, account_id, no conflicting orders, mode"). A proposal
  confirmed after the venue left `NORMAL_TRADING` is **rejected at preflight** (`signals.decision='skipped'`,
  `reason='not_trading'`), not sent.
- **Stale data:** if the trading-status read is unavailable/stale, treat as `entry_eligible = FALSE`
  (`reason='not_trading'`); a protective exit may still be attempted if the venue accepts it.

## 3. Session-window timeline (informational, Europe/Moscow) [verify]

The eligibility decision in §2 is driven **solely** by the live broker `SecurityTradingStatus`, never by a
wall-clock window. This timeline is documentation to align the scheduler's expectations with MOEX hours; the
exact clock boundaries are **[verify]** at integration and must never override the live status check.

| Phase | Approx MSK window | entry_eligible | Notes |
| --- | --- | --- | --- |
| Pre-market / opening auction | before ~10:00 | FALSE | auction state |
| **Main continuous session** | ~10:00 – ~18:40 | **TRUE iff `NORMAL_TRADING`** | the only entry window |
| Closing auction (pre-`moex_auction_shift_date`) | ~18:40 – 18:50 | FALSE | sets the official close [verify] |
| Closing auction (on/after `2026-03-23`) | ~18:55 – 19:00 | FALSE | auction shifted +10 min [verify §6] |
| Evening session | ~19:00 – ~23:50 | FALSE | **no entries** [LAW]; does **not** set the official close [verify] |
| Weekend / holiday | — | FALSE | **no entries** [LAW]; see §5 |

The **continuous-session clock window is advisory only**; the binding gate is `risk.allowed_trading_status ==
NORMAL_TRADING` (§2). Evening and weekend windows are listed to make the "no entries in weekend/evening/dealer
sessions" LAW visible, but the live status read is what enforces it.

## 4. Daily workflow & APScheduler job layout (TZ §19, §17; frozen-decisions.md, "Order & risk rules" (session-gate / market-regime / re-entry rows) + "Strategy, data & backtest honesty" (no-lookahead row))

The daily cycle runs **once per trading day, after the final D1 close** (see §6 for *when* — owner-pending),
and produces **at most one** proposal (`risk.max_proposals_per_day = 1` [LAW]). Ordering is fixed so that no
step can act on data the model could not yet know (no-lookahead).

### 4.1 Daily pipeline (ordered steps)

```text
DAILY CYCLE  (fires at config.daily_run_time — owner-pending, see §6)  [LAW: ≤1 proposal/day]
 0. control_state gate     : read control_state.mode (singleton row, db-schema §3.3).
                             paused | killed | blocked_reconciliation_mismatch -> NO new entries
                             (monitoring + protective exits continue; resume needs extra confirm — TZ §7.8).
 1. account_id guard       : assert config.account_id present & exact-match (config §3) [LAW] — else refuse.
 2. reconciliation         : reconciliations.kind='startup'|'post_restart' on (re)start;
                             'periodic' on the daily fire. Mismatch -> control_state.mode=
                             'blocked_reconciliation_mismatch' (TZ §8): block entries, monitor on,
                             RISK exits allowed, PROFIT/target exits FORBIDDEN.
 3. data load + truth      : load final D1 close per close_definition (§6); cross-check T-Invest vs MOEX ISS;
                             divergence > data_conflict.close_divergence_pct -> re-check after
                             data_conflict.recheck_delay_minutes -> persistent => data_status='data_conflict'
                             => skip ENTRY for that instrument (never an exit) [LAW: data truth].
 4. market-regime filter   : IMOEX close < MA(risk.market_regime_index_ma=50)  OR
                             IMOEX 5d return < risk.market_regime_5d_floor_pct (-5%) => NO new entries today;
                             exits ALWAYS allowed [LAW, frozen-decisions.md, "Order & risk rules" (market-regime row)].
 5. eligibility + session  : per approved ticker run §2 session gate + eligibility filters (config §2.4);
                             failing approved ticker => signals.decision='skipped' (reason code) — skip != remove.
 6. strategy (pure fn)     : compute signals on the FINAL closed D1 only; entry no earlier than next session
                             [LAW: no intraday lookahead]. dividend-gap block (tax §6) applies to entries only.
 7. rank + select          : ≤1 proposal/day -> at most one signals.decision='selected' -> ONE proposal
                             (proposals.state='awaiting_confirmation'); all others 'candidate'/'skipped'.
 8. propose (Telegram)     : confirm-mode only — bot proposes, human confirms (entry order placed NEXT
                             session, not now); protective exits remain automated. [LAW: confirm-first]
```

- **Entry timing [LAW]:** the proposal is computed after the final close; the **entry order is placed no
  earlier than the next session** and only while `NORMAL_TRADING` holds (§2). The daily cycle never submits a
  *buy* into the post-close window — it produces a proposal whose order is constructed in the next main session.
- **Exits are not gated by the daily cycle:** protective-exit monitoring runs continuously (independent of the
  once-a-day entry cadence) so a hard stop / trend-break / target-trailing / time exit can fire whenever the
  venue accepts the order — including on `paused` / `blocked_reconciliation_mismatch` (RISK exits only in the
  latter). This is why exit eligibility (§2.1) is evaluated per-attempt, not once per day.

### 4.2 APScheduler job inventory

| Job id | Trigger | Cadence | Purpose | Skips when |
| --- | --- | --- | --- | --- |
| `daily_cycle` | cron @ `daily_run_time` [owner-pending §6] | every MOEX **trading** day | §4.1 pipeline; ≤1 proposal | non-trading day (§5); host asleep (run on wake, §4.3) |
| `monthly_whitelist_review` | cron (monthly, owner-pending day/time) | monthly | re-evaluate `approved`+`watch_only` on liquidity/lot/spread/availability; emit **replacement proposals to Telegram for owner confirm**; may set `universe.pending` — **never** auto-`approved` (TZ §12) | — |
| `exit_monitor` | interval (continuous; cadence owner-pending) | intraday during venue-open | evaluate protective exits per-attempt session gate (§2); runs in `paused` (ALL protective exits continue) AND `blocked_reconciliation_mismatch` (RISK exits only; PROFIT/`target_trailing` FORBIDDEN; `trend`/`time` [owner-pending], not auto-fired — §4.1 step 2) | venue closed |
| `proposal_ttl_sweep` | interval | minutes | expire proposals past `button_ttl_minutes` (`proposals.state='expired'`); a proposal created before a restart is re-evaluated & expired on resume (TZ §8) | — |
| `order_ttl_sweep` | interval | minutes | cancel unfilled LIMIT orders past `order.ttl_minutes` (one attempt/signal; no price chasing — TZ §8) | — |

- **Timezone:** all cron triggers use `timezone = Europe/Moscow` (config §2.1) so MOEX hours are honored
  regardless of host tz. Epoch-ms UTC remains the stored canonical (db-schema §1); MSK is the schedule edge.
- **Single-fire idempotency:** `daily_cycle` is guarded so a restart on the same trading day does **not**
  re-run the proposal step (dedupe via the day's `signals`/`proposals`/`audit_journal` rows) — preserves
  `max_proposals_per_day = 1` across restarts.

### 4.3 Host-sleep / missed-run handling [verify]

- **Missed daily fire (host asleep / down at `daily_run_time`):** on wake, if the final D1 close for the
  current trading day is available and **today's** `daily_cycle` has not run, run it **once** (still ≤1
  proposal). If the next main session has already opened (the entry window is live), the entry timing LAW still
  holds — the proposal's order is for the **current** open session, which is the "next session" relative to the
  acted-on close. If the close is **not** yet final (woke before §6's run condition), **do not run** — wait.
- **APScheduler misfire policy:** use `misfire_grace_time` bounded to the trading day and
  `coalesce=true` (a backlog of missed daily fires collapses to a single run). Exact grace window is
  **[verify]** (pairs with the §17 watchdog / NTP work).
- A run skipped because the day was non-trading (§5) is **not** a misfire — it is expected.

## 5. MOEX trading calendar — holidays & short sessions [verify]

The scheduler must consult a **MOEX trading-day calendar** before firing `daily_cycle`; a non-trading day
produces no signals, no proposal, no entry (and no misfire — §4.3).

- **Non-trading days (weekends + MOEX holidays):** `entry_eligible = FALSE` for the whole day; `daily_cycle`
  does not fire (or fires and short-circuits at the calendar check). The **live session gate (§2) is still the
  final authority** — the calendar is an optimization/guard, not a substitute for the status read.
- **Short / pre-holiday sessions:** the session may end earlier; the **final-close timing in §6 shifts with
  the venue schedule**. The run condition (close is final per `close_definition`) — not a hard-coded clock —
  is what gates the run, so a short session is handled by the same close-availability check.
- **Calendar source [verify]:** MOEX ISS trading calendar (e.g. `/iss/.../securities` board schedule or the
  MOEX calendar endpoint) — confirm the exact endpoint + anonymous availability at integration (mirrors the
  ISS-source [verify] notes in db-schema §5 / tax §7). Until wired, the **live status gate (§2) is the
  fail-safe** (a holiday simply never shows `NORMAL_TRADING`).
- **Trading-day arithmetic** elsewhere (e.g. dividend ex-date = `last_buy_date + 1 trading day`, tax §6) uses
  the **same** MOEX trading calendar — one calendar source, no divergence.

## 6. Close definition & daily run time — OWNER-PENDING (no-lookahead LAW surface)

> **Do not assert a canonical close here.** `close_definition` + `daily_run_time` together form the
> **no-lookahead LAW surface** (frozen-decisions: "signal only after the daily candle closes … no intraday
> lookahead"; config-and-secrets §6a / §3.1; db-schema §4). The owner must **ratify** the close definition.
> Both options are presented as placeholders; an authored value ≥ 18:50 would be a silent frozen-change and is
> forbidden in this contract. The startup loader (config §3.1) **hard-fails** on a leaky combination.

### 6.1 The two coupled knobs (placeholders)

```text
close_definition : enum { auction_close , d1_candle_after_evening }   # [owner-ratify] — pick ONE
daily_run_time   : "HH:MM" (Europe/Moscow)                            # [owner-pending] — bound to the choice
```

| `close_definition` (option) | Final-close source | Lookahead-safe at | Required `daily_run_time` constraint (enforced by config §3.1 loader) |
| --- | --- | --- | --- |
| `auction_close` *(research-recommended, not asserted)* | main-session **auction** close via `GetClosePrices` / `OrderBook.close_price` — **not** the GetCandles D1 close | 18:50 MSK; **19:00** on/after `moex_auction_shift_date` (2026-03-23) | `daily_run_time` ≥ 18:50 (≥ 19:00 on/after the shift date) **[owner-pending — do not author the value]** |
| `d1_candle_after_evening` | GetCandles **D1 close** re-read after the evening session (~23:50) | after evening close (~23:55) **and** `candles.is_complete=1` (db-schema §4) | `daily_run_time` after ~23:55 **and** the D1 bar confirmed `is_complete` **[owner-pending — do not author the value]** |

- **Why owner-pending, not authored:** research `whq6u1gxe` *recommends* `auction_close`, but whether the
  evening session prints into the T-Invest GetCandles D1 `close` is an **empirical M1/M4 check** (db-schema §5).
  Asserting a concrete `daily_run_time` here would lock the no-lookahead LAW surface before the owner ratifies
  it — a silent frozen-change. The contract therefore fixes the **coupling rule**, not the value.
- **`is_complete` gate [LAW: no-lookahead]:** the daily pipeline (§4.1 step 6) may compute signals only on a D1
  bar whose `candles.is_complete=1`, which is set **only** when the close source matches the ratified
  `close_definition` (db-schema §4). This binds the scheduler to the data layer's no-lookahead gate.
- **Startup binding (config §3.1, restated — not redefined):** the loader **refuses to start** (exit non-zero)
  if `daily_run_time` is unset, **or** earlier than the final close implied by `close_definition`. This
  contract relies on that gate; it does not author the threshold value.

### 6.2 Auction-shift handling (`moex_auction_shift_date = 2026-03-23`)

- Before `2026-03-23`: closing auction ≈ 18:40–18:50; the `auction_close` final-close availability is **18:50**.
- On/after `2026-03-23`: closing auction ≈ 18:55–19:00; final-close availability shifts to **19:00**.
- The scheduler/loader must apply the **19:00** threshold for any `daily_run_time` evaluated on/after
  `moex_auction_shift_date` when `close_definition = auction_close` (config §3.1). The shift date is a config
  constant, not hard-coded in the scheduler.

## 7. Frozen invariants honored

| Invariant (frozen-decisions) | How this contract honors it |
| --- | --- |
| No entries in weekend/evening/dealer sessions; check trading status before EVERY action; entries only in `NORMAL_TRADING` ("Order & risk rules", session-gate row) | §2 session gate is a per-action pre-check; default-deny; dealer + auction explicitly excluded; weekend/evening rows in §3/§5 are non-eligible. |
| Signal only after the daily candle closes; entry no earlier than next session; no intraday lookahead (strategy/backtest) | §4.1 step 6 computes on the FINAL closed D1 (`is_complete=1`); entry order placed next session; §6 keeps the close/run-time as a ratified, lookahead-safe coupling. |
| ≤ 1 new trade proposal per day (`max_proposals_per_day=1`) (pilot limits) | §4.1 step 7 selects at most one; §4.2 `daily_cycle` single-fire idempotency preserves it across restarts. |
| Market-regime filter — no new entries when IMOEX < MA50 or 5d return < −5%; exits always allowed ("Order & risk rules", market-regime row) | §4.1 step 4 short-circuits new entries; exit monitoring (§4.2 `exit_monitor`) is independent of the regime gate. |
| `kill` stops the bot + cancels orders but never sells; `pause` blocks entries, keeps monitoring + exits ("Order & risk rules", kill/pause row) | §4.1 step 0 reads `control_state.mode`; `killed`/`paused` block the entry pipeline; protective exits continue (RISK-only under `blocked_reconciliation_mismatch`). |
| Account guard — refuse to start without an exact `account_id` match (account & access) | §4.1 step 1 asserts `config.account_id` before any session action [LAW]. |
| Startup reconciliation; state machine with audit trail (strategy/state) | §4.1 step 2 runs `reconciliations.kind` startup/post_restart/periodic; mismatch → `blocked_reconciliation_mismatch`. |
| Data truth — large divergence → `data_conflict`, skip the signal (never an exit) | §4.1 step 3 applies the divergence + recheck flow; skips entry only. |
| Re-entry discipline — no same-day re-entry; 5-day cooldown ("Order & risk rules", re-entry row) | §4.1 step 5/6 honors the cooldown before a ticker is eligible (config `risk.reentry_cooldown_days`); the once-daily cadence cannot churn a just-exited ticker. |

## 8. Open questions / owner-pending

- **`close_definition` (owner-ratify) [LAW surface].** `auction_close` vs `d1_candle_after_evening` — research
  *recommends* `auction_close`; the **owner must ratify** the no-lookahead close definition (config §6a).
  Presented as placeholders only — **no canonical close asserted** in this contract.
- **`daily_run_time` (owner-pending) [LAW surface].** Bound to `close_definition` per §6.1; the value is
  **not authored here** (an authored value ≥ 18:50 would be a silent frozen-change). The config §3.1 loader
  hard-fails a leaky combination; this contract only fixes the coupling rule.
- **Evening-session effect on the D1 close [verify].** Whether the evening session prints into the T-Invest
  GetCandles D1 `close` is an empirical M1/M4 check (db-schema §5); it decides whether `auction_close` vs
  `d1_candle_after_evening` is even necessary.
- **MOEX trading-calendar source [verify].** Exact MOEX ISS calendar endpoint + anonymous availability for
  holiday / short-session detection (§5); until wired, the live status gate (§2) is the fail-safe.
- **APScheduler misfire/grace window [verify].** `misfire_grace_time` + `coalesce` tuning for host-sleep /
  watchdog (§4.3) pairs with the §17 NTP/time-sync + watchdog work.
- **`monthly_whitelist_review` schedule [owner-pending].** Day-of-month + time for the monthly job (§4.2);
  the job logic (propose, never auto-approve) is frozen, only the cron time is open.
- **`exit_monitor` cadence [owner-pending].** Continuous-poll interval for protective-exit checks (§4.2),
  balanced against T-Invest rate limits (≤50 req/s; PostOrder 15/s — broker contract).

## 9. Cross-references

- Frozen LAW: `docs/frozen-decisions.md` — "Order & risk rules" (session-gate, market-regime, re-entry,
  kill/pause, ≤1 proposal/day rows), "Strategy, data & backtest honesty" (no-lookahead row), "Account & access"
  (account guard row).
- Spec: `docs/TZ.md` §7 (risk-engine pre-checks/controls), §8 (state machine, preflight, reconciliation),
  §9 (trading-status / DEALER + auction excluded), §17 (daily-run vs evening session), §19 (open items).
- Contracts: [config-and-secrets.md](config-and-secrets.md) (`daily_run_time`, `close_definition`,
  `moex_auction_shift_date`, `risk.allowed_trading_status`, §3.1 loader gate),
  [db-schema.md](db-schema.md) (`control_state.mode`, `candles.is_complete`, `reconciliations.kind`,
  `signals.decision`/`reason`), [tax-and-dividends.md](tax-and-dividends.md) (dividend-gap entry block,
  trading-day arithmetic).
- Skills: `risk-policy-guardian`, `state-machine-discipline`, `broker-api-contract`.
