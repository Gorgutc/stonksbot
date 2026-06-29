# stonksbot — Техническое задание (TZ) / Build Spec — MVP v1 (rev.2)

> **Кратко (RU):** всеобъемлющее ТЗ для осторожной торговой лаборатории под T-Invest (T-Bank) API
> на акциях MOEX. Цель MVP — не «робот, который зарабатывает 24/7», а **проверяемая система**:
> данные → честный бэктест → walk-forward → paper/sandbox → live **confirm** на выделенном счёте
> 10 000 ₽. Источник правды для постройки (читается с `docs/frozen-decisions.md` 🔒 и `AGENTS.md`).
> Вехи — `docs/ROADMAP.md` (репо) и `1-Projects/stonksbot/Roadmap.md` (второй мозг).
>
> **rev.2** учитывает многоагентное adversarial-ревью (workflow `w3y28swo6`): добавлены contract-слой
> для M0 (схема БД с типами, ключи config/.env), грунтинг налоговой/дивидендной/сплит-моделей, явные
> hard-rules (margin/short/market), детализация fail-safe состояний и STOP-гейт при провале edge.

---

## 0. How to use this document
- **`docs/frozen-decisions.md` 🔒 wins** on any conflict. This TZ may only *detail* invariants, never
  weaken them. A genuine change requires an owner decision + an ADR + the same-change rule.
- Build **milestone by milestone** (§3). A profile in `.agent-kit.json` flips `dormant → active` only
  when its milestone starts; `component-guardian` enforces "no toolchain until active".
- **Resolve the M0 contract layer before writing M0 code:** §4.1 (config/secrets keys), §5.1 (schema DDL
  types/PK/FK/enums), §12.1 (tax/dividend rules) bake irreversible type/structure choices.
- **[verify]** = re-check against live T-Invest docs/SDK before that code ships (full list §20).

## 1. Goal, non-goals, product character
**Goal:** a disciplined research + execution loop that proves (or disproves) a small statistical edge
after real costs, then trades it safely under human-confirmed entries.

**Non-goals (MVP):** full autonomy; market-making/HFT; multi-broker; futures/options/bonds/FX/foreign;
LLM making trade decisions; "stable capital growth" as a promise.

**Product character = LABORATORY (owner decision).** The *research/paper* posture is high-resolution and
fast-learning: log **every** candidate signal (selected and rejected) with reasons, rich diagnostics, fast
iteration. The *live* posture stays **conservative** (confirm-mode, ≤1 proposal/day, frozen limits).
Strategy timeframe stays **D1** (daily close, no intraday lookahead — frozen). "Lab" changes how we
*learn*, not the risk frame.

## 2. Owner decisions captured in this TZ
| Topic | Decision |
| --- | --- |
| Character | Laboratory (rich paper diagnostics + fast iteration; conservative live) |
| Run environment | **Local (Windows) first** → VPS before live confirm |
| Tariff | Model **both** Инвестор (0.30%/side, **no monthly fee**) and Трейдер (0.05%/side **+ 390 ₽/mo**) in backtest; pick by cost-sensitivity (M3). **At 10k the 390 ₽/mo ≈ 47%/yr drag → Инвестор likely better.** M2 reports a verdict **per tariff** (provisional); M3 finalizes the binding tariff |
| Universe | `approved` = SBER, T, GAZP, ROSN, TATN, X5; `watch_only` = IRAO, LKOH |
| Holding horizon | **2–6 weeks base, max 8 weeks without review** (frozen; supersedes the early "2–5 days"). `max_holding_days` grid {20, 40} D1 bars realizes this (40 bars ≈ 8 weeks) |
| Secrets | `.env` (git-ignored) + env vars locally; secret store/env on VPS |
| Dashboard | **Local FastAPI dashboard from the start** (127.0.0.1, read-only observability; bounded MVP cut-line §11) |
| Manual sell | Telegram **"Закрыть позицию" button** + confirm |
| Taxes & corporate actions | **Model from the start**: two-layer PnL (pre/post НДФЛ), dividends, dividend-gap entry block, split adjustment (rules §12.1) |

