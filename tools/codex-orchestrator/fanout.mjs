#!/usr/bin/env node
// Codex fan-out orchestrator (read-only by default).
//
// Lets an agent (Claude Code, or you) act as an ORCHESTRATOR and fan a job out
// to many parallel `codex exec` turns. Orchestration runs on the Claude/Anthropic
// side; the Codex turns run on your Codex/ChatGPT plan. Two quota pools at once.
//
// Zero dependencies: only Node built-ins. Copy this single file anywhere.
// It does NOT depend on any Claude Code / Codex plugin — only the standalone
// Codex CLI (`npm install -g @openai/codex`, then `codex login`).
//
// Usage:
//   node tools/codex-orchestrator/fanout.mjs --doctor              # check prerequisites (no quota)
//   node tools/codex-orchestrator/fanout.mjs --spec <spec.json>    # run a fan-out
//   node tools/codex-orchestrator/fanout.mjs --spec <spec.json> --out <dir>   # results into <dir>
//
// Spec JSON shape (all top-level fields optional except `units`):
//   {
//     "concurrency": 4,                 // max parallel codex processes (default 4)
//     "sandbox": "read-only",           // read-only | workspace-write | danger-full-access
//     "model": "gpt-5.3-codex",         // optional; omit for the Codex default
//     "effort": "medium",               // optional reasoning effort
//     "cwd": "<abs project path>",      // working root for every unit (default: process.cwd())
//     "timeoutMs": 900000,              // per-unit hard timeout (default 15 min)
//     "skipGitCheck": false,            // pass --skip-git-repo-check (for non-git dirs)
//     "units": [
//       { "id": "audit-deps", "prompt": "..." },
//       { "id": "review-api", "prompt": "...", "model": "...", "effort": "high", "cwd": "..." }
//     ]
//   }
//
// Output (under <out>/<runId>/, default tools/codex-orchestrator/runs/<runId>/):
//   <id>.final.md   final agent message
//   <id>.log        full stdout+stderr stream
//   summary.json    machine-readable results
//   summary.md      human-readable digest
//
// Exit code: 0 if every unit exited 0, else 1.

import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_RUNS_DIR = path.join(SCRIPT_DIR, "runs");
const IS_WIN = process.platform === "win32";

// Resolve how to launch Codex. Spawning the .cmd shim with shell:true leaves
// args unescaped (Node DEP0190) and breaks on paths with spaces, so prefer
// invoking the real `bin/codex.js` through node directly with shell:false.
function findCodexJsOnWindows() {
  const launcherNames = ["codex.cmd", "codex.ps1", "codex"];
  const dirs = (process.env.PATH || "").split(path.delimiter).filter(Boolean);
  for (const dir of dirs) {
    const hasLauncher = launcherNames.some((name) => {
      try {
        return fs.existsSync(path.join(dir, name));
      } catch {
        return false;
      }
    });
    if (!hasLauncher) continue;
    const js = path.join(dir, "node_modules", "@openai", "codex", "bin", "codex.js");
    if (fs.existsSync(js)) return js;
  }
  return null;
}

function resolveLauncher() {
  if (process.env.CODEX_JS) {
    return { exe: process.execPath, prefixArgs: [process.env.CODEX_JS], useShell: false };
  }
  if (process.env.CODEX_BIN) {
    return { exe: process.env.CODEX_BIN, prefixArgs: [], useShell: IS_WIN };
  }
  if (IS_WIN) {
    const js = findCodexJsOnWindows();
    if (js) return { exe: process.execPath, prefixArgs: [js], useShell: false };
    return { exe: "codex.cmd", prefixArgs: [], useShell: true }; // last resort
  }
  return { exe: "codex", prefixArgs: [], useShell: false };
}

const LAUNCHER = resolveLauncher();

function parseCliArgs(argv) {
  const out = { spec: null, doctor: false, runId: null, out: null, help: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--spec") out.spec = argv[++i];
    else if (arg === "--doctor" || arg === "--check") out.doctor = true;
    else if (arg === "--run-id") out.runId = argv[++i];
    else if (arg === "--out") out.out = argv[++i];
    else if (arg === "--help" || arg === "-h") out.help = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }
  return out;
}

