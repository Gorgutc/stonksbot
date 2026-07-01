# Pre-live owner decisions — triage sheet

> **Purpose.** One place that enumerates every decision only the owner can make,
> with the milestone it binds to and what it blocks. Agents formulate questions and
> record context here; **they never resolve these silently** (frozen-decisions law).
> When the owner decides: record the decision in `docs/frozen-decisions.md` (if it
> freezes a value) and/or a Second Brain `Decisions/ADR-*`, then update this sheet.
>
> Status legend: `OPEN` (undecided) · `RECOMMENDED` (agents proposed, owner sign-off
> pending) · `DECIDED (ref)`.

## 1. Milestone-pinned decisions (from TZ / Improvements)

| # | Decision | Binds at | Blocks | Status / context |
| --- | --- | --- | --- | --- |
| 1.1 | **Binding tariff** (investor vs trader; 390 ₽/mo component) | M3 | M3 cost-sensitivity verdict; per-tariff PASS/WEAK/FAIL | OPEN — model BOTH tariffs until M3 evidence exists (`docs/contracts/backtest.md`); decided by cost-sensitivity, not preference. |
| 1.2 | **Token / account feasibility** — can a read-only + account-scoped token pair be issued for the dedicated bot account (product type)? | M4 (empirical) | T-Invest adapter (read-only market-data leg earlier, if activated); startup scope check | OPEN — not an M0/M1 blocker (fail-closed stub in place); requires the real bot account. Read-only market-data token is the ONLY token M1 may ever see (`AGENTS.md`, secrets policy). |
| 1.3 | **Secret-storage backend** (Windows local vs VPS store; env/.env is interim) | M6 (before live) | Live deployment | OPEN — `.env` placeholders remain until then (`docs/contracts/config-and-secrets.md`). |
| 1.4 | **Telegram whitelist user ids** | M5 | Telegram control plane | OPEN. |
| 1.5 | **`max_holding_days`** — pin from grid {20, 40} | M3 (after evidence) | M3 verdict; M4 config | OPEN — grid until walk-forward evidence. |
| 1.6 | **Trend/time exits under `blocked_reconciliation_mismatch`** | M4 | Reconciliation semantics | OPEN — risk exits allowed, profit/target exits forbidden until decided (`docs/contracts/reconciliation.md`). |

## 2. Decisions surfaced by the M1 data-layer designs (2026-07-01)

These arise from implementing the universe registry / ISS bridge / session policy.
Implementations below ship with the RECOMMENDED option **explicitly marked provisional**
and reversible; owner sign-off converts them to DECIDED (ADR).

| # | Decision | Recommendation (provisional) | Why it is owner-zone |
| --- | --- | --- | --- |
| 2.1 | **Pre-T-Invest `instrument_uid` scheme** for rows created before the adapter exists | Namespaced synthetic uid `moex_iss:{kind}:{SECID}` (e.g. `moex_iss:index:IMOEX`); permanent for indices (no T-Invest leg exists for index candles), placeholder for shares re-stitched by ISIN via `identifier_history` when the T-Invest reference refresh lands | Touches the frozen identity law's implementation (`data-layer.md` §2); needs an ADR |
| 2.2 | **Audit actor** for config-materialized registry changes | `system`, with `detail` JSON naming the config file/list (owner authored the config; the process applying it is the system) | `universe-eligibility.md` §6 pins only `owner:<id>` / `system` |
| 2.3 | **`source_version` allocation** | Per-ingest-run `1 + MAX(source_version)` per `(instrument_uid, interval)` | Contract says only "new load = new version" |
| 2.4 | **Index-leg completeness rule** (no T-Invest close source for ISS index bars) | `is_complete = ts < complete_before_ts` — the current session's bar is never marked complete | `close_definition` is defined against T-Invest sources; single-source leg needs its own conservative rule |
| 2.5 | **Registry orphans** (DB share row absent from every config list) | Never delete/demote; surface as drift for owner attention | Managed-registry law: bot may never demote |
| 2.6 | **Session-policy: same-day absence semantics** — calendar fetched through today, today's IMOEX bar absent at 19:05: quiet holiday-skip vs loud `calendar_stale` alert | Loud `calendar_stale` (fail-closed alert) until the ISS same-day publication timing `[verify]` is resolved; a false skip only delays ingest, never a missed exit | Distinguishes "holiday" from "data problem"; affects daily-cycle behavior |
| 2.7 | **Interim trading-calendar source in the contract** — ratify the IMOEX-candle-derived calendar as the implemented interim producer (dedicated ISS calendar endpoint stays `[verify]`) | Ratify as interim; revisit if/when the ISS calendar endpoint is verified | `session-policy.md` §5 header requires owner ack for contract edits |

## 3. Open `[verify]` items feeding the above (empirical, not decisions)

- ISS same-day IMOEX D1 bar availability at ~19:05 MSK (feeds 2.6).
- Does MOEX ISS date index bars on weekend/irregular sessions? (calendar arithmetic).
- Does T-Invest GetCandles D1 close include the evening session? (informational under `auction_close`).
- Are T-Invest D1 share candles split-adjusted?
- Does T-Invest `instrument_uid` change on instrument rename? (stitch by ISIN until verified).
- Exact MOEX ISS trading-calendar endpoint + anonymous availability.

## 4. How to answer

Reply per item ("2.1: approve" / "2.1: use X instead"). Agents will then update the
relevant contract + `docs/frozen-decisions.md` (same-change rule) and record an ADR in
the Second Brain `Decisions/` folder.