Defaults (confirm during build — §19): benchmark = IMOEX (price) + **MCFTR** (total-return, since dividends
modeled) + cash; DB = SQLite → Postgres (at VPS); daily run after the **final** MOEX D1 close (see §17 — the
evening session matters); `max_holding_days` from {20, 40}.

## 3. Phased roadmap (milestones) — summary
Detail + live status + the post-M3 STOP gate in `docs/ROADMAP.md`.
- **M0 — Foundations:** skeleton, **config/.env contract (§4.1)**, **SQLite schema with DDL contract (§5.1)**,
  logging, CI. Activates `research-backtest`.
- **M1 — Data layer:** T-Invest read-only + MOEX ISS, `candles` + `instrument_reference` (+ index series),
  snapshot versioning, stale/`data_conflict` (threshold §5.1), split + ticker-history, dividend calendar.
- **M2 — Strategy + honest backtest:** strategy contract, conservative fills both sides, costs (both tariffs
  incl. 390 ₽/mo), taxes/dividends, benchmarks, pass/weak/fail **per tariff**. Also: start the **early
  signal-only lab journal** (begins the 30-day clock without execution).
- **M3 — Walk-forward + validation:** rolling train/test, sensitivity, DSR, cost-sensitivity → **binding
  tariff**; robust-params; `docs/evidence/walk-forward-latest.md`. **STOP gate:** if edge dies after costs →
  do NOT proceed to M4; iterate within frozen constraints or shelve.
- **M4 — Broker adapter + risk engine + state machine (sandbox):** activates `broker-adapter` +
  `execution-confirm`; idempotency, reconciliation, kill/pause/resume; **fill-model parity check**.
- **M5 — Telegram + dashboard + journal/reporting:** confirm flow, Close button, alerts; bounded dashboard;
  two-layer PnL; full execution-grade paper mirror.
- **M6 — Paper/sandbox ≥30d → live confirm gate:** pre-live gates, account guard, token policy, secret-scan
  gate; VPS prep can run **in parallel** (§17); owner approves live on the dedicated 10k account.

## 4. Stack & repository layout
**Stack:** Python 3.12+; FastAPI (dashboard + internal API); SQLite (MVP) → Postgres (VPS);
python-telegram-bot (control plane); APScheduler (daily/monthly jobs); pydantic (config/DTO); official
**T-Invest Python SDK** (grpc) — package **`t-tech-investments`** installed from T-Bank's
GitLab simple-index (NOT PyPI; legacy `tinkoff-investments` is quarantined), pin the exact
version at build time *(resolved: ADR-0005 / research whq6u1gxe)*; pandas/numpy (research); pytest + hypothesis;
ruff; structlog.

**Layout (M0):**
```
stonksbot/
  pyproject.toml  .env.example  .ruff.toml
  src/stonksbot/
    config/   data/   universe/   strategy/   backtest/   risk/
    broker/   execution/   telegram/   dashboard/   journal/   reporting/   scheduler/
  tests/      docs/evidence/
```

### 4.1 Configuration & secrets contract (resolve at M0)
Two layers, loaded by pydantic-settings. **Secrets (env-only, never committed; in `.env`):**
- `TINVEST_TOKEN_SANDBOX`, `TINVEST_TOKEN_LIVE_CONFIRM` (per-mode; account-scoped where possible),
  `TELEGRAM_BOT_TOKEN`, `DASHBOARD_AUTH_TOKEN`.
**Config (committed YAML/TOML, non-secret):** `account_id` (the dedicated bot account — id only, guarded),
`mode` (paper|sandbox|confirm), `db_path`, `dashboard_bind` (127.0.0.1), `telegram_user_whitelist` (ids),
universe lists + statuses, eligibility-filter thresholds, risk limits, strategy params (pinned), order TTL,
daily-run time, `tariff`, benchmark symbols, `data_conflict` thresholds.
`.env.example` ships with **placeholder** values only (no real token). **Secret-scan gate** (§16) matches
token shapes: Telegram bot token = `^\d{6,15}:[A-Za-z0-9_-]{30,}$`; T-Invest token = opaque ~80+ char
`t.<base64url>` style — treat any committed value matching these (or assigned to a `*_TOKEN` key) as a leak.