function ts() {
  // Local node process: Date is fine here.
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}` +
    `-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`
  );
}

function logLine(msg) {
  process.stderr.write(`[fanout] ${msg}\n`);
}

function spawnCodexCapture(args, { stdinText } = {}) {
  return new Promise((resolve) => {
    const child = spawn(LAUNCHER.exe, [...LAUNCHER.prefixArgs, ...args], {
      shell: LAUNCHER.useShell,
      windowsHide: true
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (c) => (stdout += c.toString()));
    child.stderr.on("data", (c) => (stderr += c.toString()));
    child.on("error", (err) => resolve({ error: err, exitCode: -1, stdout, stderr }));
    child.on("close", (code) => resolve({ exitCode: code, stdout, stderr }));
    if (stdinText != null) child.stdin.write(stdinText);
    child.stdin.end();
  });
}

async function runDoctor() {
  logLine(`launcher: ${LAUNCHER.exe} ${LAUNCHER.prefixArgs.join(" ")} (platform ${process.platform}, shell ${LAUNCHER.useShell})`);
  const steps = [];

  const ver = await spawnCodexCapture(["--version"]);
  const codexOk = !ver.error && ver.exitCode === 0;
  if (codexOk) {
    logLine(`codex: ${ver.stdout.trim() || ver.stderr.trim()}`);
  } else {
    logLine("codex: NOT FOUND");
    steps.push("Install Codex CLI:  npm install -g @openai/codex");
  }

  let loginOk = false;
  if (codexOk) {
    const login = await spawnCodexCapture(["login", "status"]);
    const text = (login.stdout + login.stderr).trim();
    loginOk = /logged in/i.test(text);
    logLine(`login: ${text || "(no output)"}`);
    if (!loginOk) {
      steps.push("Log in to Codex:  codex login   (or: codex login --device-auth)");
    }
  }

  const ready = codexOk && loginOk;
  if (ready) {
    logLine("DOCTOR: ready. You can run a fan-out with --spec <spec.json>.");
  } else {
    logLine("DOCTOR: not ready. Next steps:");
    for (const s of steps) logLine(`  - ${s}`);
  }
  return ready ? 0 : 1;
}

function buildUnitArgs(unit, cfg, finalFile) {
  const sandbox = unit.sandbox || cfg.sandbox;
  const model = unit.model || cfg.model;
  const effort = unit.effort || cfg.effort;
  const cwd = unit.cwd ? path.resolve(unit.cwd) : cfg.cwd;
  const args = ["exec", "-s", sandbox, "-C", cwd, "--color", "never", "-o", finalFile];
  if (model) args.push("-m", model);
  if (effort) args.push("-c", `model_reasoning_effort="${effort}"`);
  if (cfg.skipGitCheck) args.push("--skip-git-repo-check");
  args.push("-"); // prompt from stdin
  return args;
}

function runUnit(unit, cfg, runDir, index, total) {
  const finalFile = path.join(runDir, `${unit.id}.final.md`);
  const logFile = path.join(runDir, `${unit.id}.log`);
  const logStream = fs.createWriteStream(logFile);
  const args = buildUnitArgs(unit, cfg, finalFile);
  const timeoutMs = unit.timeoutMs || cfg.timeoutMs;
  const start = Date.now();

  logLine(`(${index + 1}/${total}) start  ${unit.id}`);

  return new Promise((resolve) => {
    const child = spawn(LAUNCHER.exe, [...LAUNCHER.prefixArgs, ...args], { shell: LAUNCHER.useShell, windowsHide: true });
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      try {
        child.kill("SIGTERM");
      } catch {
        /* ignore */
      }
    }, timeoutMs);

    child.stdout.on("data", (c) => logStream.write(c));
    child.stderr.on("data", (c) => logStream.write(c));

    child.on("error", (err) => {
      clearTimeout(timer);
      logStream.end(`\n[fanout] spawn error: ${err.message}\n`);
      resolve({
        id: unit.id,
        exitCode: -1,
        error: err.message,
        timedOut: false,
        durationMs: Date.now() - start,
        logFile,
        finalFile: null
      });
    });

    child.on("close", (code) => {
      clearTimeout(timer);
      logStream.end();
      let finalMsg = "";
      try {
        finalMsg = fs.readFileSync(finalFile, "utf8");
      } catch {
        /* file may not exist on failure */
      }
      const status = timedOut ? "TIMEOUT" : code === 0 ? "ok" : `exit ${code}`;
      logLine(`(${index + 1}/${total}) done   ${unit.id}  [${status}, ${Math.round((Date.now() - start) / 1000)}s]`);
      resolve({
        id: unit.id,
        exitCode: timedOut ? 124 : code,
        timedOut,
        durationMs: Date.now() - start,
        logFile,
        finalFile: finalMsg ? finalFile : null,
        finalPreview: finalMsg.trim().slice(0, 280)
      });
    });

    child.stdin.write(unit.prompt);
    child.stdin.end();
  });
}

async function runPool(items, limit, worker) {
  const results = new Array(items.length);
  let cursor = 0;
  async function lane() {
    while (true) {
      const i = cursor++;
      if (i >= items.length) return;
      results[i] = await worker(items[i], i);
    }
  }
  const lanes = Array.from({ length: Math.max(1, Math.min(limit, items.length)) }, lane);
  await Promise.all(lanes);
  return results;
}

function validateSpec(spec) {
  if (!spec || typeof spec !== "object") throw new Error("Spec must be a JSON object.");
  if (!Array.isArray(spec.units) || spec.units.length === 0) {
    throw new Error("Spec.units must be a non-empty array.");
  }
  const ids = new Set();
  for (const u of spec.units) {
    if (!u || typeof u.id !== "string" || !u.id.trim()) throw new Error("Each unit needs a string id.");
    if (!/^[A-Za-z0-9._-]+$/.test(u.id)) throw new Error(`Unit id "${u.id}" must match [A-Za-z0-9._-].`);
    if (ids.has(u.id)) throw new Error(`Duplicate unit id: ${u.id}`);
    ids.add(u.id);
    if (typeof u.prompt !== "string" || !u.prompt.trim()) throw new Error(`Unit ${u.id} needs a non-empty prompt.`);
  }
}

function writeSummary(runDir, cfg, results) {
  const summary = {
    runId: path.basename(runDir),
    startedAt: cfg.startedAt,
    finishedAt: new Date().toISOString(),
    sandbox: cfg.sandbox,
    model: cfg.model || "(codex default)",
    concurrency: cfg.concurrency,
    cwd: cfg.cwd,
    total: results.length,
    ok: results.filter((r) => r.exitCode === 0).length,
    failed: results.filter((r) => r.exitCode !== 0).length,
    units: results
  };
  fs.writeFileSync(path.join(runDir, "summary.json"), JSON.stringify(summary, null, 2));

  const lines = [];
  lines.push(`# Fan-out run ${summary.runId}`);
  lines.push("");
  lines.push(`- sandbox: ${summary.sandbox}`);
  lines.push(`- model: ${summary.model}`);
  lines.push(`- concurrency: ${summary.concurrency}`);
  lines.push(`- cwd: ${summary.cwd}`);
  lines.push(`- units: ${summary.total} (ok ${summary.ok}, failed ${summary.failed})`);
  lines.push("");
  for (const r of results) {
    const status = r.exitCode === 0 ? "[ok]" : r.timedOut ? "[timeout]" : "[fail]";
    lines.push(`## ${status} ${r.id}  (${Math.round(r.durationMs / 1000)}s, exit ${r.exitCode})`);
    if (r.error) lines.push(`- error: ${r.error}`);
    if (r.finalFile) lines.push(`- final: ${path.relative(runDir, r.finalFile)}`);
    lines.push(`- log:   ${path.relative(runDir, r.logFile)}`);
    if (r.finalPreview) {
      lines.push("");
      lines.push(`> ${r.finalPreview.replace(/\n/g, "\n> ")}`);
    }
    lines.push("");
  }
  fs.writeFileSync(path.join(runDir, "summary.md"), lines.join("\n"));
  return summary;
}

