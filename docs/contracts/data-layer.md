# Contract — Data-layer behavioral contract (TZ §5, §5.1, §19)

> **Status:** M0 contract, **resolved on paper (no data code yet)**. This pins the **behavior** of the M1
> `data/` layer — ingestion order, snapshot/source-version discipline, staleness, the `data_conflict` state
> machine, split back-adjustment, and identifier stitching — so M1 implements it verbatim. It is the behavioral
> companion to the **storage** contract [db-schema.md](db-schema.md): that file fixes the tables/types/enums;
> this file fixes *how rows get written and gated*. **`docs/frozen-decisions.md` 🔒 wins** on any conflict.
> Values marked **[LAW]** mirror a frozen invariant and may change only via owner decision + ADR + same-change
> rule. **[owner-pending]** = the owner must confirm before it locks. **[verify]** = an empirical check at M1/M4
> (do NOT block this M0 contract; do NOT assert a settled value). Identifiers, enums, table/column names, and
> config keys are reused **verbatim** from [db-schema.md](db-schema.md) and
> [config-and-secrets.md](config-and-secrets.md) — never renamed here.
>
> **Provenance for the resolved source facts (so this doc has no live dependency on a separate write-up):**
> ADR-0005 + research `whq6u1gxe` (Second Brain References `2026-06-27-tinvest-moex-tax-verify` /
> `2026-06-27-tinvest-api-grounding`) — index candles = **MOEX ISS** (IMOEX + MCFTR, `engine=stock`,
> `market=index`, `interval=24`); dividends = T-Invest `InstrumentsService.get_dividends` (GetDividends);
> splits / corp-actions / renames = **MOEX ISS** (no T-Invest API). These are cited inline per rule below.

This contract feeds: the M1 data layer; the strategy/backtest no-lookahead surface (TZ §6, §13); the risk
engine's `data_conflict` / staleness skip gate (TZ §7.1); and the `instrument_reference`, `candles`,
`dividends`, `data_conflicts` tables ([db-schema.md](db-schema.md)).

---

## 1. Scope, sources & the data-truth law

| # | Source | Role | What it provides | Provenance |
| --- | --- | --- | --- | --- |
| 1 | **T-Invest API** (`t-tech-investments` SDK, gRPC) | **PRIMARY** | share D1 candles (`GetCandles`), instrument reference (`InstrumentsService`), dividends (`GetDividends`), trading status, `min_price_increment`, lot | TZ §9, §20 [verified] |
| 1a | **T-Invest API** — auction close (`GetClosePrices` / `OrderBook.close_price`) | PRIMARY (close source, **owner-ratify**) | the main-session auction close that defines `close_definition=auction_close` (§4) | TZ §20 [verify] — the MOEX evening-session effect on the final D1 close is must-verify; close source is owner-ratify (§4, [config-and-secrets.md](config-and-secrets.md) §6a) |
| 2 | **MOEX ISS** | **FALLBACK + CROSS-CHECK** for shares; **PRIMARY** for indices & corp-actions | IMOEX + MCFTR D1 candles; splits / corp-actions / renames; cross-check D1 close for shares | ADR-0005 + research `whq6u1gxe` |

**Data-truth [LAW] (frozen-decisions.md, "Strategy, data & backtest honesty" (data truth row), TZ §5):** primary = T-Invest; fallback + cross-check = MOEX ISS. On a
**large divergence (> 0.5% on the D1 close)** OR a **missing / duplicated bar** in the lookback window, the
instrument is marked `data_conflict` and the **signal is skipped — the bot never silently trades on suspect
data**. `data_conflict` skips an **ENTRY but NEVER blocks a protective EXIT** (§5.4, [LAW]).

**Index data source (resolved — ADR-0005 + research `whq6u1gxe`, TZ §19):** IMOEX (price) and MCFTR
(total-return) **daily candles come from MOEX ISS** — `/iss/engines/stock/markets/index/securities/{SECID}/candles`
with `interval=24` (D1), `engine=stock`, `market=index`. T-Invest exposes index **last price only**, never index
candles, so the index series has **no T-Invest leg and therefore no cross-check** (§3.4). Stored as
`candles.source='moex_iss'`. Config key: `index_source = moex_iss` (const; [config-and-secrets.md](config-and-secrets.md) §2.9).

