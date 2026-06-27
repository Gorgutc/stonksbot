# Contract — Configuration & Secrets (TZ §4.1)

> **Status:** M0 contract, **resolved on paper (no code yet)**. This pins the irreversible
> *shape* of configuration so the M0 `config/` module implements it verbatim.
> **`docs/frozen-decisions.md` 🔒 wins** on any conflict — values marked **[LAW]** mirror a frozen
> invariant and may not be changed here (only via owner decision + ADR + same-change rule).
> **[owner-pending]** = a value the owner must confirm before it is locked (do not silently fix it).
> **[verify]** = depends on a `docs/contracts/` or §20 fact still being confirmed by research `whq6u1gxe`.
>
> Two layers loaded by **pydantic-settings**: (1) **secrets** — env-only, never committed; (2) **config**
> — committed non-secret YAML/TOML. Precedence: explicit env var > `.env` > committed config file > default.

---

## 1. Secret layer (env-only — never in git, logs, dashboard, or Telegram) [LAW: token policy]

Loaded from environment / `.env` (git-ignored). **Separate token per mode.** Never a default value in code.

| Env key | Purpose | Required when | Notes |
| --- | --- | --- | --- |
| `TINVEST_TOKEN_SANDBOX` | T-Invest sandbox token | `mode = sandbox` | sandbox-scoped token only |
| `TINVEST_TOKEN_LIVE_CONFIRM` | T-Invest live token for confirm mode | `mode = confirm` | account-scoped where the account product type allows [verify] |
| `TELEGRAM_BOT_TOKEN` | Telegram control-plane bot token | always (control plane) | shape `^\d{6,12}:[A-Za-z0-9_-]{30,}$` |
| `DASHBOARD_AUTH_TOKEN` | Bearer token for the local dashboard | always (dashboard from start) | opaque random ≥32 chars |
| `TINVEST_TOKEN_LIVE_AUTO_SMALL` | *reserved, DISABLED in MVP* | never (MVP) | `auto_small` is architected but off [LAW] |

Rules: only the token for the **active** mode is required at startup (a missing token for an inactive mode is
not an error). Tokens are loaded into memory only; **never echoed** to logs/dashboard/Telegram (log presence
as a boolean `token_loaded=true`, never the value). **Startup scope check BLOCKS trading** (refuse to start),
never warns, if the token's scope is missing / over-broad / not account-scoped [LAW]. Token lifetime is
3-months-from-last-use (rolling) — operational note, not config.

**Secret-storage backend** (Windows local vs VPS secret store, OS keyring vs plain `.env`) is **[owner-pending]**
— the *contract* is "env vars / `.env` locally", the *storage mechanism* is decided before live (M6).

## 2. Config layer (committed, non-secret YAML/TOML)

Grouped. `bps` = basis points (1% = 100 bps). Money in **₽ integers** at the config edge; internally Quotation
units/nano (see [db-schema](db-schema.md)). **[LAW]** values mirror frozen invariants.

### 2.1 Account & mode
| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `account_id` | string | — (required for sandbox/confirm) | the **one** dedicated bot account [LAW]; see §3 guard |
| `mode` | enum `paper`\|`sandbox`\|`confirm` | `paper` | `confirm` = first live mode [LAW]; no full-auto |
| `db_path` | string | `./stonksbot.db` | SQLite (MVP) → Postgres DSN at VPS (M6) |
| `timezone` | string | `Europe/Moscow` | display/scheduling tz |

### 2.2 Dashboard & Telegram
| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `dashboard_bind` | string | `127.0.0.1` | **never public** [LAW]; VPS via SSH tunnel/VPN |
| `dashboard_port` | int | `8765` | local only |
| `telegram_user_whitelist` | list[int] | `[]` | allowed user-ids; others ignored+logged [LAW] · ids **[owner-pending]** (M5) |
| `button_ttl_minutes` | int | `45` | proposal/confirm button wall-clock TTL |