## 5. Data layer
Tables (SQLite; Postgres-compatible). Every row carries `source` + `source_version` + `as_of`. Data-truth:
primary T-Invest, cross-check MOEX ISS; transient divergence → re-check; persistent → `data_conflict`, skip
**entry** (never block a protective exit).

### 5.1 Schema contract (types / keys / enums — resolve at M0)
- **Money/price = NEVER float.** Store the T-Invest **Quotation** representation: `*_units` (INTEGER) +
  `*_nano` (INTEGER, value = units + nano/1e9), for `price`, `min_price_increment`, amounts, commissions.
  This preserves exact equality for reconciliation/idempotency. Convert to `Decimal` for research math only.
- **Timestamps = INTEGER epoch milliseconds UTC** (not TEXT/native datetime). Display-tz at the edges only.
- **`instrument_uid` = TEXT** primary identifier (not FIGI).
- **`instrument_reference`** (PK `instrument_uid`): ticker, `instrument_kind` (`share`|`index`),
  `is_tradable` (bool — indices `false`), lot, `min_price_increment_units/nano`, currency, trading_status,
  `whitelist_status` CHECK ∈ {`approved`,`managed_only`,`watch_only`,`blocked`,`pending`} (NULL for indices),
  `first_1day_candle_date`, liquidity stats incl. **`spread_bps`** (for the 0.50% filter + ranking),
  `identifier_history` (JSON — e.g. TCSG→T ticker/uid stitching).
- **`index_reference`/rows in instrument_reference** with `instrument_kind=index` hold IMOEX, MCFTR
  (`is_tradable=false`, no whitelist_status) so benchmarks/regime have a home. Index candles come from
  **MOEX ISS** (`index_source=moex_iss`); T-Invest gives index *last price* only *(resolved: ADR-0005 / research whq6u1gxe)*.
- **`candles`** (PK `instrument_uid, interval, ts, source_version`): OHLCV as Quotation units/nano,
  `is_complete`, `is_stale`, `adjusted` (split-adjusted), source. New loads = new `source_version` (no silent overwrite).
- **`dividends`** (uid, ex_date, gross_amount units/nano, currency). Source [verify §20].
- **`signals`** (PK id): uid, ts, features JSON, `decision` CHECK ∈ {`candidate`,`selected`,`skipped`,`risk_rejected`},
  reason. **Risk rejection is recorded here only** (no order row is created); `selected` → a proposal is created.
- **`proposals`** (PK `proposal_id`): signal_id FK, created_at, ttl_ms, telegram_user_id,
  `state` CHECK ∈ {`awaiting_confirmation`,`confirmed`,`rejected`,`expired`}.
- **`orders`** (PK `order_id` = client idempotency key): proposal_id FK, uid FK, side, type=`LIMIT`,
  price units/nano, lots, `state` CHECK ∈ {`submitted`,`partially_filled`,`filled`,`cancel_requested`,`cancelled`,`reconcile_required`},
  broker_order_id, attempts.
- **`fills`** (order_id FK, ts, price, qty, commission). **`positions`** (PK id; uid FK, account_id, qty,
  avg_price, opened_at, source ∈ {`bot`,`manual_adopted`,`managed_only`}, exit_rules JSON,
  `state` ∈ {`open`,`closed`}, close_reason ∈ {`risk`,`trend`,`target_trailing`,`time`,`manual`}).
- **`cash_events`** (ts, type ∈ {deposit,withdrawal,commission,tax,dividend}, amount) → recompute limits.
- **`reconciliations`** (ts, kind, result, mismatch JSON). **`audit_journal`** (append-only; FK
  proposal_id/order_id/position_id) linking proposal→confirm→order→fill→position→exit.
