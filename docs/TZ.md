# stonksbot — Техническое задание (TZ) / Build Spec — MVP v1

> **Кратко (RU):** это **всеобъемлющее ТЗ** для постройки осторожной торговой
> лаборатории под T-Invest (T-Bank) API на акциях MOEX. Цель MVP — не «робот, который
> зарабатывает 24/7», а **проверяемая система**: данные → честный бэктест → walk-forward →
> paper/sandbox → live **confirm** на выделенном счёте 10 000 ₽. Документ — источник правды
> для постройки (читается вместе с `docs/frozen-decisions.md` 🔒 и `AGENTS.md`). Прогресс по
> вехам — в `docs/ROADMAP.md` (репо) и `1-Projects/stonksbot/Roadmap.md` (второй мозг).
>
> **This is the build spec.** It does NOT restate the locked invariants — those live in
> `docs/frozen-decisions.md` (LAW). This document adds the *implementation contract* on top.

---

## 0. How to use this document
- **`docs/frozen-decisions.md` 🔒 wins** on any conflict. This TZ may only *detail* invariants,
  never weaken them. A genuine change requires an owner decision + an ADR + the same-change rule.
- Build **milestone by milestone** (§3). A component profile in `.agent-kit.json` is flipped
  `dormant → active` only when its milestone starts; `component-guardian` enforces "no toolchain
  until active".
- Every section marked **[verify]** must be re-checked against live T-Invest docs before that code
  ships. Section §20 lists what was already web-verified (2026) and what remains.

## 1. Goal, non-goals, product character
**Goal:** a disciplined research + execution loop that proves (or disproves) a small statistical edge
after real costs, then trades it safely under human-confirmed entries.

**Non-goals (MVP):** full autonomy; market-making/HFT; multi-broker; futures/options/bonds/FX/foreign;
LLM making trade decisions; "stable capital growth" as a promise.

**Product character = LABORATORY (owner decision).** The *research/paper* posture is high-resolution
and fast-learning: log **every** candidate signal (selected and rejected) with reasons, compute rich
diagnostics, iterate quickly. The *live* posture stays **conservative** (confirm-mode, ≤1 proposal/day,
the frozen portfolio limits). Strategy timeframe stays **D1** (daily close, no intraday lookahead —
frozen). "Lab" changes how we *learn*, not the risk frame.

## 2. Owner decisions captured in this TZ
| Topic | Decision |
| --- | --- |
| Character | Laboratory (rich paper diagnostics + fast iteration; conservative live) |
| Run environment | **Local (Windows) first** → VPS before live confirm |
| Tariff | Model **both** Инвестор (0.30%/side) and Трейдер (0.05%/side **+ 390 ₽/mo**) in backtest; pick by cost-sensitivity (see §13 — the monthly fee makes Инвестор likely better at 10k) |
| Universe | `approved` = SBER, T, GAZP, ROSN, TATN, X5; `watch_only` = IRAO, LKOH |
| Secrets | `.env` (git-ignored) + env vars locally; secret store/env on VPS |
| Dashboard | **Local FastAPI dashboard from the start** (127.0.0.1, read-only observability) |
| Manual sell | Telegram **"Закрыть позицию" button** + confirm |
| Taxes & corporate actions | **Model from the start**: two-layer PnL (pre/post НДФЛ), dividends, dividend-gap entry block, split adjustment |

Defaults set by this TZ (confirm during build — §19): benchmark = IMOEX (price) + **MCFTR** (total-return,
because dividends are modeled) + cash; DB = SQLite → Postgres; `max_holding_days` from backtest grid
{20, 40}; daily run after MOEX main-session close (~19:00 MSK) on the closed D1 candle.

## 3. Phased roadmap (milestones) — summary
Detail + live status in `docs/ROADMAP.md`.

