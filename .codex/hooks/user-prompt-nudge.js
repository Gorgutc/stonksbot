// Shared by Codex and Claude Code. Nudges toward the fan-out orchestrator when
// the prompt looks like broad / parallel / multi-file work. Projects can extend
// the trigger list via `broadTaskTriggers` in .agent-kit.json.
const { execSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const BUILTIN_TRIGGERS = [
  "audit", "refactor", "review", "dead code", "optimize", "migrate", "across",
  "all files", "everywhere", "whole", "entire", "codebase", "in parallel",
  "аудит", "рефактор", "ревью", "оптимиз", "миграц", "по всему", "везде",
  "весь", "целиком", "все файлы", "параллел"
];

function projectRoot() {
  try {
    return execSync("git rev-parse --show-toplevel", { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return process.cwd();
  }
}

function loadExtraTriggers() {
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(projectRoot(), ".agent-kit.json"), "utf8"));
    if (Array.isArray(cfg.broadTaskTriggers)) {
      return cfg.broadTaskTriggers.filter((t) => typeof t === "string" && t.trim()).map((t) => t.toLowerCase());
    }
  } catch {
    /* no config */
  }
  return [];
}

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
});
process.stdin.on("end", () => {
  let prompt = "";
  try {
    const parsed = JSON.parse(input || "{}");
    prompt = parsed.prompt || parsed.user_prompt || "";
  } catch {
    prompt = input || "";
  }

  const lower = prompt.toLowerCase();
  const triggers = BUILTIN_TRIGGERS.concat(loadExtraTriggers());
  const broad = triggers.some((word) => lower.includes(word));

  if (!broad) return;

  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "UserPromptSubmit",
        additionalContext:
          "[agent-kit] Broad/parallel task detected. Consider the fan-out orchestrator (tools/codex-orchestrator/fanout.mjs): decompose into read-only units and run many Codex agents at once. See AGENTS.md."
      }
    })
  );
});