- **`data_conflict` threshold (config, default):** flag if D1 close differs > **0.5%** between T-Invest and
  MOEX ISS, OR a bar is missing/duplicated in the lookback window → re-check after delay → persistent → `data_conflict`.
- **Split adjustment:** corporate-action source [verify §20]; back-adjust prices by ratio + adjust volume;
  `candles.adjusted` true. Ticker/uid changes handled via `identifier_history`.

## 6. Strategy contract
Concept: **pullback inside an uptrend** (D1). Pure function (the only place strategy logic lives):
- **inputs/lookback:** D1 candles for uid + index; warm-up = max(MA windows) bars (see §13 warm-up).
- **trend filter:** price > MA50 and MA20 > MA50.
- **pullback:** 2–6% below local high, critical level intact. **confirmation:** close > prior day OR back
  above MA20 OR volume rise on recovery.
- **timing:** computed only **after the final daily close**; entry **next session** (frozen, no intraday lookahead).
- **Canonical MAs = MA20/MA50** across entry, exits, and regime (so all three agree — frozen exits use MA50/MA20).
  The optimization grid below is **research exploration only**; the **shipped live config is pinned to the
  frozen values** (ma_fast=20, ma_slow=50, take_profit=6%). If walk-forward strongly prefers other values,
  that is an **owner decision to change the frozen contract** (same-change rule) — never a silent live divergence.
- **optimization space (research only):** ma_fast {10,20,30}, ma_slow {50,100}, pullback_min {2,3},
  pullback_max {4,5,6}, take_profit {5,6,8}, max_holding {20,40}. **Live bot never self-optimizes.**
- **ranking** (≤1 proposal/day): liquidity, trend strength, pullback quality, reward/risk, spread; tie → liquidity.

## 7. Risk engine
Enforces every frozen invariant. In order:
1. **Pre-checks:** account_id guard (refuse to start if missing/ambiguous); mode not pause/kill/blocked;
   market-regime (no entry if IMOEX close < MA50 or 5d return < −5%); session = `NORMAL_TRADING` only for
   entries (**DEALER_NORMAL_TRADING and auction states are NOT eligible** — §9); `data_conflict` → skip entry.
2. **Hard order rules (reject, never emit otherwise):** **LIMIT only** — `ORDER_TYPE_MARKET` and
   `ORDER_TYPE_BESTPRICE` are hard-rejected; **never set `confirmMarginTrade=true` (no margin)**; **long-only —
   reject any SELL exceeding held qty (no shorts).**
3. **Eligibility filters** (config): max lot value 30%, max spread 0.50%, min turnover 50M ₽, min trading
   days 40, trading-status + candles required → else `skipped` (skip ≠ remove from approved).
4. **Sizing:** `risk_per_lot = |entry−stop| × lot`; `lots = floor(allowed_risk / risk_per_lot)`; clamp by max
   position (3000 ₽ / 30%), cash, lot, `min_price_increment`. Risk/trade 50 ₽ is a **soft** sizing ref (a
   literal 50 ₽ stop ≈ 1.5–2% would be whipsawed by D1 noise); hard stop ≈4%.
5. **Limits:** **1 open position** (pilot value — expansion to 2+ is a separate owner decision only after a
   successful confirm period), 50% cash reserve, daily hard stop 100 ₽ (blocks new entries), ≤1 proposal/day.
6. **Re-entry:** no same-day re-entry; 5-day cooldown; fresh pullback + new signal + new confirm. *(Rationale:
   never take profit at +6% and immediately re-buy the same ticker because price kept rising.)*
7. **Exits (auto):** hard stop ~4%; trend-break (close < MA50); `target_then_trailing` (TP **6%** pinned,
   trail 3%, support close < MA20); time exit (`max_holding_days`; **8-week max without review**).
8. **Controls:** `pause` (block entries, cancel still-live entry BUY orders, keep monitoring+exits); `resume` (extra confirm + preflight);
   `kill` (stop bot + cancel active orders only — **never sells**).