- **M0 — Foundations:** project skeleton, config, `.env.example`, SQLite schema, logging, CI (ruff+pytest). Activates profile `research-backtest`.
- **M1 — Data layer:** T-Invest read-only + MOEX ISS fallback, `candles` + `instrument_reference`, snapshot versioning, stale/`data_conflict`, split adjustment, dividend calendar.
- **M2 — Strategy + honest backtest:** strategy contract, conservative fills (both sides), costs (both tariffs incl. monthly fee), 3-year backtest, taxes/dividends, benchmarks, pass/weak/fail gates.
- **M3 — Walk-forward + validation:** rolling train/test, sensitivity, DSR, cost-sensitivity, robust-params report (the evidence-gate artifact).
- **M4 — Broker adapter + risk engine + state machine (sandbox):** profiles `broker-adapter` + `execution-confirm` activate; order/position state machine, idempotency, reconciliation, kill/pause/resume — all in **sandbox**.
- **M5 — Telegram + dashboard + journal/reporting:** confirm flow, Close button, alerts; FastAPI dashboard; two-layer PnL; paper-mode parallel to live market.
- **M6 — Paper/sandbox ≥30 days → live confirm gate:** polish, pre-live gates, account guard, token policy, VPS deploy; owner approves live on the dedicated 10k account.

## 4. Stack & repository layout
**Stack:** Python 3.12+; FastAPI (dashboard backend + internal API); SQLite (MVP) → Postgres (VPS);
python-telegram-bot (control plane); APScheduler (daily jobs); pydantic (config/DTO); httpx + official
**T-Invest Python SDK** (grpc); pandas/numpy (research); pytest + hypothesis; ruff; structlog.

**Layout (created at M0):**
```
stonksbot/
  pyproject.toml  .env.example  .ruff.toml
  src/stonksbot/
    config/            # pydantic settings, account-guard, secrets loading
    data/              # market_data, instrument_reference, moex_iss, store (SQLite)
    universe/          # registry + statuses + eligibility filters
    strategy/          # pullback-in-uptrend, signal contract
    backtest/          # engine, costs, walk-forward, metrics, benchmarks
    risk/              # risk_engine, limits, market-regime, re-entry
    broker/            # tinvest adapter, normalization, rate-limit, reconciliation
    execution/         # state machine, order gateway, position_manager, exits
    telegram/          # bot, proposals, commands, whitelist
    dashboard/         # FastAPI app (127.0.0.1)
    journal/           # audit trail, two-layer PnL
    reporting/         # daily status, weekly report, alerts
    scheduler/         # APScheduler jobs, daily workflow
  tests/               # unit / integration / e2e + honesty tests
  docs/evidence/       # walk-forward-latest.md (evidence gate target)
```

## 5. Data layer (schema)
Core tables (SQLite; Postgres-compatible). Every row carries `source` + `source_version` + `as_of`.
- **`instrument_reference`**: `instrument_uid` (PK — **not FIGI**), ticker, lot, `min_price_increment`,
  currency, trading_status, whitelist_status (`approved|managed_only|watch_only|blocked|pending`),
  `first_1day_candle_date`, liquidity stats. **[verify]** identifiers via SDK.
- **`candles`**: uid, interval (D1 in MVP), ts (UTC normalized), OHLCV, `is_complete`, `is_stale`,
  `adjusted` (split-adjusted), source. Snapshot-versioned; re-loads create new versions, never silent overwrite.
- **`dividends`**: uid, ex_date, amount, currency (for dividend-gap block + total-return).
- **`signals`**: id, uid, ts, type, features snapshot, decision (`candidate|selected|skipped|risk_rejected`), reason.
- **`proposals`**: `proposal_id`, signal_id, created_at, ttl, telegram_user_id, state.
- **`orders`**: `order_id` (client idempotency key), proposal_id, uid, side=BUY/SELL, type=LIMIT, price, qty(lots), state, broker_order_id, attempts.
- **`fills`**: order_id, ts, price, qty, commission.
- **`positions`**: uid, qty, avg_price (broker), opened_at, source (`bot|manual_adopted|managed_only`), exit_rules, state.
- **`cash_events`**: ts, type (deposit/withdrawal/commission/tax/dividend), amount → recompute limits.
- **`reconciliations`**: ts, kind, result, mismatch details.
- **`audit_journal`**: append-only event log linking proposal→confirm→order→fill→position→exit.

Rule: **data truth** — primary T-Invest, cross-check MOEX ISS; transient divergence → re-check; persistent → `data_conflict`, skip entry (never block a protective exit). See `backtest-honesty` skill.