### 2.3 Universe (managed registry — bot may not add tickers) [LAW]
| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `universe.approved` | list[ticker] | `[SBER, T, GAZP, ROSN, TATN, X5]` | **[owner-pending confirm]** |
| `universe.watch_only` | list[ticker] | `[IRAO, LKOH]` | **[owner-pending confirm]** |
| `universe.managed_only` | list[ticker] | `[]` | adopted manual positions |
| `universe.blocked` | list[ticker] | `[]` | — |
| `universe.pending` | list[ticker] | `[]` | monthly-review proposals (bot sets `pending`, never auto-`approved`) |

Statuses are the frozen vocabulary `{approved, managed_only, watch_only, blocked, pending}` — must match the
`whitelist_status` CHECK enum in [db-schema](db-schema.md) exactly.

### 2.4 Eligibility filters (per-cycle) [LAW: starting values]
| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `eligibility.max_lot_value_pct` | int | `30` | lot cost ≤ 30% of capital |
| `eligibility.max_spread_bps` | int | `50` | 0.50% max spread |
| `eligibility.min_turnover_rub` | int | `50_000_000` | min avg daily turnover |
| `eligibility.min_trading_days` | int | `40` | min recent trading days |

A failing `approved` ticker → `skipped` for the cycle (reason logged); **skip ≠ remove from approved** [LAW].

### 2.5 Risk limits (pilot) [LAW]
| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `risk.capital_rub` | int | `10_000` | pilot capital |
| `risk.max_open_positions` | int | `1` | pilot; expansion = post-confirm owner decision |
| `risk.max_position_rub` | int | `3_000` | hard cap |
| `risk.max_position_pct` | int | `30` | of capital |
| `risk.cash_reserve_pct` | int | `50` | min cash |
| `risk.daily_hard_stop_rub` | int | `100` | blocks new entries that day |
| `risk.max_proposals_per_day` | int | `1` | ≤1 proposal/day |
| `risk.risk_per_trade_rub` | int | `50` | **soft** sizing reference (not a literal stop) |
| `risk.hard_stop_pct` | float | `4.0` | ~4% hard stop |
| `risk.reentry_cooldown_days` | int | `5` | no same-day re-entry [LAW] |
| `risk.market_regime_index_ma` | int | `50` | no entry if index close < MA50 [LAW] |
| `risk.market_regime_5d_floor_pct` | float | `-5.0` | no entry if index 5d return < −5% [LAW] |
| `risk.allowed_trading_status` | string | `NORMAL_TRADING` | entries only here [LAW]; exclude DEALER/auction |

### 2.6 Strategy params — **shipped live config pinned to frozen** [LAW]
| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `strategy.ma_fast` | int | `20` | MA20 canonical |
| `strategy.ma_slow` | int | `50` | MA50 canonical |
| `strategy.pullback_min_pct` | float | `2.0` | — |
| `strategy.pullback_max_pct` | float | `6.0` | — |
| `strategy.take_profit_pct` | float | `6.0` | TP pinned |
| `strategy.trailing_pct` | float | `3.0` | trail after TP |
| `strategy.trend_support_ma` | int | `20` | trail support = close < MA20 |
| `strategy.trend_break_ma` | int | `50` | exit on close < MA50 |
| `strategy.max_holding_days` | int | — | **[owner-pending]** from grid {20,40}; pinned after M3 |

> The optimization grid (ma_fast {10,20,30}, ma_slow {50,100}, …) is **research-only**; the live bot never
> self-optimizes. Changing a pinned live value = owner decision to change the frozen contract.

### 2.7 Order execution
| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `order.type` | const | `LIMIT` | **LIMIT only**; market/bestprice hard-rejected [LAW] |
| `order.ttl_minutes` | int | `45` | 30–60; unfilled → cancel; one attempt/signal [LAW] |
| `order.max_entry_premium_pct` | float | `0.20` | ceiling above reference; round tick **down for buy** |

