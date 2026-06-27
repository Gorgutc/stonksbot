# Getting started

How to activate this kit in a **new or existing project**.

## 1. One-time, per machine

The fan-out orchestrator needs only the standalone Codex CLI:

```bash
npm install -g @openai/codex
codex login            # ChatGPT auth; or: codex login --device-auth
codex login status     # expect: "Logged in using ..."
```

(Optional, for Claude Code cross-review only — not required for orchestration:
`/plugin marketplace add openai/codex-plugin-cc` then `/plugin install codex@openai-codex`.)

## 2. Copy the kit into the project

Copy the **contents** of `agent-kit/` into the project root, so you end up with:

```
your-project/
  AGENTS.md  CLAUDE.md  GEMINI.md  .agent-kit.json
  .codex/  .claude/  tools/  docs/  evals/
  ...your code...
```

On Windows PowerShell, from inside `agent-kit/`:

```powershell
Copy-Item -Recurse -Force -Path .\* -Destination C:\path\to\your-project\
```

If the project is not a git repo yet, run `git init` (the Codex hooks resolve
paths via `git rev-parse`). For a throwaway/non-git dir, set `"skipGitCheck": true`
in your fan-out spec instead.

## 3. Verify the plumbing (no quota spent)

```bash
node tools/check-kit.mjs                            # harness integrity / parity
node tools/codex-orchestrator/fanout.mjs --doctor   # Node + Codex CLI + login
```

## 4. Fill in project specifics

- Edit the **`PROJECT SPECIFICS`** section at the bottom of `AGENTS.md`.
- Configure **`.agent-kit.json`**:
  - `verify.fast` — a cheap check run by the post-edit hook (e.g. `"npm run lint"`).
  - `verify.deep` / `verify.ship` — heavier checks for the orchestrator and pre-PR.
  - `verifyCommand` — back-compat single command (used if `verify.fast` is unset).
  - `verifyPaths` — optional globs (`*`, `**`); the post-edit hook only runs after
    editing a matching file (empty = run on every edit).
  - `broadTaskTriggers` — extra keywords that trigger the fan-out nudge.
  - `profiles`, `evidenceGates`, `forbiddenTerms` — see below (all optional).

## 5. Use it

Open a Codex / Claude Code / Gemini session in the project — instructions,
subagents, and hooks load automatically. Start non-trivial work with the
`session-bootstrap` skill.

Run a fan-out manually:

```bash
node tools/codex-orchestrator/fanout.mjs --spec tools/codex-orchestrator/examples/audit.spec.json
```

…or just tell Claude Code: *"fan this out to Codex agents"* — it will decompose
the task, write a spec, run the orchestrator, and synthesize the results.

## Optional features

### Git gates (pre-commit / pre-push)
Install zero-dependency git hooks that run your verify tiers:

```bash
node tools/install-hooks.mjs          # pre-commit -> verify.fast ; pre-push -> verify.ship/deep + check-kit + evidence-gate
node tools/install-hooks.mjs --uninstall
```

The hooks are ownership-marked (`# managed-by: agent-kit`) and refuse to clobber a
foreign hook. (Prefer `lefthook`? Rename `lefthook.yml.example` and run
`lefthook install`.)

### Evidence gates
Enforce "you changed a risky surface, so produce evidence" before shipping. In
`.agent-kit.json`:

```json
"evidenceGates": [
  { "changed": ["src/ui/**", "**/*.css"], "requires": ["tests/visual/latest.json"], "note": "UI changed -> attach visual-QA result" }
]
```

Run `node tools/evidence-gate.mjs` (also wired into the pre-push hook). It fails
closed if a `changed` glob matched but a `requires` file is missing/empty.

### Component profiles (multi-component repos)
Declare each component (backend / addon / desktop / …) as a profile so one harness
can pre-stage them without prematurely pulling in toolchains. See the
"Component profiles" section of `AGENTS.md` and `docs/profiles/_TEMPLATE.md`.

### Stack guardrails (what NOT to port)
Because the kit is copied between projects, set `forbiddenTerms` to catch rules
inherited from another stack:

```json
"forbiddenTerms": ["Next.js", "Tailwind", "Playwright"]
```

`node tools/check-kit.mjs` then fails (with file:line) if any appear in active
instruction/skill files. Pair this with a one-line "What NOT to port" note in
`AGENTS.md` → `PROJECT SPECIFICS`.

## Monorepos / nested instructions

`AGENTS.md` is the root source of truth. For sub-projects, compose rather than
restate:

- A sub-dir `CLAUDE.md` that is just `@AGENTS.md` re-uses the parent rules.
- A sub-dir `AGENTS.md` should be an **override-only delta** (only what differs
  from the root), wrapped in `<!-- BEGIN:<id> --> … <!-- END:<id> -->` markers if a
  script manages the block. Keep it to a few lines.
- Vendored / reference sub-trees: fence them as **read-only** in the root
  `PROJECT SPECIFICS` so agents don't edit code they don't own.

## Notes

- **Multi-harness:** `AGENTS.md` is canonical. `CLAUDE.md` imports it and adds
  Claude specifics; `GEMINI.md` is a redirect stub. Additional harnesses get a stub
  only — never a duplicated rule set or per-harness skill mirror.
- **Read-only** fan-out is safe wide. For **parallel edits**, give each unit its own
  git worktree (per-unit `cwd`) — see `tools/codex-orchestrator/README.md`.
- Keep `.codex/` and `.claude/` consistent **by hand**; `node tools/check-kit.mjs`
  catches mirror drift, missing hooks, broken stubs, bad profiles, and forbidden terms.
- `evals/` holds copy-paste behavior smoke tests for the harness.
- `docs/IMPROVEMENT-ROADMAP.md` tracks upgrades distilled from mature sibling harnesses.