**Dividend source (resolved — research `whq6u1gxe`, TZ §12.1):** T-Invest `InstrumentsService.get_dividends`
(GetDividends). Per-share GROSS amount is in field **`dividend_net`** (mislabeled — it is gross). There is **no
`ex_dividend_date`** → derive `ex_date = last_buy_date + 1 trading day`; the request `to` param filters on
`record_date`. Behavior detailed in §7; storage in [db-schema.md](db-schema.md) `dividends`; tax/gap rules in
[tax-and-dividends.md](tax-and-dividends.md).

**Corp-action / split / rename source (resolved — research `whq6u1gxe`, TZ §12.1):** **MOEX ISS only** (no
T-Invest API) — `/iss/statistics/engines/stock/splits[/{sec}]`, `/iss/cci/corp-actions`,
`…/shares/securities/changeover`. Behavior in §6.

## 2. Identifier rules (the stable keys)

- **`instrument_uid` (TEXT) is THE primary identifier** — never FIGI ([LAW], frozen-decisions; db-schema §1).
  All candles/dividends/signals/orders/positions key on `instrument_uid`. FIGI is not stored as a key.
- **ISIN is the cross-source stitch key.** MOEX ISS speaks MOEX trading-code (`SECID`) + ISIN; T-Invest speaks
  `instrument_uid` + ISIN. The data layer joins the two sides **on ISIN** (the only identifier both sources share
  stably across a rename), then maps to the canonical `instrument_uid`.
- **Renames** (e.g. **TCSG → T**, MOEX `action_date` **2024-11-27**, confirmed via ISS `…/changeover`) are
  recorded in `instrument_reference.identifier_history` (JSON `[{ticker,uid,from,to}]`) and stitched on ISIN so a
  history query spans the old and new ticker as one continuous series (§6.2).
- **[verify] (M4, empirical — do NOT assert):** whether a T-Invest `instrument_uid` **changes across a rename**
  is unexposed. The contract therefore treats **ISIN as the stable join key** and confirms `instrument_uid`
  continuity (vs. a fresh uid that must be re-stitched) at integration — never assumed settled here.

## 3. Ingestion behavior

### 3.1 Ingestion order (per daily cycle, after the final D1 close)
Ordered so each step has its prerequisites; later steps depend on earlier ones (sequential):

```text
1. Reference refresh   -> instrument_reference (T-Invest InstrumentsService): ticker, instrument_kind,
                          is_tradable, lot, min_price_increment_{units,nano}, currency, trading_status,
                          first_1day_candle_date.  Index rows (IMOEX, MCFTR) seeded as instrument_kind='index'.
2. Identifier stitch   -> resolve ISIN <-> instrument_uid; apply MOEX ISS changeover; write identifier_history.
3. Corp-action / split -> MOEX ISS splits/corp-actions; compute back-adjust ratios (applied in step 5).
4. Candle pull (shares)-> T-Invest GetCandles D1 (PRIMARY) for approved + watch_only + managed_only uids.
5. Split back-adjust    -> apply ratios to historical OHLC + volume; set candles.adjusted (see §6.1 PROVISIONAL).
6. Candle pull (index) -> MOEX ISS D1 for IMOEX + MCFTR (no T-Invest leg).
7. Cross-check (shares) -> MOEX ISS D1 close vs T-Invest close; run the §5 data_conflict state machine.
8. Dividend calendar    -> T-Invest GetDividends; derive ex_date; load forward horizon for the gap block.
9. Liquidity stats      -> avg_turnover_rub, spread_bps for the eligibility filters + ranking.
10. Completeness gate    -> set candles.is_complete only when the close matches config.close_definition (§4).
```

