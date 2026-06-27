---
name: risk-policy-guardian
description: Use before touching anything that sizes a position, builds an order, changes a limit, or changes account/mode handling — enforce the frozen risk invariants so the bot can never market-order, margin, short, trade the wrong account, or let a kill switch dump the portfolio.
---

# Risk policy guardian

The frozen risk invariants in `docs/frozen-decisions.md` are LAW. Before editing
sizing / order construction / limits / account or mode handling, check the change
against them. If a change would violate one, STOP and confirm with the owner.

Hard rules (never silently relax):

1. **Order types:** limit orders only. No market orders. No margin, no shorts —
   long-only.
2. **Account:** trade only the configured `account_id`. Refuse to start trading if
   the id is missing / ambiguous; show account name+id on startup; manual confirm on
   change. Never reach into the main account.
3. **Mode:** first live mode is `confirm` (human approves the entry); `auto_small`
   stays disabled in the MVP.
4. **Portfolio limits (pilot):** 10 000 ₽; max 1 open position; ≤ 3 000 ₽ / 30% per
   position; 50% cash reserve; ≤ 1 new proposal/day.
5. **Exits are automated** (daily hard stop 100 ₽, hard stop ~4%, trend-break,
   target_then_trailing 6%/3%, time exit) — but `kill` only stops the bot and cancels
   active orders; it must **never sell positions**.
6. **Universe:** the bot may not add tickers to the trading universe itself; status
   is `approved` / `managed_only` / `watch_only` / `blocked` / `pending`.
7. **Design axis = expected profit per trade after costs**, not trade frequency.
8. **No model in the decision path.** No LLM / Codex / Claude / ML call may decide
   buy/sell/quantity or gate an entry — trading actions come only from deterministic
   strategy + risk rules. LLMs build, review, and document; they never trade.

If a change is correct but touches a frozen item, follow the `frozen-decisions`
skill (update the doc + its guard together, with an ADR). When in doubt, prefer the
more conservative behavior and ask.
