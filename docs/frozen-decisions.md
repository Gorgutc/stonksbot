# Frozen decisions

Durable decisions that must not silently change. Paired with the `frozen-decisions`
skill, the `risk-policy-guardian` / `backtest-honesty` / `secrets-token-policy` /
`state-machine-discipline` skills, and the `risk-invariant-auditor` /
`lookahead-auditor` subagents.

**Same-change rule:** when a frozen decision genuinely changes, update BOTH this
file AND the check/verifier (or skill/agent) that enforces it, in the same change —
never let the doc and the guard drift apart. Changing one of these requires an
explicit owner decision; record it as an ADR in the Second Brain `Decisions/` too.

> **Provenance:** these are the decisions the owner locked across the six
> research/handoff documents (the T-Bank/MOEX trading-bot discussion). They are the
> product's safety contract, not suggestions. **These are LAW until the owner says
> otherwise.**

## Canonical files
- This file — the source of truth for risk + safety invariants.
- `AGENTS.md` → `PROJECT SPECIFICS` — the short mirror agents read first.
- Second Brain: `1-Projects/stonksbot/Conventions.md` 🔒 (human-facing mirror) and
  `Decisions/ADR-*` (the rationale per decision).
- When code exists: the risk-engine module + its tests become the executable guard.

## Frozen contracts

### Scope & product shape
| Decision | Why frozen | Enforced by |
| --- | --- | --- |
| Broker = **T-Invest (T-Bank) API only**; market = **MOEX Russian stocks**. Sber is **phase 2** (QUIK-based) and out of MVP. | One broker / one market keeps the first version provable; Sber lacks a comparable public retail trading API. | `broker-api-contract` skill, `docs/profiles/broker-adapter.md` |
| The system is a **trading laboratory**, not an "autonomous trader". Goal = a system that survives costs and does not break the account — NOT "stably grow capital". | "Stably grow capital" pushes overfitting; the honest goal is positive expectancy after costs. | `backtest-honesty` skill, review |
| **LLM/Codex/Claude are for building, reviewing, documenting — never for buy/sell decisions.** Trading actions come only from formal strategy + risk rules. | An LLM as order executor is non-auditable and non-repeatable. | `risk-policy-guardian`, `risk-invariant-auditor`, review |
| Phased path is mandatory: **research → backtest → walk-forward → paper/sandbox (≥30 days) → live `confirm` → (much later) `auto_small`**. No skipping. | Each stage discards false hypotheses before real money. | `pre_live_gates`, review |
| **Design around expected profit per trade after all costs — NOT trade frequency.** A ~0.03–0.07% gross/round-trip edge is garbage on the retail stack. | Frequency amplifies friction; only per-trade expectancy after costs matters. | `backtest-honesty` skill, review |
| **Event-driven / earnings logic is a v2 layer, deferred** — conceptually opposed to "24/7 small trades"; not in the MVP. | Keeps the first version a narrow, provable experiment. | review, `docs/profiles/` |

### Account & access
| Decision | Why frozen | Enforced by |
| --- | --- | --- |
| **Dedicated bot account** in phase 1; bot may trade **only the one configured `account_id`**; migration to the main account needs manual approval. | Blast-radius containment; never touch the main portfolio. | `risk-policy-guardian`, `account_guard` (when code) |
| **Account guard:** refuse to start in trading/confirm/live mode if `account_id` is missing, or if multiple accounts exist with no exact match; show account name+id on startup; manual confirm on account change. | Trading the wrong account is worse than most trading losses. | `risk-invariant-auditor`, startup check |
| **Token policy (core frozen):** no tokens in code / config files / logs / dashboard / Telegram; load from env or secret store; separate tokens per mode (sandbox / live_confirm / live_auto_small); startup token-scope check; block trading if scope unexpected. *(Storage specifics — Windows vs VPS secret store, per-account scoping — are owner-pending; see Improvements.)* | A token is the key to a brokerage account, not a setting. | `secrets-token-policy` skill, `.gitignore`, review |

### Order & risk rules
| Decision | Why frozen | Enforced by |
| --- | --- | --- |
| **Limit orders only** — no market orders in MVP; **no margin, no shorts, long-only.** | Caps price/blow-up risk; market orders on low liquidity fill at bad prices. | `risk-policy-guardian`, `risk-invariant-auditor` |
| **First live mode = `confirm`** (bot proposes, human confirms in Telegram); `auto_small` is architected but DISABLED in MVP. | Keeps a human at the point of opening risk. | `risk-policy-guardian`, review |
| **Portfolio limits (pilot):** 10 000 ₽ start; **max 1 open position**; max position 3 000 ₽ / 30% of capital; 50% cash reserve; **≤ 1 new trade proposal per day.** | Survive-first sizing for the learning period. | `risk-policy-guardian`, risk-engine tests |
| **Risk exits:** daily hard stop 100 ₽ (blocks new entries); hard stop-loss ~4%; trend-break exit (close below MA50); `target_then_trailing` (take-profit 6%, trailing 3%, trend support = close below MA20); time exit. | Automated protection without per-tick human babysitting. | `risk-policy-guardian`, position-manager tests |
| **Order TTL** ~45 min (30–60); if unfilled → cancel; if partially filled → cancel remainder & manage filled position; **no price chasing; one order attempt per signal.** | Avoid paying up to chase a moved price. | `state-machine-discipline`, execution tests |
| **`kill`** stops the bot and cancels active orders **only — it does NOT sell positions.** `pause` blocks new entries but keeps monitoring + exits; `resume` needs extra confirmation + preflight. | A kill switch must never itself dump the portfolio. | `risk-invariant-auditor`, control tests |
| **Every order carries a client order id (idempotency key);** no duplicate orders after a restart/retry; honor T-Invest rate limits (~50 req/s total, `postOrder` ~15 req/s) with backoff. | "Scheduler sent order submit twice" / duplicate orders after restart is a top real-world failure. | `state-machine-discipline`, `broker-api-contract`, execution tests |
| **No entries in weekend / evening / dealer sessions;** check trading status before every action — new entries only in `NORMAL_TRADING`. | Order types and execution differ outside the main session. | `risk-policy-guardian`, session manager |
| **Market-regime filter:** no NEW entries when the MOEX index closes below its MA50 or its 5-day return is below −5%; **exits are always allowed.** | Don't open into a falling market even on a per-stock signal. | `risk-policy-guardian`, market-regime check |
| **Re-entry discipline:** no same-day re-entry into a just-exited ticker; 5-day cooldown; re-entry requires a **fresh pullback + new signal + new confirm**. Never sell at +6% and immediately re-buy because price kept rising. | Prevents churn / chasing after a take-profit. | `risk-policy-guardian`, strategy contract |

