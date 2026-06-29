#!/usr/bin/env node
// Zero-dependency regression tests for the agent-kit gate scripts.

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SCAN_SCRIPT = path.join(ROOT, "tools", "secret-scan.mjs");
const EVIDENCE_GATE = path.join(ROOT, "tools", "evidence-gate.mjs");
const NODE = process.execPath;
let failed = 0;

function run(cmd, args, opts = {}) {
  return spawnSync(cmd, args, {
    cwd: opts.cwd || ROOT,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, GIT_CONFIG_GLOBAL: path.join(os.tmpdir(), "stonksbot-test-empty-gitconfig") },
  });
}

function assert(condition, message, details = "") {
  if (condition) {
    process.stdout.write(`PASS  ${message}\n`);
    return;
  }
  failed += 1;
  process.stderr.write(`FAIL  ${message}\n${details}`);
}

function tempDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function write(file, content) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, content);
}

function initRepo() {
  const dir = tempDir("stonksbot-gate-test-");
  for (const args of [
    ["init", "-b", "main"],
    ["config", "user.email", "agent@example.invalid"],
    ["config", "user.name", "Agent Test"],
  ]) {
    const res = run("git", args, { cwd: dir });
    assert(res.status === 0, `git ${args.join(" ")} in temp repo`, res.stderr || res.stdout);
  }
  return dir;
}

function testSecretScanOutsideGitFailsClosed() {
  const dir = tempDir("stonksbot-no-git-");
  const res = run(NODE, [SCAN_SCRIPT, "--all"], { cwd: dir });
  assert(
    res.status !== 0 && /failing closed/i.test(res.stderr),
    "secret-scan fails closed outside a git repository",
    `status=${res.status}\nstdout=${res.stdout}\nstderr=${res.stderr}\n`,
  );
}

function testSecretScanBlocksToken() {
  const dir = initRepo();
  write(path.join(dir, "leak.txt"), "TINVEST_" + "TOKEN_LIVE_CONFIRM=t." + "A".repeat(80) + "\n");
  run("git", ["add", "leak.txt"], { cwd: dir });
  const res = run(NODE, [SCAN_SCRIPT], { cwd: dir });
  assert(
    res.status === 1 && /SUSPECTED SECRET/.test(res.stderr),
    "secret-scan blocks staged real-looking tokens",
    `status=${res.status}\nstdout=${res.stdout}\nstderr=${res.stderr}\n`,
  );
}

function testSecretScanAllowsEnvExamplePlaceholder() {
  const dir = initRepo();
  write(path.join(dir, ".env.example"), "TINVEST_" + "TOKEN_LIVE_CONFIRM=<your-token>\n");
  run("git", ["add", ".env.example"], { cwd: dir });
  const res = run(NODE, [SCAN_SCRIPT], { cwd: dir });
  assert(
    res.status === 0 && /clean/.test(res.stdout),
    "secret-scan allows .env.example placeholders",
    `status=${res.status}\nstdout=${res.stdout}\nstderr=${res.stderr}\n`,
  );
}

function testSecretScanBlocksEnvExampleToken() {
  const dir = initRepo();
  write(path.join(dir, ".env.example"), "TINVEST_" + "TOKEN_LIVE_CONFIRM=t." + "B".repeat(80) + "\n");
  run("git", ["add", ".env.example"], { cwd: dir });
  const res = run(NODE, [SCAN_SCRIPT], { cwd: dir });
  assert(
    res.status === 1 && /SUSPECTED SECRET/.test(res.stderr),
    "secret-scan blocks real tokens in .env.example",
    `status=${res.status}\nstdout=${res.stdout}\nstderr=${res.stderr}\n`,
  );
}

function testEvidenceGateBaseSeesCommittedBranchChanges() {
  const dir = initRepo();
  write(
    path.join(dir, ".agent-kit.json"),
    JSON.stringify({
      evidenceGates: [
        {
          changed: ["src/strategy/**"],
          requires: ["docs/evidence/walk-forward-latest.md"],
          note: "strategy evidence required",
        },
      ],
    }),
  );
  write(path.join(dir, "README.md"), "base\n");
  run("git", ["add", "."], { cwd: dir });
  run("git", ["commit", "-m", "base"], { cwd: dir });
  run("git", ["switch", "-c", "feature"], { cwd: dir });
  write(path.join(dir, "src", "strategy", "rule.py"), "print('signal')\n");
  run("git", ["add", "."], { cwd: dir });
  run("git", ["commit", "-m", "strategy change"], { cwd: dir });

  const noBase = run(NODE, [EVIDENCE_GATE], { cwd: dir });
  assert(
    noBase.status === 0,
    "evidence-gate without --base ignores clean committed branch changes",
    `status=${noBase.status}\nstdout=${noBase.stdout}\nstderr=${noBase.stderr}\n`,
  );
  const withBase = run(NODE, [EVIDENCE_GATE, "--base", "main"], { cwd: dir });
  assert(
    withBase.status === 1 && /required evidence missing/i.test(withBase.stderr),
    "evidence-gate --base blocks missing evidence for committed branch changes",
    `status=${withBase.status}\nstdout=${withBase.stdout}\nstderr=${withBase.stderr}\n`,
  );

  const invalidBase = run(NODE, [EVIDENCE_GATE, "--base", "missing-ref"], { cwd: dir });
  assert(
    invalidBase.status !== 0 && /failing closed/i.test(invalidBase.stderr),
    "evidence-gate --base fails closed when git cannot diff the base",
    `status=${invalidBase.status}\nstdout=${invalidBase.stdout}\nstderr=${invalidBase.stderr}\n`,
  );

  write(path.join(dir, "docs", "evidence", "walk-forward-latest.md"), "untracked evidence\n");
  const withUntrackedEvidence = run(NODE, [EVIDENCE_GATE, "--base", "main"], { cwd: dir });
  assert(
    withUntrackedEvidence.status === 1 && /required evidence missing/i.test(withUntrackedEvidence.stderr),
    "evidence-gate --base ignores untracked evidence files",
    `status=${withUntrackedEvidence.status}\nstdout=${withUntrackedEvidence.stdout}\nstderr=${withUntrackedEvidence.stderr}\n`,
  );

  run("git", ["add", "docs/evidence/walk-forward-latest.md"], { cwd: dir });
  const evidenceCommit = run("git", ["commit", "-m", "add evidence"], { cwd: dir });
  assert(evidenceCommit.status === 0, "git commit evidence in temp repo", evidenceCommit.stderr || evidenceCommit.stdout);
  const withCommittedEvidence = run(NODE, [EVIDENCE_GATE, "--base", "main"], { cwd: dir });
  assert(
    withCommittedEvidence.status === 0,
    "evidence-gate --base accepts evidence committed in HEAD",
    `status=${withCommittedEvidence.status}\nstdout=${withCommittedEvidence.stdout}\nstderr=${withCommittedEvidence.stderr}\n`,
  );
}

testSecretScanOutsideGitFailsClosed();
testSecretScanBlocksToken();
testSecretScanAllowsEnvExamplePlaceholder();
testSecretScanBlocksEnvExampleToken();
testEvidenceGateBaseSeesCommittedBranchChanges();

process.stdout.write(`\n${failed ? "FAIL" : "PASS"}  gate regression tests\n`);
process.exitCode = failed ? 1 : 0;
