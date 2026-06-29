#!/usr/bin/env node
// Secret-scan gate (zero dependencies). Blocks committing broker/Telegram tokens.
//
// Scans git-STAGED files by default (the pre-commit path), or the whole tracked
// tree with --all. Token shapes per docs/contracts/config-and-secrets.md §5
// (TZ §4.1 / §16): a leak is any committed value matching a Telegram bot-token or
// T-Invest token shape, or a real-looking value assigned to a *_TOKEN key.
// `.env.example` placeholders (`<...>` form) are allow-listed.
//
// Usage:
//   node tools/secret-scan.mjs           # scan staged changes (pre-commit)
//   node tools/secret-scan.mjs --all     # scan all tracked files
// Exit 0 = clean; exit 1 = suspected secret(s) found; exit 2 = scanner infrastructure failure.

import { execSync, execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

function git(cmd) {
  return execSync(`git ${cmd}`, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
}

let ROOT;
try {
  ROOT = git("rev-parse --show-toplevel").trim();
} catch {
  process.stderr.write("secret-scan: unable to locate git repository; failing closed.\n");
  process.exit(2);
}

const ALL = process.argv.includes("--all");

// --- detectors ---------------------------------------------------------------
// Telegram bot token: <6-15 digits>:<30+ url-safe chars> (bot-ids grow; keep the id quantifier loose).
const TELEGRAM = /\b\d{6,15}:[A-Za-z0-9_-]{30,}\b/;
// T-Invest token: opaque "t.<base64url>" ~50+ chars (real tokens are ~80+).
const TINVEST = /\bt\.[A-Za-z0-9_-]{50,}\b/;
// Assignment of a value to a secret-ish key (KEY = value | KEY: value).
const SECRET_KEY_ASSIGN =
  /\b((?:[A-Z0-9_]*_)?TOKEN|TINVEST_TOKEN_[A-Z0-9_]+|TELEGRAM_BOT_TOKEN|DASHBOARD_AUTH_TOKEN|[A-Z0-9_]*SECRET[A-Z0-9_]*|[A-Z0-9_]*API_?KEY)\s*[:=]\s*(.+?)\s*$/;

// A value that is clearly NOT a real secret (placeholder / env-interpolation / empty).
function isPlaceholder(value) {
  const v = value.trim().replace(/^["']|["'],?$/g, "").trim();
  if (!v) return true;
  if (v.startsWith("<") && v.endsWith(">")) return true; // <placeholder>
  if (/^\$\{?[A-Za-z_]/.test(v)) return true; // ${ENV} / $ENV interpolation
  if (/^os\.environ|getenv|process\.env|Field\(/.test(v)) return true; // code reading env
  if (/(placeholder|example|changeme|change-me|dummy|sample|redacted|your[-_ ]|xxxx|\.\.\.|todo|none|null)/i.test(v))
    return true;
  // a bare key-shape reference with no actual entropy (e.g. all-caps name)
  if (/^[A-Z0-9_]+$/.test(v)) return true;
  return false;
}

// Only scan plausibly-textual files; skip binary/large blobs.
function looksBinary(buf) {
  const n = Math.min(buf.length, 8000);
  for (let i = 0; i < n; i++) if (buf[i] === 0) return true;
  return false;
}

// --- file list ---------------------------------------------------------------
function stagedFiles() {
  const out = git("diff --cached --name-only --diff-filter=ACMR -z");
  return out.split("\0").filter(Boolean);
}
function trackedFiles() {
  const out = git("ls-files -z");
  return out.split("\0").filter(Boolean);
}

const readErrors = [];

function contentOf(rel) {
  if (ALL) {
    const abs = path.join(ROOT, rel);
    try {
      const buf = fs.readFileSync(abs);
      if (looksBinary(buf) || buf.length > 1_000_000) return null;
      return buf.toString("utf8");
    } catch {
      readErrors.push(rel);
      return null;
    }
  }
  // staged blob — execFileSync (no shell) so paths with spaces/special chars are not silently skipped
  try {
    const buf = execFileSync("git", ["show", `:${rel}`], { cwd: ROOT, maxBuffer: 16 * 1024 * 1024 });
    if (looksBinary(buf) || buf.length > 1_000_000) return null;
    return buf.toString("utf8");
  } catch {
    readErrors.push(rel);
    return null;
  }
}

// --- scan --------------------------------------------------------------------
const files = ALL ? trackedFiles() : stagedFiles();
const findings = [];

for (const rel of files) {
  const text = contentOf(rel);
  if (text == null) continue;
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (TELEGRAM.test(line)) findings.push({ rel, n: i + 1, why: "Telegram bot-token shape" });
    else if (TINVEST.test(line)) findings.push({ rel, n: i + 1, why: "T-Invest token shape (t.<base64url>)" });
    else {
      const m = line.match(SECRET_KEY_ASSIGN);
      if (m && !isPlaceholder(m[2])) {
        findings.push({ rel, n: i + 1, why: `real value assigned to secret key "${m[1]}"` });
      }
    }
  }
}

if (findings.length) {
  process.stderr.write("\nsecret-scan: SUSPECTED SECRET(S) — commit blocked.\n");
  for (const f of findings) process.stderr.write(`  ${f.rel}:${f.n}  ${f.why}\n`);
  process.stderr.write(
    "\nTokens must never be committed (docs/frozen-decisions.md token policy; TZ §16).\n" +
      "Move the value to .env (git-ignored) and reference it via env. Use <placeholder> in .env.example.\n" +
      "False positive? Adjust tools/secret-scan.mjs allow-lists deliberately.\n",
  );
  process.exit(1);
}

if (readErrors.length) {
  process.stderr.write("secret-scan: unable to read file content; failing closed.\n");
  for (const rel of readErrors) process.stderr.write(`  ${rel}\n`);
  process.exit(2);
}

process.stdout.write(`secret-scan: clean (${files.length} ${ALL ? "tracked" : "staged"} file(s) scanned).\n`);
process.exit(0);