### 2.8 Costs / tariff (both modeled in backtest)
| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `tariff` | enum `investor`\|`trader` | `investor` | **[owner-pending]** — finalized by M3 cost-sensitivity; investor likely (390 ₽/mo ≈ 47%/yr at 10k) |
| `costs.investor_commission_bps` | int | `30` | 0.30%/side, no monthly fee [verified] |
| `costs.trader_commission_bps` | int | `5` | 0.05%/side [verified] |
| `costs.trader_monthly_fee_rub` | int | `390` | modeled at 10k [verified] |
| `costs.slippage_bps` | int | `10` | 0.10%/side buffer (**BACKTEST** modeled cost; live cost is already in the fill price) |
| `costs.min_commission_units/nano` | int pair | `0, 10000000` | 0.01 ₽ floor as **Quotation** (no float — db-schema §1); parsed at load, never used as a float in PnL/cost math |
| `costs.iceberg_surcharge_bps` | int | `1` | +0.01%/side (TZ §13/§20); a 1-lot/3000 ₽ pilot never icebergs → effectively 0, kept for parity |

> **Sandbox ≠ tariff:** the T-Bank sandbox applies a flat 0.05% commission — a plumbing artifact, **not** the
> Трейдер tariff (also 0.05%). Backtest/live cost realism always uses the configured `tariff` + slippage both
> sides + the min-commission floor; never read the sandbox figure as the live cost (mirrors the frozen
> sandbox-≠-proof rule).

### 2.9 Data, benchmarks, schedule
| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `benchmarks` | list | `[IMOEX, MCFTR, cash, equal_weight]` | MCFTR gross vs net-dividends documented (see tax contract) |
| `index_source` | const | `moex_iss` | ✅ research `whq6u1gxe`: IMOEX **and** MCFTR daily candles via MOEX ISS (`engine=stock,market=index,interval=24`); T-Invest gives index *last price* only |
| `data_conflict.close_divergence_pct` | float | `0.5` | flag if D1 close differs >0.5% T-Invest vs ISS [LAW: data truth] |
| `data_conflict.recheck_delay_minutes` | int | `30` | transient → re-check; persistent → `data_conflict`, skip entry |
| `daily_run_time` | string `HH:MM` | **[owner-pending]** | run after the **final** MOEX D1 close (see `close_definition`). Research `whq6u1gxe`: official close = main-session **auction** (18:40–18:50 MSK pre-23.03.2026; **18:55–19:00 from 23.03.2026**); evening session (~23:50) does *not* set it [LAW: no-lookahead] |
| `close_definition` | enum `auction_close`\|`d1_candle_after_evening` | `auction_close` | **[owner-ratify]** auction close via `GetClosePrices`/`OrderBook.close_price` (final & lookahead-safe at 18:50/19:00) vs D1 candle close treated final only after ~23:50 — pick ONE for no-lookahead |
| `moex_auction_shift_date` | date | `2026-03-23` | closing auction moved to 18:55–19:00 from this date (schedule config) |
| `dividend_gap_block_days` | int | `2` | no new entry 2 trading days pre-ex (see tax contract) |
| `db_switch_point` | note | SQLite→Postgres at VPS/M6 | documented, not auto |

## 3. `account_id` guard (startup) [LAW]

Before any sandbox/confirm/live action the config module + risk engine must:
1. **Require an explicit `account_id`** — refuse to start if missing.
2. List broker accounts for the token; **refuse to start if multiple accounts exist and none exactly matches**
   `account_id` (no "pick the first" fallback).
3. **Show account name + id on startup** (log + dashboard status) for human verification.
4. **Require manual confirmation on any `account_id` change** between runs.
5. Trade **only** the configured account; migrating to the main account needs manual approval.

`paper` mode needs no broker account (no real orders). For **read-only market data** in paper mode, reuse a
read-only/sandbox-scoped T-Invest token (or read cached snapshots / MOEX ISS); never require a trade-scoped
token merely to read candles.

