# Profile: research-backtest  (status: dormant)

Governs the **research & backtest** layer: data ingestion, instrument reference,
the signal/strategy engine, and the honest backtest (walk-forward, cost model,
validation metrics). This is the layer that decides *whether the idea is worth
trading at all* — it must exist and pass before any live profile is activated.

## Scope
- Market-data loader (T-Invest API primary; MOEX ISS fallback / cross-check).
- `candles` + `instrument_reference` tables (timestamps, source version, staleness
  flag, lot size, figi/instrument_uid, currency, trading status, whitelist status).
- Strategy engine: "pullback inside an uptrend", parameters
  `optimized_with_manual_override`, one common parameter set across the approved
  universe in the MVP.
- Conservative backtest: signal only after the daily candle closes, entry no
  earlier than the next session (no intraday lookahead), limit fills only if the
  day's low reaches the limit, costs applied both sides (0.30%/side commission +
  0.10%/side slippage buffer), max entry premium 0.20% above reference (limit-price ceiling).
- Validation: expectancy, max drawdown, Sharpe + Deflated Sharpe, hit rate,
  turnover, exposure, **walk-forward** (train → choose → test → shift), cost
  sensitivity; benchmarks = equal-weight buy-and-hold of the approved list, the
  MOEX index, and cash.
- Gate criteria (locked, handoff sec 12): **PASS** if net return ≥ +2 pp vs equal-weight
  buy-and-hold AND not worse than the MOEX index AND max DD ≤ benchmark AND not dependent
  on one lucky trade; **WEAK PASS** ≥ +1 pp (acceptable if lower risk than the index);
  **FAIL** if the result disappears after costs or is worse than equal-weight.

## Status rule
- **dormant** — the rules above exist, but NO Python toolchain / dependency /
  build command may be introduced until an explicit request flips status to
  `active`. The `component-guardian` subagent enforces this.

## Active toolchain (when active)
Leave empty while dormant. Intended: Python 3.12+, pandas/numpy, pytest
(+ hypothesis selectively). Set `verify.fast`/`verify.deep` in `.agent-kit.json`.

## Decision checklist (fill when activated)
- [ ] data schema + snapshot versioning + stale-data mode recorded here
- [ ] strategy contract (lookback, signal, invalidation, stop, sizing) recorded
- [ ] backtest honesty checks wired (no-lookahead, costs, fill rule) as tests
- [ ] walk-forward + cost-sensitivity report produced (evidence gate target)

## Explicit defers
- Live broker calls (see `broker-adapter`), order placement (see
  `execution-confirm`), taxes, corporate-actions handling, intraday/second data.

## Verification
`pytest` over the strategy contract + backtest-honesty tests; the
`lookahead-auditor` and `backtest-honesty` skill gate this layer.
