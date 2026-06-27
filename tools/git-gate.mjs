#!/usr/bin/env node
// Git-hook dispatcher (zero dependencies). Called by the hooks that
// tools/install-hooks.mjs writes. Usage:
//   node tools/git-gate.mjs pre-commit   # runs verify.fast
//   node tools/git-gate.mjs pre-push     # runs verify.ship||deep + check-kit + evidence-gate
// Every step is optional/no-op when not configured. Exits non-zero on failure.

import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

function projectRoot() {
  try {
    return execSync("git rev-parse --show-toplevel", { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return process.cwd();
  }
}

const ROOT = projectRoot();
const stage = process.argv[2] || "pre-commit";

let cfg = {};
try {
  cfg = JSON.parse(fs.readFileSync(path.join(ROOT, ".agent-kit.json"), "utf8"));
} catch {
  /* no config */
}
const v = cfg.verify || {};

function run(cmd) {
  process.stdout.write(`[git-gate:${stage}] ${cmd}\n`);
  execSync(cmd, { cwd: ROOT, stdio: "inherit" });
}
function node(script, args = "") {
  run(`node ${JSON.stringify(path.join(ROOT, script))}${args ? " " + args : ""}`);
}

try {
  if (stage === "pre-commit") {
    const cmd = v.fast || cfg.verifyCommand;
    if (cmd) run(cmd);
  } else if (stage === "pre-push") {
    const cmd = v.ship || v.deep || v.fast || cfg.verifyCommand;
    if (cmd) run(cmd);
    node("tools/check-kit.mjs");
    node("tools/evidence-gate.mjs");
  } else {
    process.stderr.write(`[git-gate] unknown stage: ${stage}\n`);
    process.exit(2);
  }
  process.exit(0);
} catch (err) {
  process.stderr.write(`[git-gate:${stage}] FAILED — commit/push blocked.\n`);
  process.exit(typeof err.status === "number" ? err.status : 1);
}
