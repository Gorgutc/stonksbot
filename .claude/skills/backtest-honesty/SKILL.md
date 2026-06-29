---
name: backtest-honesty
description: Use when writing or reviewing any strategy, signal, or backtest code — keep the backtest honest (no lookahead, conservative fills, full costs), validate out-of-sample with walk-forward, and never treat a single backtest or sandbox run as proof of profitability.
---

# Backtest honesty

The default failure mode of a trading bot is a beautiful backtest that is actually
overfit. Keep the research layer honest:

1. **No lookahead.** A signal is computed only after the daily candle closes; entry
   is no earlier than the next session. No intraday peeking at the close.
2. **Conservative fills — both sides.** A limit entry fills only if the
   next-session order TTL window actually trades at/through the limit price; with
   D1-only OHLC, fill only if `D+1.open <= limit` (whole-day low is not a valid
   45-minute-order proxy). Unfilled = no trade; no price chasing; one order
   attempt/signal. Model EXITS just as conservatively: a take-profit limit sell fills
   only if the day's high reaches it; a stop / MA-break that gaps through the level
   fills at the realistic worse (gap) price, not the exact level; apply costs on the
   exit too. Asymmetric exit optimism inflates results as much as entry lookahead.
3. **Costs both sides, always.** Apply commission 0.30%/side + a slippage buffer
   0.10%/side (≈ 0.80% round trip). Keep commission/slippage as **config, not
   constants** — the tariff tier is an OPEN owner decision (Инвестор 0.30%/side vs
   Трейдер 0.05%/side); report results at both tiers and re-verify before live.
4. **Validate out-of-sample.** Optimize on a train window, test on the next window,
   shift, repeat (**walk-forward**). Prefer robust params over max return. Reject
   params that work on one ticker or one short period.
5. **Report the right metrics:** expectancy `p·avgWin − (1−p)·avgLoss − costs`, max
   drawdown, Sharpe + Deflated Sharpe (for multiple-testing), hit rate, turnover,
   exposure; **cost-sensitivity** (at what commission / slippage / fill-rate does the
   edge die?).
6. **Benchmarks:** equal-weight buy-and-hold of the approved list, the MOEX index,
   and cash. "The bot made money" is not a benchmark.
7. **Sandbox ≠ proof.** The T-Bank sandbox uses a fixed 0.05% commission and
   simplified fills; it is for plumbing, not for proving the edge.
8. **Data truth.** Primary = T-Invest, cross-check = MOEX ISS. On large divergence,
   **re-check after a short delay** (a single transient poll difference is not a
   conflict); if it persists, mark `data_conflict` and skip the **new entry**.
   `data_conflict` blocks entries only — it must **never** block a protective
   risk-exit on an already-open position (a stale feed can't strand a losing trade).

Pair with the `lookahead-auditor` subagent for an adversarial read.
