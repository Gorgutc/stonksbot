#!/usr/bin/env node
// Self-installing git hooks (zero dependencies). Writes pre-commit and pre-push
// hooks that call tools/git-gate.mjs. Ownership-marked so it never clobbers a
// foreign hook; worktree-aware; idempotent. Run:
//   node tools/install-hooks.mjs            # install / adopt
//   node tools/install-hooks.mjs --uninstall
//   node tools/install-hooks.mjs --force    # overwrite even unmanaged hooks

import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const MARKER = "# managed-by: agent-kit";
const STAGES = ["pre-commit", "pre-push"];

function git(cmd) {
  return execSync(cmd, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
}

let root;
let hooksDir;
try {
  root = git("git rev-parse --show-toplevel");
  const hp = git("git rev-parse --git-path hooks");
  hooksDir = path.isAbsolute(hp) ? hp : path.join(process.cwd(), hp);
} catch {
  process.stderr.write("install-hooks: not inside a git repository. Run `git init` first.\n");
  process.exit(1);
}

const uninstall = process.argv.includes("--uninstall");
const force = process.argv.includes("--force");

function hookBody(stage) {
  return `#!/bin/sh\n${MARKER}\nexec node "$(git rev-parse --show-toplevel)/tools/git-gate.mjs" ${stage}\n`;
}

fs.mkdirSync(hooksDir, { recursive: true });

for (const stage of STAGES) {
  const file = path.join(hooksDir, stage);
  const existing = fs.existsSync(file) ? fs.readFileSync(file, "utf8") : null;
  const managed = existing && existing.includes(MARKER);

  if (uninstall) {
    if (managed) {
      fs.rmSync(file);
      process.stdout.write(`removed ${stage}\n`);
    } else if (existing) {
      process.stdout.write(`skipped ${stage} (not managed by agent-kit)\n`);
    }
    continue;
  }

  if (existing && !managed && !force) {
    process.stderr.write(`refusing to overwrite unmanaged ${stage} hook (use --force to replace)\n`);
    continue;
  }

  fs.writeFileSync(file, hookBody(stage));
  try {
    fs.chmodSync(file, 0o755);
  } catch {
    /* chmod is a no-op on some platforms */
  }
  process.stdout.write(`installed ${stage} -> tools/git-gate.mjs\n`);
}

void root;