async function runSpec(specPath, opts) {
  const raw = fs.readFileSync(specPath, "utf8");
  const spec = JSON.parse(raw);
  validateSpec(spec);

  const cfg = {
    concurrency: Number(spec.concurrency) > 0 ? Number(spec.concurrency) : 4,
    sandbox: spec.sandbox || "read-only",
    model: spec.model || null,
    effort: spec.effort || null,
    cwd: spec.cwd ? path.resolve(spec.cwd) : process.cwd(),
    timeoutMs: Number(spec.timeoutMs) > 0 ? Number(spec.timeoutMs) : 15 * 60 * 1000,
    skipGitCheck: Boolean(spec.skipGitCheck),
    startedAt: new Date().toISOString()
  };

  const runsBase = opts.out ? path.resolve(opts.out) : DEFAULT_RUNS_DIR;
  const runId = opts.runId || ts();
  const runDir = path.join(runsBase, runId);
  fs.mkdirSync(runDir, { recursive: true });

  logLine(`run ${runId}: ${spec.units.length} units, concurrency ${cfg.concurrency}, sandbox ${cfg.sandbox}, model ${cfg.model || "(default)"}`);
  if (cfg.sandbox !== "read-only") {
    logLine(`WARNING: sandbox is "${cfg.sandbox}". Parallel writers in the same cwd will clobber each other — give each unit its own worktree via per-unit cwd.`);
  }

  const results = await runPool(spec.units, cfg.concurrency, (unit, i) => runUnit(unit, cfg, runDir, i, spec.units.length));
  const summary = writeSummary(runDir, cfg, results);

  logLine(`run ${runId} complete: ${summary.ok} ok, ${summary.failed} failed`);
  logLine(`summary: ${path.join(runDir, "summary.md")}`);
  process.stdout.write(JSON.stringify(summary, null, 2) + "\n");
  return summary.failed === 0 ? 0 : 1;
}

function printHelp() {
  process.stdout.write(
    [
      "Codex fan-out orchestrator (read-only by default)",
      "",
      "Usage:",
      "  node tools/codex-orchestrator/fanout.mjs --doctor",
      "  node tools/codex-orchestrator/fanout.mjs --spec <spec.json> [--out <dir>] [--run-id <id>]",
      "",
      "See the header of this file or README.md for the spec shape.",
      ""
    ].join("\n")
  );
}

async function main() {
  const args = parseCliArgs(process.argv.slice(2));
  if (args.help) {
    printHelp();
    return 0;
  }
  if (args.doctor) return runDoctor();
  if (!args.spec) {
    printHelp();
    throw new Error("Provide --spec <spec.json> or --doctor.");
  }
  return runSpec(path.resolve(args.spec), { runId: args.runId, out: args.out });
}

main()
  .then((code) => {
    process.exitCode = code ?? 0;
  })
  .catch((err) => {
    process.stderr.write(`[fanout] ${err instanceof Error ? err.message : String(err)}\n`);
    process.exitCode = 1;
  });
