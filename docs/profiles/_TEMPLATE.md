# Profile: &lt;name&gt;  (status: active | dormant)

Copy this to `docs/profiles/<name>.md` and declare it in `.agent-kit.json`:

```json
"profiles": [
  { "name": "<name>", "status": "dormant", "doc": "docs/profiles/<name>.md" }
]
```

Profiles let ONE harness pre-declare multiple component types (e.g. `research`,
`broker-adapter`, `dashboard`) without dragging in their toolchains prematurely.
The `component-guardian` subagent enforces the status rule below.

## Scope
What component this profile governs.

## Status rule
- **dormant** — the rules exist, but NO toolchain / dependency / build command for
  this component may be introduced until an explicit request flips status to `active`.
- **active** — the component is being built; the decisions below are in force.

## Active toolchain (when active)
Languages, build/test commands, packaging. Leave empty while dormant.

## Decision checklist (fill when activated)
- [ ] stack chosen and recorded here
- [ ] build + verify commands set in `.agent-kit.json`
- [ ] packaging / output path defined

## Explicit defers
Things intentionally NOT in scope for this profile yet.

## Verification
The exact command(s) that gate this component.
