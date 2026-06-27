// Shared by Codex (.codex/hooks.json) and Claude Code (.claude/settings.json).
// Emits session context: project name, verify command(s), active/dormant
// component profiles, the fan-out orchestrator, and the subagents/skills.
const { execSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

function projectRoot() {
  try {
    return execSync("git rev-parse --show-toplevel", { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return process.cwd();
  }
}

const root = projectRoot();
let name = "this project";
let verifyHint = "";
let profileHint = "";
try {
  const cfg = JSON.parse(fs.readFileSync(path.join(root, ".agent-kit.json"), "utf8"));
  if (cfg && typeof cfg.name === "string" && cfg.name && !/^todo/i.test(cfg.name)) {
    name = cfg.name;
  }
  const fast = cfg.verify && cfg.verify.fast ? cfg.verify.fast : cfg.verifyCommand;
  const deep = cfg.verify && (cfg.verify.deep || cfg.verify.ship);
  if (fast || deep) {
    verifyHint = ` Verify: fast='${fast || "(unset)"}'${deep ? `, deep='${deep}'` : ""}.`;
  }
  if (Array.isArray(cfg.profiles) && cfg.profiles.length) {
    profileHint = ` Component profiles: ${cfg.profiles.map((p) => `${p.name}(${p.status})`).join(", ")} - do not introduce toolchain for dormant profiles.`;
  }
} catch {
  /* no config; use defaults */
}

const context = [
  `Project: ${name}. Primary instructions: AGENTS.md (single source of truth for Codex + Claude Code + Gemini).${verifyHint}${profileHint}`,
  "Before substantial work use the session-bootstrap skill. Fan-out orchestrator at tools/codex-orchestrator/fanout.mjs: decompose broad/parallel work into a spec.json and run many Codex agents at once (read-only by default; `--doctor` checks prerequisites).",
  "Read-only subagents in .codex/agents/*.toml (.claude/agents/*.md mirrors): explorer, code_reviewer, dead_code_auditor, researcher, instruction_drift_auditor, verification_reviewer, component_guardian. Skills in .claude/skills: fanout-orchestrator, session-bootstrap, context-keeper, frozen-decisions, instruction-drift.",
  "Run `node tools/check-kit.mjs` to self-check the harness."
].join(" ");

process.stdout.write(
  JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext: context
    }
  })
);