## 6. Strategy contract
Concept: **pullback inside an uptrend** (D1). The strategy module is a pure function answering the
standard contract (the only place strategy logic lives):
- **inputs/lookback:** D1 candles for the uid + index; required history.
- **trend filter:** price > MA50 and MA20 > MA50 (params optimizable).
- **pullback:** price 2–6% below local high, critical level not broken.
- **confirmation:** close > prior day OR back above MA20 OR volume rise on recovery.
- **signal timing:** computed only **after the daily close**; entry **next session** (frozen, no intraday lookahead).
- **invalidation / stop / take-profit / sizing:** per §7.
- **optimization space (research only):** ma_fast {10,20,30}, ma_slow {50,100}, pullback_min {2,3},
  pullback_max {4,5,6}, take_profit {5,6,8}, max_holding {20,40}. **Live bot never self-optimizes.**
- **ranking** (≤1 proposal/day): liquidity, trend strength, pullback quality, reward/risk, spread; tie → liquidity.

## 7. Risk engine
Enforces every frozen invariant (`docs/frozen-decisions.md`). Implements, in order:
1. **Pre-checks:** account_id guard; mode not pause/kill/blocked; market-regime (no entry if IMOEX < MA50 or 5d return < −5%); session = `NORMAL_TRADING` only for entries.
2. **Eligibility filters** (config): max lot value 30%, max spread 0.50%, min turnover 50M ₽, min trading days 40, trading-status + candles required → else `skipped` (skip ≠ remove from approved).
3. **Sizing:** `risk_per_lot = |entry−stop| × lot`; `lots = floor(allowed_risk / risk_per_lot)`; then clamp by max position (3000 ₽ / 30%), available cash, lot, `min_price_increment`. Risk/trade 50 ₽ is a **soft** sizing ref; hard stop ≈4%.
4. **Limits:** 1 open position, 50% cash reserve, daily hard stop 100 ₽ (blocks new entries), ≤1 proposal/day.
5. **Re-entry:** no same-day re-entry; 5-day cooldown; requires fresh pullback + new signal + new confirm.
6. **Exits (auto):** hard stop ~4%; trend-break (close < MA50); `target_then_trailing` (TP 6%, trail 3%, support close < MA20); time exit (`max_holding_days`).
7. **Controls:** `pause` (block entries, keep monitoring+exits); `resume` (extra confirm + preflight); `kill` (stop bot + cancel active orders only — **never sells**).

## 8. Order/position state machine & execution
States: `candidate → risk_rejected | awaiting_confirmation → expired | submitted → partially_filled |
filled → cancel_requested | cancelled → reconcile_required`; position `open → (risk|trend|target_trailing|time)_exit → closed`.
- **Confirm flow:** proposal has `proposal_id` + TTL, bound to whitelisted Telegram user; on confirm → **re-run preflight** (instrument tradable, price/spread/lot, limits, account_id, no conflicting orders, mode ok) → place order.
- **Order:** LIMIT only; **client `order_id` idempotency key on every PostOrder** (verified — §20); TTL 45 min (30–60); unfilled → cancel; partial → cancel remainder + manage filled; no price chasing; one attempt per signal; `max_entry_premium` 0.20% above reference.
- **Idempotency:** re-processing/restart must not double-submit (dedupe by `order_id`).
- **Reconciliation:** on startup/restart sync positions+orders from broker before trading; retry 3× (60/180/300s), require 2 consecutive clean checks; persistent mismatch → block entries, allow risk-exits only.
- **External/manual changes:** adopt via reconciliation (manual buy of approved → adopt+manage; manual sell → update; deposit/withdraw → recompute limits; manual position outside approved → observe-only until owner picks managed_only/approved).

## 9. Broker adapter (T-Invest) — grounded facts (2026-verified, §20)
- **Identifiers:** use `instrument_uid` as primary key (recommended over FIGI). [verified]
- **Tokens:** read-only (data/monitoring), full-access (trading), **account-scoped** (restrict to the bot account), sandbox. Per-mode tokens (sandbox / live_confirm / live_auto_small); startup scope check. [verified]
- **Pre-order:** check tradability, trading status, last price, `min_price_increment`, lot; optionally `GetOrderPrice` for pre-trade cost+commission (limit orders). [verified]
- **Rate limits:** ≤50 req/s total recommendation; **PostOrder 15/s (900/min)**, PostOrderAsync 600/min; streams 300 subscriptions / 100 sub-requests/min — layer reconnect/retry/backoff. [verified]
- **Candle history:** D1 available deep (per-call window up to ~6 years, limit 2400); use `first_1day_candle_date` for true depth; seconds only last month (irrelevant — we use D1). [verified]
- **Sandbox:** plumbing only — simplified fills + fixed-style commission, no taxes/dividends/full margin; **never proof of edge or execution quality.**
- **Sber = phase 2 (QUIK)** — not in MVP.