### 3.1 Config-load validation (hard-fail, not warn) [LAW: no-lookahead + account guard]
The loader must **refuse to start** (exit non-zero) if any of these hold:
1. `mode ∈ {sandbox, confirm}` and `account_id` is missing/blank, or multiple broker accounts exist with no exact match (§3).
2. The token required by the active `mode` (§1) is absent.
3. `daily_run_time` is unset, **or** it is **earlier than the final close implied by `close_definition`**:
   - `close_definition = auction_close` → `daily_run_time` ≥ 18:50 (≥ 19:00 on/after `moex_auction_shift_date`)
     and the close is sourced via `GetClosePrices`/`OrderBook.close_price` (NOT the GetCandles D1 close).
   - `close_definition = d1_candle_after_evening` → `daily_run_time` after the evening close (~23:55) **and** the
     D1 bar is confirmed `is_complete` (db-schema §4).
   This binds the two coupled knobs so the **no-lookahead** invariant cannot be misconfigured — a leaky
   combination is a startup-blocking error, never a silent default.

## 4. `.env.example` (placeholders only — ships in git) [LAW]

```dotenv
# stonksbot secrets — COPY to .env (git-ignored) and fill real values. NEVER commit real tokens.
TINVEST_TOKEN_SANDBOX=<sandbox-token-placeholder>
TINVEST_TOKEN_LIVE_CONFIRM=<live-confirm-token-placeholder>
TELEGRAM_BOT_TOKEN=<telegram-bot-token-placeholder>
DASHBOARD_AUTH_TOKEN=<random-32+char-placeholder>
# TINVEST_TOKEN_LIVE_AUTO_SMALL is intentionally absent — auto_small is DISABLED in the MVP.
```

The committed config file (e.g. `config/config.yaml`) holds **only** the §2 non-secret keys.

## 5. Secret-scan token shapes (pre-commit gate, TZ §16)

The secret-scan gate (`tools/secret-scan.mjs`, run by the git hooks) treats as a leak any committed value matching:
- **Telegram bot token:** `\d{6,15}:[A-Za-z0-9_-]{30,}` — bot-ids grow over time, so keep the id quantifier
  loose (do **not** cap at 12) lest a longer-id token slip past.
- **T-Invest token:** opaque `t.<base64url>` (~50+ chars; real tokens ~80+).
- **Catch-all (fail-closed):** any **non-placeholder** value assigned to a `*_TOKEN` key — covers
  `TINVEST_TOKEN_*`, `TELEGRAM_BOT_TOKEN`, and **`DASHBOARD_AUTH_TOKEN`** (which has no fixed shape) — plus
  `*SECRET*` / `*API_KEY` keys.
- Allow-listed: `<...>`-form placeholders and `${ENV}` / `os.environ` references; `.env.example` by path.

## 6. Owner-pending summary (raise before locking)
- `tariff` (investor vs trader) — finalized M3; default `investor`.
- `strategy.max_holding_days` — {20,40}; pinned after M3.
- `close_definition` + `daily_run_time` — research gives the auction-close answer (`auction_close` recommended);
  **owner must ratify** the close definition (it is the no-lookahead LAW surface).
- `universe.approved` / `watch_only` final confirmation.
- `telegram_user_whitelist` ids — needed by M5.
- Secret-storage backend (local Windows vs VPS store) — decided before live (M6).
- Bot-account product type (account-scoped token feasible? else rely on the guard) — [verify, empirical at M4].

## 6a. Resolved by research `whq6u1gxe` (see References `2026-06-27-tinvest-moex-tax-verify`)
- `index_source = moex_iss` (IMOEX + MCFTR via MOEX ISS; T-Invest = index last price only).
- **Build dependency (M0 pyproject, not a runtime key):** the Python SDK is **`t-tech-investments`**
  (latest 1.49.2, 2026-06-15) installed from T-Bank's **GitLab index**, NOT public PyPI
  (`--index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple`); legacy
  `tinkoff-investments` is quarantined. Pin the exact version at build time.
- D1 close = the main-session **auction** close; default `close_definition=auction_close` (owner-ratify).

## 7. Cross-references
- Frozen LAW: `docs/frozen-decisions.md` (account guard, limits, token policy, limit-only).
- Schema: [db-schema.md](db-schema.md) (enum/type parity). Taxes: [tax-and-dividends.md](tax-and-dividends.md).
- Spec: `docs/TZ.md` §4.1, §7, §11, §16. Skill: `secrets-token-policy`, `risk-policy-guardian`.