- **A failed step never silently produces a tradable bar.** If a step that a downstream entry depends on cannot
  complete (e.g. T-Invest candles unavailable), the instrument is `skipped` for that cycle with the schema's
  frozen reason code (`data_missing` / `not_trading` / `data_conflict`) — `skip ≠ remove from approved` [LAW].
- **Exits do not depend on the full chain.** The latest usable price for a held position is read independently so
  a degraded ingestion never blocks a protective exit (§5.4).

### 3.2 Snapshot / source-version discipline (no silent overwrite) [db-schema §1]
- Every data row carries **`source`**, **`source_version`** (INTEGER), and **`as_of`** (epoch-ms UTC).
- **A new load = a new `source_version`.** Re-fetching a bar/dividend that already exists writes a **new
  versioned row**, never an in-place UPDATE. `candles` PK is `(instrument_uid, interval, ts, source_version)`;
  `dividends` PK is `(instrument_uid, last_buy_date, source_version)` — the version is part of the key, so an
  overwrite is structurally impossible.
- **Latest-wins read:** consumers (strategy, backtest, reconciliation) read the **highest `source_version`** for
  a given `(instrument_uid, interval, ts)`. Older versions are retained for audit and divergence forensics.
- `source` is the frozen vocabulary `'tinvest' | 'moex_iss'` (db-schema §2/§3). Index candles are always
  `'moex_iss'`; share candles are `'tinvest'` unless a fallback row is explicitly written from ISS.

### 3.3 Time & money representation (no edge conversions leak in) [db-schema §1]
- **Timestamps = INTEGER epoch-ms UTC** at every stored column; tz (`Europe/Moscow`,
  [config-and-secrets.md](config-and-secrets.md) §2.1) is applied only for display/scheduling at the edges.
- **Money/price = Quotation `*_units` + `*_nano` INTEGER pair — NEVER float.** The ingestion layer stores the
  raw Quotation from T-Invest verbatim; ISS decimal strings are parsed to `units`/`nano` exactly (no float
  intermediate). `Decimal` is permitted only for derived research math, never for stored values.
- **Bar timestamp (`candles.ts`)** is the canonical D1 bar timestamp; the close it carries must match
  `config.close_definition` before `is_complete=1` (§4).

### 3.4 Index leg has no cross-check
Because T-Invest provides no index candles, IMOEX/MCFTR have a **single source** (MOEX ISS). The §5
divergence check (which needs two legs) **does not apply** to indices; only the **missing / duplicate / stale**
checks (§3.5, §5.2) apply. A missing index bar that the market-regime filter needs → the regime check fails
**closed** (no NEW entries when the regime input is unavailable), mirroring the regime LAW (frozen-decisions.md, "Order & risk rules" (market-regime row)).

### 3.5 Completeness vs. staleness (two distinct flags)
- **`is_complete`** — the bar reflects the FINAL D1 close per `config.close_definition` (no-lookahead gate, §4).
  Set to `1` only at ingestion step 10.
- **`is_stale`** — the freshest stored bar is **older than expected for the current trading calendar position**
  (e.g. today is a trading day past the close but no new complete bar arrived). Stale data is flagged, logged,
  and treated as **skip-entry** for that instrument (it is not, by itself, a `data_conflict` — see §5.2).

## 4. No-lookahead completeness gate [LAW: signal after final close] (TZ §5.1, §6, §17)

- `candles.is_complete = 1` **only** when the bar carries the **final** close defined by
  `config.close_definition` (enum `auction_close | d1_candle_after_evening`, [config-and-secrets.md](config-and-secrets.md) §2.9):
  - `auction_close` → the main-session **auction close** from `GetClosePrices` / `OrderBook.close_price`,
    captured at/after **18:50 MSK** (**19:00 from `moex_auction_shift_date` = 2026-03-23**).
  - `d1_candle_after_evening` → the `GetCandles` D1 close re-read after **~23:50 MSK** once the evening session
    has printed.
- The data layer **asserts the close source matches `close_definition`** before setting `is_complete=1`; a
  mismatch is a hard error, never a silent set. A signal may be computed **only** on an `is_complete=1` bar.