## 10. Telegram control plane (pult, not the engine)
- **Whitelist** of allowed Telegram user-id(s) (owner provides at M5); ignore + log others.
- Proposal message → buttons **Подтвердить / Отклонить** (one-shot, TTL, replay-protected — old button can't fire).
- **"Закрыть позицию"** button (manual sell) + confirmation.
- Commands: `/status`, `/pause`, `/resume`, `/kill`, `/positions`.
- Alerts: risk-limit hit, auto-exit, partial fill, TTL cancel, API down, status change, pause/kill/resume, failed preflight, reconciliation mismatch.

## 11. Dashboard (local, from start)
FastAPI bound to **127.0.0.1** (no external exposure in MVP; on VPS via SSH tunnel/VPN, never public).
Read-only views: current positions; active + rejected signals (with skip reasons); trade history; PnL
(pre/post tax); risk limits; API/market status; whitelist/statuses; logs. Mutating controls stay in
Telegram (dashboard is observability). Even local — gate behind a token/password.

## 12. Journal & reporting (two-layer PnL)
- **Audit trail:** append-only `audit_journal` linking proposal→confirm→order→fill→position→exit; exportable.
- **Two-layer PnL (owner decision):** (a) economic strategy PnL; (b) broker/tax PnL — **НДФЛ** (incl. loss carry/offset where modelable) + **dividends** received + commissions. Show both.
- **Daily status:** mode, cash, positions, daily PnL, total PnL, signals, trades, skip reasons, API/data errors.
- **Weekly report:** weekly + since-inception return, trades, open positions, commissions, taxes, skipped
  candidates, risk filters, errors, whitelist recommendations, short verdict.

## 13. Backtest & validation
- **History:** 3 years D1 for approved + index; split-adjusted; dividend-aware.
- **Honest fills (both sides):** entry fills only if day low ≤ limit; **exits modeled just as conservatively**
  (TP sell only if day high ≥ target; stop/MA-break gapping fills at the worse gap price); unfilled = no trade.
- **Costs (config, not constants):** commission **both tariffs** — Инвестор 0.30%/side (no monthly fee),
  Трейдер 0.05%/side **+ 390 ₽/mo** (waived only at no-trades / ≥1.5M assets / ≥5M turnover — **at 10k
  capital the monthly fee ≈ 47%/yr drag, so Трейдер is likely WORSE; cost model must include it**) +
  0.10%/side slippage. Iceberg +0.01%/side. Min commission 0.01 ₽. [verified §20]
- **Taxes/dividends:** model НДФЛ on realized gains + dividend cashflows; block new entry before a known
  dividend ex-date gap; report after-tax PnL.
- **Metrics:** expectancy, max drawdown, Sharpe + **Deflated Sharpe**, hit rate, turnover, exposure,
  **cost-sensitivity** (at what commission/slippage/fill-rate does the edge die?).
- **Benchmarks:** equal-weight buy-and-hold of approved; **IMOEX (price) + MCFTR (total-return)**; cash.
- **Gate criteria (locked, handoff sec 12):** PASS ≥ +2 pp vs equal-weight AND not worse than index AND
  DD ≤ benchmark AND not one-lucky-trade; WEAK PASS ≥ +1 pp; FAIL if it dies after costs.

## 14. Walk-forward & pre-live gates
- **Walk-forward:** optimize on train window → test on next → shift → aggregate; prefer robust params over
  max return; reject params that work on one ticker/short period. Output = `docs/evidence/walk-forward-latest.md`
  (the evidence-gate artifact).
- **Pre-live gates:** (1) 3-year backtest passes; (2) ≥30 days paper/sandbox with no critical execution
  issues, journal working, risk limits blocking bad trades, dashboard/Telegram consistent, weekly reports
  clear; (3) **manual owner approval** to go live confirm.

## 15. Live confirm
Dedicated account 10k; account_id guard; per-mode token; ≤1 proposal/day; confirm entries; auto exits;
`kill` never sells. `auto_small` architected but **disabled** (revisit only after a successful confirm period).

## 16. Security
- Secrets: `.env` (git-ignored) + env locally; VPS env/secret store. **Never** in code/config/logs/dashboard/Telegram.
- Tokens: per-mode, account-scoped where possible; startup scope check; rotation/revoke plan.
- Add a **secret-scan gate** (gitleaks/trufflehog/regex for T-Invest+Telegram token patterns) to pre-commit before any live profile activates.
- Telegram user-id whitelist; dashboard bound to 127.0.0.1 + auth; no request-header logging.

## 17. Deployment (local → VPS)
- **Local (Windows) first:** prevent host sleep, process supervisor/watchdog (auto-restart), NTP/time sync,
  SQLite backups, structured logs.
- **VPS (before live confirm):** Docker Compose, systemd/auto-restart after reboot, Postgres, secret store,
  firewall, dashboard via SSH tunnel/VPN (never public), DB+log backups, disaster-recovery + secret-rotation runbook.

## 18. Testing strategy
Unit (strategy, risk, state machine — pure, dependency-injected) → integration (adapter vs mocks, sandbox
plumbing) → e2e (full confirm cycle in paper). **Honesty tests** enforce no-lookahead, conservative both-side
fills, costs incl. monthly fee, walk-forward. Property-based (hypothesis): generated D1 candles → strategy/risk
invariants hold. The `lookahead-auditor` + `risk-invariant-auditor` subagents gate each PR.

## 19. Open items to confirm during build
- Telegram user-id(s) for the whitelist (owner provides at M5).
- Final `max_holding_days` (from backtest grid {20, 40}).
- Final tariff (output of cost-sensitivity — note the monthly-fee finding above).
- Benchmark index symbols (IMOEX + MCFTR assumed) + date alignment on gaps.
- Exact daily-run time (MSK) and holiday/short-session handling.
- DB switch point SQLite → Postgres (assumed at VPS/M6).

## 20. API facts verified (2026) + must-verify
**Web-verified (official T-Bank/T-Invest docs, June 2026; full research archived in the Second Brain
References as the `tinvest-api-grounding` run):**
- Tariffs (per side, securities): Инвестор **0.30%**, Трейдер **0.05%** (+390 ₽/mo), Premium **0.04%**;
  Iceberg +0.01%; exchange commission included; min commission 0.01 ₽. Инвестор no monthly fee.
  (invest-tariff PDFs, T-ИНВЕСТ-260424 / T-ТРЕЙД-260522 / T-ПРЕМ-260603.)
- Token types: read-only / full-access / sandbox / **account-scoped** (+transfer-access). (developer.tbank.ru/invest/intro/intro/token)
- Rate limits: ≤50 req/s total (recommendation); **PostOrder 15/s (900/min)**; PostOrderAsync 600/min;
  streams 300 subs / 100 sub-req/min. (developer.tbank.ru/invest/intro/intro/limits) — supersedes any legacy 5/s figure.
- **PostOrder accepts client `order_id` idempotency key.** (orders.proto / PostOrder page)
- `GetOrderPrice` = pre-trade cost+commission for **limit** orders. `GetTechAnalysis` = server-side
  SMA/EMA/RSI/MACD/Bollinger (own IndicatorInterval enum ≠ CandleInterval).
- `instrument_uid` recommended over FIGI.
- Candle depth: D1 deep (per-call window ~6y, limit 2400; use `first_1day_candle_date`); seconds last month only.

**Must-verify before the relevant code ships [verify]:** exact per-method rate caps (GetOrderPrice/GetTechAnalysis);
whether `GetOrderPrice` covers market orders (limit-only assumed); precise sandbox fill/commission semantics;
current SDK package name/version; account-scoped token creation flow in the T-Bank UI.

## 21. Legal / disclaimer
Own-account only — no third-party signals/advice/money management (else 39-ФЗ investment-advisory /
securities-management licensing applies; verify with the Bank of Russia registry). **No profit is
guaranteed; backtest results do not guarantee future returns;** API failures, gaps, partial fills,
commissions, and taxes are real. The owner makes all live-launch and risk-limit decisions.
