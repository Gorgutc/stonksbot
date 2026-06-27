---
name: instruction-drift
description: Use to audit the agent harness itself (AGENTS.md, CLAUDE.md, GEMINI.md, skills, subagent contracts, hooks, .agent-kit.json) for staleness and contradictions — commands that no longer exist, stale counts/claims, rules that disagree across harnesses, or instructions that drifted from the real code.
---

# Instruction drift audit

Read-only audit of the harness, not the product code.

Check for:

- Commands referenced in instructions that no longer exist (verify against
  `package.json` / `.agent-kit.json` / the real scripts).
- Hard-coded stale facts (counts, pass-totals, dates, versions) that no longer hold.
- Contradictions ACROSS harness surfaces (`AGENTS.md` vs `CLAUDE.md` vs `GEMINI.md`
  vs skills vs hooks).
- Mirror drift: each `.codex/agents/*.toml` has a matching `.claude/agents/*.md`;
  `tools:` / `sandbox_mode` agree. Run `node tools/check-kit.mjs` for the
  structural part.
- Instructions that contradict the current code/tests — the code wins.

Classify each finding as **KEEP / REWRITE / ARCHIVE / USER-DECISION** with
file:line evidence. Do not edit; report.