- `close_definition` is **[owner-ratify]** — research recommends `auction_close`; this contract does **not**
  assert a final value (it is the no-lookahead LAW surface; config-and-secrets §6a owns the ratification).
- **[verify] (M1, empirical):** does the T-Invest GetCandles D1 `close` include the evening session? Snapshot
  19:00 vs 23:50 on a known day. Until this passes, prefer `auction_close` (db-schema §4).

## 5. `data_conflict` detection state machine [LAW: data truth]

The single source-of-truth for when an instrument is `data_status='data_conflict'` and a signal is skipped.
Detection writes rows to `data_conflicts` (kind ∈ `close_divergence | missing_bar | duplicate_bar`, db-schema
§3.1) and flips `instrument_reference.data_status` ∈ `ok | data_conflict` (db-schema §2).

### 5.1 Trigger conditions (per instrument, per cycle)
```text
close_divergence : |tinvest_close - iss_close| / iss_close > config.data_conflict.close_divergence_pct (=0.5) [LAW]
missing_bar      : an expected D1 bar (per MOEX trading calendar) is absent in the lookback window
duplicate_bar    : two distinct bars share the same (instrument_uid, interval, ts) within one source_version pull
```
- The **0.5% divergence threshold** and the **re-check delay** are config, not constants:
  `config.data_conflict.close_divergence_pct = 0.5` [LAW: data truth] and
  `config.data_conflict.recheck_delay_minutes = 30` ([config-and-secrets.md](config-and-secrets.md) §2.9).

### 5.2 State machine (transient → re-check → persistent)
```text
            detect (divergence > 0.5%  OR  missing_bar  OR  duplicate_bar)
                                   |
                                   v
                          [ suspect ]  --- write data_conflicts row (resolved=0); do NOT trade on it yet
                                   |
                   wait config.data_conflict.recheck_delay_minutes (=30)
                                   |
                                   v
                            re-fetch & re-compare
                          /                        \
            divergence gone / bar present            still divergent / still missing / still duplicate
                    |                                              |
                    v                                              v
        [ ok ]  data_status='ok'                         [ data_conflict ]  data_status='data_conflict'
        mark data_conflicts.resolved=1                   data_conflicts.resolved=0  -> SKIP ENTRY (never exit)
```
- **Transient** (divergence/missing clears on re-check) → resolve, `data_status='ok'`, the instrument is eligible
  again that cycle if timing allows.
- **Persistent** (still bad after the delay) → `data_status='data_conflict'`; the signal for that instrument is
  recorded `signals.decision='skipped'`, `reason='data_conflict'` (frozen skip vocab, db-schema §2). **No order
  is created.**
- **Index leg** (no second source) cannot raise `close_divergence`; only `missing_bar` / `duplicate_bar` apply
  (§3.4).

### 5.3 Re-check delay realism
- `recheck_delay_minutes` exists because the two sources publish at slightly different times; a brief delay
  distinguishes a **publish-timing skew** (transient) from a **real data fault** (persistent). The delay is
  bounded so the daily cycle still completes before the next session; if the cycle window closes before a
  persistent re-check resolves, the instrument stays `data_conflict` (fail-safe = skip entry).

### 5.4 `data_conflict` / staleness gates ENTRY only — NEVER an exit [LAW]
- A `data_conflict` or `is_stale` flag **skips a new ENTRY** for that instrument and **must never block a
  protective EXIT** of a held position (frozen-decisions.md, "Strategy, data & backtest honesty" (data truth row), TZ §5, §7.1). The exit path reads the best usable
  last price independently of the cross-check chain.
- Rationale: suspect data must not trap the bot in a position it would otherwise risk-exit. Fail-safe means
  *don't open*, not *don't close*.

## 6. Corporate actions: split back-adjustment & identifier stitching

