---
name: fanout-orchestrator
description: Use when a task is broad, parallelizable, or multi-file and would benefit from many Codex agents at once — audits, dead-code/duplication sweeps, multi-file reviews, codebase-wide research, or any "do this across everything" request. Explains how to orchestrate parallel codex exec runs from Claude Code.
---

# Fan-out orchestrator

Act as the **orchestrator**: decompose the task, dispatch independent units to
many parallel `codex exec` agents (running on the user's Codex/ChatGPT plan via
`tools/codex-orchestrator/fanout.mjs`), then synthesize and verify the results.
Orchestration is on the Claude/Anthropic side; execution is on the Codex plan —
two quota pools at once.

## When to use this

- Broad audits, dead-code / duplication search, optimization planning.
- Multi-file reviews where each file or area is an independent unit.
- Research that sweeps several parts of the codebase simultaneously.
- Any "across the whole repo / everywhere / all files" request.

For a single focused edit or a one-shot question, do it yourself or use one
`codex exec` — don't spin up the harness.

## How to run a fan-out

1. **Check prerequisites once** (no quota):
   ```bash
   node tools/codex-orchestrator/fanout.mjs --doctor
   ```
   If it reports not-ready, surface the exact step it prints (install / login).

2. **Decompose** the task into independent units — one self-contained prompt
   each. Each prompt must stand alone (the Codex agent has none of this
   conversation's context): name the files/areas, state "read-only, analysis
   only", and ask for evidence (file:line).

3. **Write a spec** and run it. Generate the spec file yourself, then:
   ```bash
   node tools/codex-orchestrator/fanout.mjs --spec <spec.json>
   ```
   Spec shape (full reference in `tools/codex-orchestrator/README.md`):
   ```json
   {
     "concurrency": 4,
     "sandbox": "read-only",
     "units": [
       { "id": "dead-code", "prompt": "..." },
       { "id": "deps-risk", "prompt": "..." }
     ]
   }
   ```

4. **Read `runs/<runId>/summary.md`** and the per-unit `<id>.final.md` files.
   Then **synthesize** one coherent answer and **verify** the key claims — the
   orchestration is not finished until findings are checked. Spot-check
   suspicious findings yourself rather than relaying them blindly.

## Rules

- **Default `sandbox: "read-only"`.** Read-only units are safe to fan out wide;
  they can't edit the tree, so there are no conflicts.
- **Parallel writes need isolation.** Only use `sandbox: "workspace-write"` when
  each unit has its **own git worktree** (per-unit `cwd`); otherwise parallel
  writers clobber each other. Create the worktrees, fan out, then merge.
- **Concurrency is capped by the Codex plan's rate limits**, not the script.
  Start at 4–6; raise empirically. If many units 429, lower it.
- **No silent caps.** If you limit coverage (top-N files, sampling), say so in
  the synthesis.

## Example specs

- `tools/codex-orchestrator/examples/audit.spec.json` — 4-unit read-only audit.
- `tools/codex-orchestrator/examples/smoke.spec.json` — connectivity check.
