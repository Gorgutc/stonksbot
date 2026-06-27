#!/usr/bin/env node
// Diff-scoped, fail-closed evidence gate (zero dependencies).
//
// Reads `evidenceGates` from .agent-kit.json:
//   "evidenceGates": [
//     { "changed": ["src/ui/**", "**/*.css"], "requires": ["tests/visual/latest.json"], "note": "UI changed -> attach a visual-QA result" }
//   ]
// For each gate: if any changed file matches a `changed` glob, every `requires`
// file must exist and be non-empty. Fails closed (exit 1) listing what's missing.
// No gates configured -> pass. Run before ship, in CI, or from a pre-push hook.
//
// Changed files = union of unstaged + staged + untracked, plus optionally
// `--base <ref>` (files changed since <ref>).

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

const ROOT = projectRoot();

function gitLines(cmd) {
  try {
    return execSync(cmd, { cwd: ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] })
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter(Boolean);
  } catch {
    return [];
  }
}

function changedFiles(base) {
  const set = new Set();
  for (const f of gitLines("git diff --name-only")) set.add(f);
  for (const f of gitLines("git diff --name-only --cached")) set.add(f);
  for (const f of gitLines("git ls-files --others --exclude-standard")) set.add(f);
  if (base) for (const f of gitLines(`git diff --name-only ${base}...HEAD`)) set.add(f);
  return [...set];
}

function main() {
  const baseIdx = process.argv.indexOf("--base");
  const base = baseIdx >= 0 ? process.argv[baseIdx + 1] : null;

  let cfg = {};
  try {
    cfg = JSON.parse(fs.readFileSync(path.join(ROOT, ".agent-kit.json"), "utf8"));
  } catch {
    /* no config */
  }
  const gates = Array.isArray(cfg.evidenceGates) ? cfg.evidenceGates : [];
  if (!gates.length) {
    process.stdout.write("evidence-gate: no gates configured — pass.\n");
    return 0;
  }

  const changed = changedFiles(base);
  const problems = [];

  gates.forEach((gate, i) => {
    const changedGlobs = (gate.changed || []).map(globToRegExp);
    const matched = changed.filter((f) => changedGlobs.some((re) => re.test(f)));
    if (!matched.length) return; // gate not triggered
    const missing = (gate.requires || []).filter((req) => {
      try {
        return fs.statSync(path.join(ROOT, req)).size === 0;
      } catch {
        return true; // absent
      }
    });
    if (missing.length) {
      problems.push({
        gate: gate.note || `gate #${i + 1}`,
        triggeredBy: matched.slice(0, 5),
        missing
      });
    }
  });

  if (!problems.length) {
    process.stdout.write(`evidence-gate: ${gates.length} gate(s) checked, all satisfied.\n`);
    return 0;
  }

  process.stderr.write("evidence-gate: FAIL (required evidence missing)\n");
  for (const p of problems) {
    process.stderr.write(`- ${p.gate}\n`);
    process.stderr.write(`    triggered by: ${p.triggeredBy.join(", ")}\n`);
    process.stderr.write(`    missing/empty: ${p.missing.join(", ")}\n`);
  }
  process.stderr.write("Produce the missing evidence (e.g. run the relevant check) before shipping.\n");
  return 1;
}

process.exitCode = main();