## 8. Order/position state machine & execution
States per §5.1 enums. Signal `selected` → proposal `awaiting_confirmation` → (confirm) preflight → order.
- **Confirm:** `proposal_id` + TTL bound to whitelisted user; on confirm **re-run preflight** (tradable,
  price/spread/lot, limits, account_id, no conflicting orders, mode). **TTL is wall-clock; a proposal created
  before a restart is re-evaluated and expired on resume** (no stale button fires).
- **Order:** LIMIT only; **client `order_id` idempotency key on every PostOrder**; TTL 45 min (30–60); unfilled
  → cancel; partial → cancel remainder + manage filled; no price chasing; one attempt/signal; `max_entry_premium`
  0.20% above reference. Limit price **rounded to a valid `min_price_increment` tick — DOWN for a buy** so the
  0.20% ceiling is never exceeded.
- **Idempotency:** re-processing/restart must not double-submit (dedupe by `order_id`).
- **Reconciliation:** on startup/restart sync positions+orders before trading; retry 3× (60/180/300s), require
  2 consecutive clean checks; persistent mismatch → state `blocked_reconciliation_mismatch`:
  **block new entries; monitoring on; RISK exits allowed; PROFIT/target exits FORBIDDEN; require a
  broker-confirmed position and no conflicting active orders; notify before AND after any exit attempt.**
- **External/manual changes:** adopt via reconciliation (manual buy of approved → adopt+manage; manual sell →
  update; deposit/withdraw → recompute limits). Manual position outside approved → §10 prompt.

## 9. Broker adapter (T-Invest) — grounded facts (2026-verified, §20)
- **Identifiers:** `instrument_uid` primary (over FIGI). [verified]
- **Order safety at the API boundary:** the adapter normalizer emits only `ORDER_TYPE_LIMIT`, never sets
  `confirmMarginTrade=true`, and rejects SELL > held qty — enforcing §7.2 even if upstream logic slips.
- **Tokens:** read-only / full-access / **account-scoped** / sandbox. Per-mode tokens; **startup scope check
  must BLOCK trading (refuse to start), not warn**, if the active token is missing, wrong-mode, read-only for
  `confirm`, or over-broad when account-scoping is available/required. Token **lifetime = 3 months from last use
  (rolling, resets each call)** — keep tokens warm or rotate. Account-scoped tokens are **not** available for
  Инвесткопилка / Счёт под ключ / Смарт-счёт — confirm the bot account's product type supports scoping; if it
  does not, owner-recorded guard-only full-access fallback relies on the `account_id` guard. [verified]
- **Pre-order:** check tradability, trading status (`NORMAL_TRADING` only; **DEALER_NORMAL_TRADING(=14) and
  auction states excluded**), last price, `min_price_increment`, lot; `GetOrderPrice` for pre-trade cost
  (limit orders — market-order support unconfirmed, moot since limit-only). [verified]
- **Rate limits:** ≤50 req/s total (recommendation); **PostOrder 15/s (900/min)** — layer reconnect/retry/backoff. [verified]
- **Indicators:** `GetTechAnalysis` (SMA/EMA/RSI/MACD/Bollinger; own IndicatorInterval enum). **If used, only
  on closed D1 candles and it must reproduce the locally-computed indicator values used in backtest** — else
  compute indicators locally (preserves backtest/live parity + no-lookahead).
- **Candle history:** D1 deep (per-call window ~6y, limit 2400; true depth via `first_1day_candle_date`). [verified]
- **Sandbox:** plumbing only — simplified fills (no partial fills), fixed-style commission, no taxes/dividends/full
  margin; **never proof of edge or execution quality** (drives the M4/M5 fill-parity check).
- **Sber = phase 2 (QUIK)** — not in MVP.

