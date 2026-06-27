# Codex fan-out orchestrator

A dependency-free orchestrator (`fanout.mjs`, Node built-ins only) that fans a job
out to many parallel `codex exec` turns. You (Claude Code, or a human) act as the
**orchestrator** — decompose, synthesize, verify — while the Codex turns run on your
Codex/ChatGPT plan. Two quota pools at once.

It depends only on the standalone Codex CLI, **not** on any Claude Code / Codex
plugin:

```bash
npm install -g @openai/codex
codex login            # ChatGPT auth; or: codex login --device-auth
```

## Usage

```bash
node tools/codex-orchestrator/fanout.mjs --doctor                 # check prerequisites (no quota)
node tools/codex-orchestrator/fanout.mjs --spec <spec.json>       # run a fan-out
node tools/codex-orchestrator/fanout.mjs --spec <spec.json> --out <dir>      # results into <dir>
node tools/codex-orchestrator/fanout.mjs --spec <spec.json> --run-id <id>    # fixed run id
node tools/codex-orchestrator/fanout.mjs --help
```

`--doctor` verifies Node + the Codex CLI + login status and prints next steps if
anything is missing. Run it once before spending any quota.

## `spec.json` shape

All top-level fields are optional **except `units`** (a non-empty array). Per-unit
fields override the top-level defaults.

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `concurrency` | int | `4` | max parallel `codex exec` processes |
| `sandbox` | string | `read-only` | `read-only` \| `workspace-write` \| `danger-full-access` |
| `model` | string | Codex default | e.g. `gpt-5.3-codex`; omit to use the Codex default |
| `effort` | string | — | reasoning effort (`low` \| `medium` \| `high`) |
| `cwd` | string | `process.cwd()` | working root for every unit |
| `timeoutMs` | int | `900000` (15 min) | per-unit hard timeout |
| `skipGitCheck` | bool | `false` | pass `--skip-git-repo-check` (for non-git dirs) |
| `units` | array | — (**required**) | the work items (see below) |

Each **unit**:

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `id` | string | yes | unique, matches `[A-Za-z0-9._-]`; names the output files |
| `prompt` | string | yes | the standalone prompt for that Codex turn |
| `model` / `effort` / `cwd` / `sandbox` / `timeoutMs` | — | no | per-unit override of the top-level value |

Example (read-only audit; see `examples/audit.spec.json`):

```json
{
  "concurrency": 4,
  "sandbox": "read-only",
  "timeoutMs": 900000,
  "units": [
    { "id": "dead-code", "prompt": "You are a read-only auditor. Find dead/unused/duplicated code..." },
    { "id": "deps-risk", "prompt": "You are a read-only auditor. Report outdated/risky/unused deps..." }
  ]
}
```

`examples/smoke.spec.json` is a single-unit connectivity check (`concurrency: 1`,
`skipGitCheck: true`) — useful to confirm the plumbing before a real run.

## Sandbox modes

- **`read-only` (default).** Units fan out wide safely — no working-tree conflicts.
  Use this for audits, reviews, and codebase-wide research.
- **`workspace-write`** (and `danger-full-access`) let units edit files. Parallel
  writers in the **same `cwd` clobber each other** — give each writing unit its own
  git worktree via a per-unit `cwd`, then merge the worktrees afterward. The script
  logs a warning whenever `sandbox` is not `read-only`.

Concurrency is capped by your Codex plan's rate limits, not the script. Start at
4–6 and raise empirically.

## Output

Results land under `<out>/<runId>/` (default `tools/codex-orchestrator/runs/<runId>/`;
`runId` defaults to a local timestamp). Per run:

- `<id>.final.md` — the unit's final agent message.
- `<id>.log` — the full stdout+stderr stream for that unit.
- `summary.json` — machine-readable results (sandbox, model, per-unit exit codes).
- `summary.md` — human-readable digest with a status line and preview per unit.

The process exit code is `0` only if **every** unit exited `0`, else `1`.
After a run, read `summary.md`, then **synthesize and verify** — the orchestration
is not done until the findings are checked.
