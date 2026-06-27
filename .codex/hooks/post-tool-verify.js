// Shared by Codex and Claude Code. After a code edit, optionally run the
// project's fast verify command from .agent-kit.json. Two upgrades over a naive
// runner: (1) path-scoping via `verifyPaths` globs so it stays silent on
// irrelevant edits, and (2) a hard exit(2) on failure so the agent is blocked,
// not merely warned. No-op when no verify command is configured, so the kit is
// safe to drop into any project before you've chosen one.
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

// Minimal glob -> RegExp. Supports `*` (not across /) and `**` (across /),
// plus the common `**/` "any leading dirs (optional)" idiom.
function globToRegExp(glob) {
  let re = "^";
  for (let i = 0; i < glob.length; ) {
    if (glob[i] === "*" && glob[i + 1] === "*") {
      if (glob[i + 2] === "/") {
        re += "(?:.*/)?";
        i += 3;
      } else {
        re += ".*";
        i += 2;
      }
    } else if (glob[i] === "*") {
      re += "[^/]*";
      i += 1;
    } else if ("\\^$+?.()|[]{}".includes(glob[i])) {
      re += "\\" + glob[i];
      i += 1;
    } else {
      re += glob[i];
      i += 1;
    }
  }
  return new RegExp(re + "$");
}

function resolveVerifyCommand(cfg) {
  if (cfg.verify && typeof cfg.verify.fast === "string" && cfg.verify.fast.trim()) {
    return cfg.verify.fast;
  }
  if (typeof cfg.verifyCommand === "string" && cfg.verifyCommand.trim()) {
    return cfg.verifyCommand;
  }
  return null;
}

function editedRelPath(payload, root) {
  const raw =
    (payload.tool_input && (payload.tool_input.file_path || payload.tool_input.path)) || "";
  if (!raw) return "";
  let p = String(raw).replace(/\\/g, "/");
  const rootSlash = root.replace(/\\/g, "/").replace(/\/$/, "");
  if (p.startsWith(rootSlash + "/")) p = p.slice(rootSlash.length + 1);
  return p.replace(/^\.\//, "");
}

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
});
process.stdin.on("end", () => {
  let payload = {};
  try {
    payload = JSON.parse(input || "{}");
  } catch {
    payload = {};
  }

  const root = projectRoot();

  let cfg = {};
  try {
    cfg = JSON.parse(fs.readFileSync(path.join(root, ".agent-kit.json"), "utf8"));
  } catch {
    return; // no config -> nothing to run
  }

  const cmd = resolveVerifyCommand(cfg);
  if (!cmd) return; // disabled

  // Path-scoping: when verifyPaths is set, only run if the edited file matches.
  const paths = Array.isArray(cfg.verifyPaths) ? cfg.verifyPaths.filter((g) => typeof g === "string" && g.trim()) : [];
  if (paths.length) {
    const rel = editedRelPath(payload, root);
    if (rel) {
      const matched = paths.some((g) => globToRegExp(g).test(rel));
      if (!matched) return; // out of scope -> skip
    }
    // if we couldn't determine the edited path, fall through and run (safe default)
  }

  try {
    const output = execSync(cmd, { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
    const lastLine = (output.trim().split(/\r?\n/).pop() || "ok").trim();
    process.stdout.write(`verify passed (${cmd}): ${lastLine}\n`);
  } catch (error) {
    const out = `${error.stdout || ""}\n${error.stderr || ""}`.trim();
    process.stderr.write(`verify failed: ${cmd}\n`);
    process.stderr.write(out.split(/\r?\n/).slice(-40).join("\n"));
    process.stderr.write("\nFix the regression or revert the edit before continuing.\n");
    process.exit(2);
  }
});