## 10. Telegram control plane (pult, not the engine)
- **Whitelist** of allowed user-id(s); ignore + log others.
- Proposal → **Подтвердить / Отклонить** (one-shot, TTL, replay-protected).
- **"Закрыть позицию"** (manual sell) + confirm. Commands: `/status`, `/pause`, `/resume`, `/kill`, `/positions`.
- **Manual-position prompt** (position outside approved, default observe-only) — three buttons: **«Сопровождать
  managed_only» / «Добавить в approved» / «Игнорировать»**. `managed_only` uses the **same exit rules as bot
  entries**, entry price = broker average, holding-period source = broker operation date (fallback adoption date).
- Alerts: risk-limit, auto-exit, partial fill, TTL cancel, API down, status change, pause/kill/resume, failed
  preflight, reconciliation mismatch.

## 11. Dashboard (local, from start; bounded)
FastAPI bound to **127.0.0.1** (never public; on VPS via SSH tunnel/VPN), behind `DASHBOARD_AUTH_TOKEN`.
**MVP cut-line (M5):** ship only **positions + signals (with skip reasons) + two-layer PnL + mode/status**
read-only — defer logs-viewer / whitelist-editor / rich charts to post-confirm. The Telegram confirm flow +
Close button must land **before** any dashboard polish. Mutating controls stay in Telegram (dashboard = observability).

## 12. Journal & reporting (two-layer PnL)
- **Audit trail:** append-only `audit_journal` linking proposal→confirm→order→fill→position→exit; exportable.
- **Two-layer PnL:** (a) economic strategy PnL (pre-cost); (b) broker/tax PnL — commissions + **НДФЛ** + net dividends.
- **Daily status:** mode, cash, positions, daily PnL, total PnL, signals, trades, skip reasons, API/data errors.
- **Weekly report:** weekly + since-inception return, trades, open positions, commissions, taxes, skipped
  candidates, risk filters, errors, short verdict.
- **Monthly whitelist-review job** (scheduler): re-evaluate `approved`+`watch_only` on liquidity/lot/spread/
  availability/signal-quality; emit **replacement proposals to Telegram for owner confirm**; the bot may set
  `pending` but **never auto-adds to approved**.

### 12.1 Tax / dividend / corporate-action rules (resolve at M0; sandbox models none of this)
- **НДФЛ on realized securities gains:** 13% (and 15% on the portion of annual income above 2.4M ₽ per the
  2025+ scale — at 10k pilot capital effectively 13%). [verify exact bracket application §20]
- **Lot accounting = FIFO** (RU broker convention) for realized-gain computation.
- **ЛДВ (3-year long-term exemption) does NOT apply** — the 2–6 week horizon is always far below 3 years; state
  this so it is not modeled.
- **Loss offset:** within-period netting of gains/losses for the after-tax layer; cross-year carry-forward noted but out of MVP scope.
- **Dividends:** taxed at source 13% → the bot's dividend cashflow in the after-tax layer is **net**; the
  **MCFTR** total-return benchmark is **gross** total return — compare like-for-like (document the gross/net choice).
- **Dividend-gap entry block:** no new entry from **2 trading days before** a known ex-date until the ex-date
  passes (configurable window).
- **Validation:** the tax layer can only be checked against **hand-computed fixtures** (no broker/sandbox
  reference) — M2 requires a worked НДФЛ example as a test fixture.

## 13. Backtest & validation
- **History:** 3 years D1 for approved + index, **plus a warm-up of ~100 leading bars** (MA50/MA100 + index
  MA50 regime consume up to ~100 D1 bars; load extra history so the first tradable day is not starved).
- **Honest fills (both sides):** entry limit placed at reference + ≤0.20% (mirrors §8: single attempt, TTL =
  no trade) → fills only if day low ≤ limit; **exits modeled just as conservatively** (TP sell only if day
  high ≥ target; stop/MA-break gapping fills at the worse gap price); unfilled = no trade.