### Strategy, data & backtest honesty
| Decision | Why frozen | Enforced by |
| --- | --- | --- |
| **Signal only after the daily candle closes; entry no earlier than the next session; no intraday lookahead.** | Prevents the backtest from "knowing" the close in advance. | `lookahead-auditor`, `backtest-honesty` skill |
| **Conservative backtest fills:** a limit entry fills only if the next-session order TTL window actually trades at/through the limit; with D1-only data, fill only at `D+1.open <= limit`; unfilled = no trade; costs applied **both sides** (0.30%/side commission + 0.10%/side slippage buffer ≈ 0.80% round trip). | Honest fills beat "bought at close, sold at magic target" or using the whole-day low for a 45-minute order. | `lookahead-auditor`, `backtest-honesty` skill |
| **Anti-overfitting:** optimize on train window only, validate out-of-sample / walk-forward; prefer robust params over max return; never trust a single backtest or sandbox profitability as proof. | Backtest overfitting + data snooping are the default failure mode. | `backtest-honesty` skill, review |
| **Data truth:** primary = T-Invest API, fallback + cross-check = MOEX ISS; on large divergence mark the instrument `data_conflict` and **skip the signal** (do not silently trade). | Quiet data degradation kills trading systems. | `backtest-honesty` skill, data-layer tests |
| **Instrument universe is a managed registry** (`approved` / `managed_only` / `watch_only` / `blocked` / `pending`), not hard-coded; the bot may NOT add new tickers to the trading universe itself. | Growing the universe must not silently grow risk. | `risk-policy-guardian`, universe tests |
| **Per-cycle eligibility filters** (starting values, in config): max lot value 30% of capital, max spread 0.50%, min avg daily turnover 50M ₽, min recent trading days 40, complete candles required; live trading status is re-read before every order action. A failing `approved` ticker is marked `skipped` for that cycle (reason: lot_too_expensive / low_liquidity / wide_spread / not_trading / data_missing / data_conflict) — **skip ≠ remove from approved**. | Universe-level risk control without losing the ticker. | `risk-policy-guardian`, universe tests |
| **State as an explicit machine with audit trail:** signal → proposal → confirm → order → (partial) fill → position → exit, with startup reconciliation and idempotent transitions; external/manual account changes are adopted via reconciliation, not treated as errors. | The expensive bugs live in state transitions, not in MA20/MA50. | `state-machine-discipline` skill, `risk-invariant-auditor` |

## Known drift / owner decisions pending
- M0 foundations now exist in the active `research-backtest` profile: Python
  package skeleton, config loader, SQLite DDL, account-guard stub, and ruff/pytest
  verification. `broker-adapter` and `execution-confirm` remain dormant. Many
  named enforcers (`pre_live_gates`, full risk-engine tests, live account-scope
  checks) are still future artifacts; treat them as the contract the later
  implementation must create, not as wired guards today.
- **Holding horizon:** the locked framing is **2–6 weeks** (max 8 without review); the
  early "2–5 days" idea is superseded. Exact `max_holding_days` still open (backtest
  grid 20/40 days).
- **Commission tier vs edge:** the cost model is Tariff *Инвестор* 0.30%/side
  (~0.80% round trip with the slippage buffer). The reports warn this leaves almost no
  edge against a ~1% move and that *Трейдер* (0.05%/side) is far cheaper — **re-verify
  the tariff before live.**
- **Final universe (owner-ratified 2026-06-29):** `approved` = SBER, T, GAZP,
  ROSN, TATN, X5; `watch_only` = IRAO, LKOH. (Supersedes the earlier NVTK/GMKN
  proposal.)
- **Close/run source (owner-ratified 2026-06-29):** `close_definition=auction_close`;
  `daily_run_time=19:05 Europe/Moscow`. Evening-session impact on provider D1
  candles remains empirical, but is not the M0 decision source.
- **Sandbox ≠ live economics:** the T-Bank sandbox uses a fixed 0.05% commission and
  simplified fills — never read sandbox profitability as proof of real-market quality.
- Full open-issue list (taxes, corporate actions, NTP/time sync, host-sleep/watchdog,
  two-layer PnL, dashboard/Telegram security, daily-workflow schedule, benchmark
  choice, …) lives in the Second Brain `1-Projects/stonksbot/Improvements.md`.

## Do-not-touch
- Secrets / tokens: never commit; never log; never echo to dashboard or Telegram.
- This file and `Conventions.md` 🔒 in the Second Brain: change only by explicit
  owner decision, recorded as an ADR, doc + guard updated together.
- Generated/vendored regions and `runs/` orchestrator output (already git-ignored).