### 6.1 Split back-adjustment — **PROVISIONAL** [verify: rank-43 empirical probe]
- **Rule (PROVISIONAL):** back-adjust historical OHLC by the split ratio and scale volume inversely; set
  `candles.adjusted = 1`. Source of split ratios = **MOEX ISS** (`/iss/statistics/engines/stock/splits[/{sec}]`,
  `/iss/cci/corp-actions`) — there is **no T-Invest split API** (research `whq6u1gxe`).
- **PROVISIONAL because** it is **not yet known whether T-Invest D1 share candles already arrive
  split-adjusted.** If T-Invest already adjusts, applying the ISS ratio a second time would **double-adjust** and
  corrupt history. Therefore:
  - This clause is marked **PROVISIONAL pending the rank-43 empirical probe** ("is T-Invest D1 already
    split-adjusted?", verified on a known split before any backtest use — M1/M4 [verify]).
  - Until the probe resolves, the data layer **records the split event and the ISS ratio** but **does not assert
    a final adjustment policy**; the `adjusted` flag truthfully records what was actually applied to the stored
    bar, so a later correction is auditable via `source_version`.
- **Do NOT mix sources blindly:** never blend an ISS-adjusted ratio onto a T-Invest bar of unknown adjustment
  state — the probe gates that decision.

### 6.2 Identifier / ticker / uid stitching
- Renames are stitched on **ISIN** via MOEX ISS `…/shares/securities/changeover`; the canonical example **TCSG →
  T (action_date 2024-11-27)** is recorded in `instrument_reference.identifier_history`
  (`[{ticker:'TCSG',uid:..,from:..,to:..},{ticker:'T',uid:..,from:..,to:..}]`).
- A history/lookback query for the current `instrument_uid` must span the pre- and post-rename bars as **one
  continuous series** (the strategy keys on `instrument_uid`; warm-up + MA windows must not break at a rename).
- **[verify] (M4, empirical):** ISS corp-action/split/changeover endpoint coverage and anonymous availability
  per ticker, and whether the T-Invest `instrument_uid` is continuous across the rename (else the post-rename uid
  is re-stitched to the pre-rename history on ISIN). Not asserted settled here.

## 7. Dividend ingestion behavior

- Source = T-Invest `GetDividends`; stored per [db-schema.md](db-schema.md) `dividends` (`last_buy_date`,
  `record_date`, `payment_date`, `declared_date`, `gross_units`/`gross_nano`, `dividend_type`, `currency`).
- **`dividend_net` field = GROSS** per share (mislabeled in the API; no withholding applied) → stored in
  `gross_units`/`gross_nano`. Net (after 13/15% at source) is computed by the **tax layer**, not the data layer
  ([tax-and-dividends.md](tax-and-dividends.md) §5).
- **No `ex_dividend_date`** → derive `ex_date = last_buy_date + 1 trading day` using the **MOEX trading calendar**
  (not calendar +1). The `to` request param filters on `record_date`.
- **Forward-horizon load (live):** load enough forward calendar that any ex-date within
  `config.dividend_gap_block_days + 1` trading days is known **before** the entry decision (feeds the
  dividend-gap entry block, tax contract §6).
- **No-lookahead (backtest):** a dividend may gate a backtest entry only if it was knowable as-of the decision
  day (`declared_date`/`as_of ≤` decision `ts`) — never use a dividend the model could not yet have known
  (tax contract §6).
- **Fail-safe on a missing calendar:** if the dividend calendar is unavailable for a ticker, log it and **fall
  back to skip-entry conservatively** for that ticker on ambiguous days (fail safe, not fail open) — never block
  an exit.

## 8. Liquidity & reference stats (feed eligibility, not stored as float)
- `instrument_reference.avg_turnover_rub` (rounded ₽ INTEGER) and `spread_bps` (INTEGER) are computed at
  ingestion step 9 and feed the per-cycle eligibility filters (`eligibility.min_turnover_rub`,
  `eligibility.max_spread_bps`, [config-and-secrets.md](config-and-secrets.md) §2.4) and the ranking (TZ §6).
- A failing `approved` ticker is `skipped` for the cycle with a frozen reason code
  (`low_liquidity` / `wide_spread` / `lot_too_expensive` / `not_trading` / `data_missing` / `data_conflict`),
  **skip ≠ remove from approved** [LAW] (db-schema §2, frozen-decisions.md, "Strategy, data & backtest honesty" (per-cycle eligibility row)).

## 9. Frozen invariants honored
- **Data truth [LAW]** — primary T-Invest, cross-check MOEX ISS; > 0.5% D1-close divergence OR missing/dup bar →
  `data_conflict` → **skip the signal** (§1, §5); the bot never silently trades suspect data.
- **`data_conflict` / staleness gates ENTRY, never an EXIT [LAW]** — §5.4.
- **`instrument_uid` (not FIGI) is the primary key; ISIN is the stable cross-source stitch** — §2.
- **Managed registry [LAW]** — the data layer never adds a ticker to the trading universe; it only refreshes
  reference data for the configured universe and marks `skipped` (never removes from `approved`) — §8.
- **No-lookahead [LAW]** — `is_complete=1` only on the final close per `close_definition`; signal only on a
  complete bar — §4; dividends gate only if knowable as-of the decision day — §7.
- **No silent overwrite** — every load is a new `source_version`; latest-wins read; older versions retained —
  §3.2.
- **No float money / epoch-ms UTC time** — Quotation `units`/`nano` and INTEGER epoch-ms at every column — §3.3.
- **Index source resolved** — IMOEX + MCFTR via MOEX ISS (`interval=24`), single-source, no cross-check —
  §1, §3.4 (ADR-0005 + research `whq6u1gxe`).

## 10. Open questions / owner-pending
- **[verify] (rank-43 empirical probe — M1/M4):** are T-Invest D1 **share** candles **already split-adjusted**?
  The §6.1 back-adjustment clause is **PROVISIONAL** until this resolves; applying the ISS ratio on top of an
  already-adjusted bar would double-adjust. Verify on a known split before any backtest use.
- **[verify] (empirical — M4):** does a **rename change the T-Invest `instrument_uid`**? The contract treats
  **ISIN as the stable stitch key** (§2, §6.2); confirm uid continuity vs. a re-stitched fresh uid at integration.
- **[owner-ratify]** `close_definition` (`auction_close` vs `d1_candle_after_evening`) — the no-lookahead LAW
  surface; owned by [config-and-secrets.md](config-and-secrets.md) §6a, not asserted here (§4).
- **[verify] (empirical — M1):** does the T-Invest GetCandles D1 `close` include the **evening session**?
  (Snapshot 19:00 vs 23:50.) Prefer `auction_close` until resolved (§4).
- **[verify] (M4):** MOEX ISS corp-action / split / changeover endpoint coverage + anonymous availability per
  ticker (§6.2).
- **[owner-pending]** `daily_run_time` — must be ≥ the final close implied by `close_definition`
  ([config-and-secrets.md](config-and-secrets.md) §2.9, §3.1); a leaky combination is a startup-blocking error.

## 11. Cross-references
- Frozen LAW: `docs/frozen-decisions.md` (data truth, managed registry, no-lookahead, no-float, idempotency).
- Spec: `docs/TZ.md` §5, §5.1, §17, §19, §20. Provenance: **ADR-0005** + research **`whq6u1gxe`**
  (Second Brain References `2026-06-27-tinvest-moex-tax-verify`, `2026-06-27-tinvest-api-grounding`).
- Storage: [db-schema.md](db-schema.md) (`instrument_reference`, `candles`, `dividends`, `data_conflicts`;
  enum/type parity). Config: [config-and-secrets.md](config-and-secrets.md) (`index_source`,
  `data_conflict.*`, `close_definition`, `daily_run_time`, `eligibility.*`). Taxes:
  [tax-and-dividends.md](tax-and-dividends.md) (dividend net/gross, dividend-gap, split source).
- Skills: `backtest-honesty` (no-lookahead, honest data), `broker-api-contract` (T-Invest adapter discipline).
  Auditors: `lookahead-auditor`, `risk-invariant-auditor`.