- **Costs (config, not constants):** **both tariffs** — Инвестор 0.30%/side (no monthly fee), Трейдер
  0.05%/side **+ 390 ₽/mo** (waived only at no-trades / ≥1.5M assets / ≥5M turnover — **at 10k the monthly fee
  must be modeled**) + 0.10%/side slippage. Iceberg +0.01%/side. Min commission 0.01 ₽. [verified §20]
- **Taxes/dividends:** per §12.1 (NDFL FIFO, net dividends, dividend-gap block, gross/net MCFTR).
- **Metrics:** expectancy, max drawdown, Sharpe + **Deflated Sharpe** (needs the trial/config count — record it
  in the walk-forward artifact), hit rate, turnover, exposure, **cost-sensitivity** (commission/slippage/
  **fill-rate** break-even, using the §8 fill model).
- **Benchmarks:** equal-weight buy-and-hold of approved; **IMOEX (price) + MCFTR (total-return)**; cash.
- **Gate criteria (locked):** PASS ≥ +2 pp vs equal-weight AND not worse than index AND DD ≤ benchmark AND not
  one-lucky-trade; WEAK PASS ≥ +1 pp; FAIL if it dies after costs. **M2 reports per tariff; M3 finalizes.**

## 14. Walk-forward, STOP gate & pre-live gates
- **Walk-forward:** optimize on train → test on next → shift → aggregate; prefer robust params; reject
  one-ticker/short-period params. Output `docs/evidence/walk-forward-latest.md` (evidence gate).
- **STOP gate after M3:** if the evidence = **FAIL** (edge dies after costs — the most likely outcome per all
  research), **STOP the live track.** Options: iterate strategy/params *within frozen constraints* and re-run
  M2–M3, or shelve. **Do NOT build M4+ on a rejected edge** (operationalizes the phased-path LAW).
- **Early signal-only lab journal:** once M1 data + M2 strategy + the M0 journal base exist, start logging live
  candidate signals (no execution) to begin accumulating the ≥30-day window early.
- **Pre-live gates:** (1) 3-year backtest passes (chosen tariff); (2) ≥30 days paper/sandbox, no critical
  execution issues, journal working, risk limits blocking bad trades, dashboard/Telegram consistent, weekly
  reports clear, **fill-model parity** (backtest-assumed vs sandbox/paper observed) documented; (3) **manual
  owner approval** to go live confirm.

## 15. Live confirm
Dedicated 10k account; account_id guard; per-mode token; ≤1 proposal/day; confirm entries; auto exits; `kill`
never sells. **1 open position is a pilot limit** (expansion is a separate post-confirm owner decision).
`auto_small` architected but **disabled** (revisit only after a successful confirm period).

## 16. Security
- Secrets: `.env` (git-ignored) + env locally; VPS env/secret store. **Never** in code/config/logs/dashboard/Telegram.
- Tokens: per-mode, account-scoped where possible; **startup scope check blocks trading** if unexpected;
  lifetime 3-mo-from-last-use; rotation/revoke plan.
- **Secret-scan gate** (gitleaks/trufflehog/regex per §4.1 token shapes) in pre-commit before any live profile activates.
- Telegram user-id whitelist; dashboard 127.0.0.1 + `DASHBOARD_AUTH_TOKEN`; no request-header logging.

## 17. Deployment (local → VPS)
- **Local (Windows) first:** prevent host sleep, process supervisor/watchdog (auto-restart), NTP/time sync,
  SQLite backups, structured logs. The ≥30-day paper window runs here.
- **Daily-run time vs the evening session:** MOEX now has an **evening session to ~23:50 MSK**; the D1 bar is
  not final at 19:00 if evening trades print to it. **Decide and document whether the acted-on D1 close
  includes the evening session, and set the run time after the *final* close accordingly** (bears directly on
  the no-intraday-lookahead invariant). [verify §20]
  - **`close_definition` (OWNER-PENDING):** `auction_close` vs `d1_candle_after_evening` is still owner-ratify —
    see `docs/contracts/config-and-secrets.md` §6a (no-lookahead LAW surface; no canonical close asserted here).
- **VPS (prepared in parallel, before the live gate):** Docker Compose, systemd/auto-restart, **Postgres
  migration** (keep the SQLite→Postgres switch point explicit), secret store, firewall, dashboard via SSH
  tunnel/VPN (never public), DB+log backups, disaster-recovery + secret-rotation runbook. Provision M6a in
  parallel with the local paper window; M6b = pre-live gate check + owner approval only.

## 18. Testing strategy
Unit (strategy, risk, state machine — pure, dependency-injected) → integration (adapter vs mocks, sandbox
plumbing) → e2e (full confirm cycle in paper). **Honesty tests:** no-lookahead, conservative both-side fills,
costs incl. monthly fee, walk-forward, **tax fixtures** (worked НДФЛ example). Property-based (hypothesis):
generated D1 candles → strategy/risk invariants hold. `lookahead-auditor` + `risk-invariant-auditor` gate each PR.

## 19. Open items to confirm during build
- Telegram user-id(s) for the whitelist (M5). Final `max_holding_days` ({20,40}). Final tariff (M3 cost-sensitivity).
- **Index data source:** **MOEX ISS** (`index_source=moex_iss`) — IMOEX **and** MCFTR daily candles via
  MOEX ISS; T-Invest gives index *last price* only. Same answer as config-and-secrets §6a
  *(resolved: ADR-0005 / research whq6u1gxe)*.
- **Daily-run time** + whether D1 close includes the evening session (§17). Holiday/short-session handling.
- `data_conflict` exact threshold (default §5.1). DB switch point SQLite→Postgres (VPS/M6).
- Dividend calendar + split corporate-action **sources** (§20). Ticker-history stitching (TCSG→T).
- NDFL bracket application + lot-accounting fixture (§12.1). Bot-account product type (account-scoping feasible?).

## 20. API facts verified (2026) + must-verify
**Web-verified (official T-Bank/T-Invest docs, June 2026; full research archived in the Second Brain References
`2026-06-27-tinvest-api-grounding`):**
- Tariffs/side: Инвестор **0.30%** (no monthly fee), Трейдер **0.05%** (+390 ₽/mo), Premium **0.04%**; Iceberg
  +0.01%; exchange comm included; min 0.01 ₽; futures Инвестор 0.10%/side.
- Tokens: read-only / full-access / sandbox / **account-scoped**; lifetime 3-mo-from-last-use; account-scoping
  unavailable for Инвесткопилка/Счёт под ключ/Смарт-счёт.
- Rate limits: ≤50 req/s total; **PostOrder 15/s (900/min)**.
- **PostOrder client `order_id` idempotency** confirmed. `GetTechAnalysis` server indicators (own enum).
  `instrument_uid` over FIGI. Candle depth D1 deep (seconds last month only).

**Must-verify before the relevant code ships [verify]:** whether the SDK exposes
GetOrderPrice/GetTechAnalysis (the package itself is resolved: **`t-tech-investments`** via T-Bank's
GitLab simple-index, NOT PyPI — *resolved: ADR-0005 / research whq6u1gxe*); per-method rate caps
(GetOrderPrice/GetTechAnalysis) and PostOrderAsync 600/min
(unverified — bot uses sync PostOrder); whether GetOrderPrice covers market orders (limit-only assumed);
dividend method (GetDividends?) and corporate-action/split source; precise sandbox fill/commission semantics;
NDFL bracket application detail; the MOEX evening-session effect on the final D1 close.

Index source is resolved: IMOEX/MCFTR daily candles come from **MOEX ISS** (`index_source=moex_iss`);
T-Invest gives index *last price* only *(resolved: ADR-0005 / research whq6u1gxe)*.

## 21. Legal / disclaimer
Own-account only — no third-party signals/advice/money management (else 39-ФЗ investment-advisory /
securities-management licensing applies; verify with the Bank of Russia registry). **No profit is guaranteed;
backtest results do not guarantee future returns;** API failures, gaps, partial fills, commissions, and taxes
are real. The owner makes all live-launch and risk-limit decisions.
