#!/usr/bin/env node
// agent-kit harness self-verifier (zero dependencies).
//
// Lightweight integrity check for the kit's own scaffolding — the "parity"
// guard that the heavier source repos enforce with full sync machinery, kept
// minimal here on purpose. Asserts:
//   - AGENTS.md exists; CLAUDE.md and GEMINI.md exist and reference AGENTS.md
//   - .agent-kit.json parses
//   - every .codex/agents/*.toml is read-only and has a matching, read-only
//     .claude/agents/<kebab>.md mirror (and vice versa — orphan detection)
//   - hook scripts referenced by .codex/hooks.json and .claude/settings.json exist
//   - the orchestrator and its example specs are present and valid
//   - every .claude/skills/*/SKILL.md has name+description frontmatter
//   - declared component profiles have a valid status and an existing doc
//   - no .agent-kit.json forbiddenTerms appear in active instruction/skill files
//
// Exit 0 if all checks pass, else 1.

import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const results = [];
function ok(msg) {
  results.push({ pass: true, msg });
}
function fail(msg) {
  results.push({ pass: false, msg });
}
function exists(rel) {
  return fs.existsSync(path.join(ROOT, rel));
}
function read(rel) {
  return fs.readFileSync(path.join(ROOT, rel), "utf8");
}
function listFiles(relDir, ext) {
  const dir = path.join(ROOT, relDir);
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).filter((f) => f.endsWith(ext));
}
function tomlValue(text, key) {
  const m = text.match(new RegExp(`^${key}\\s*=\\s*"([^"]*)"`, "m"));
  return m ? m[1] : null;
}
function frontmatter(text) {
  const m = text.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!m) return {};
  const out = {};
  for (const line of m[1].split(/\r?\n/)) {
    const kv = line.match(/^([A-Za-z0-9_-]+)\s*:\s*(.*)$/);
    if (kv) out[kv[1]] = kv[2].trim();
  }
  return out;
}

// --- instructions ---
if (exists("AGENTS.md")) ok("AGENTS.md present");
else fail("AGENTS.md missing");

for (const stub of ["CLAUDE.md", "GEMINI.md"]) {
  if (!exists(stub)) fail(`${stub} missing`);
  else if (!/AGENTS\.md/.test(read(stub))) fail(`${stub} does not reference AGENTS.md`);
  else ok(`${stub} references AGENTS.md`);
}

// --- config ---
let cfg = {};
if (!exists(".agent-kit.json")) {
  fail(".agent-kit.json missing");
} else {
  try {
    cfg = JSON.parse(read(".agent-kit.json"));
    ok(".agent-kit.json parses");
  } catch (e) {
    fail(`.agent-kit.json invalid JSON: ${e.message}`);
  }
}

// --- subagents: toml <-> md mirror parity ---
const tomls = listFiles(".codex/agents", ".toml");
const mdSeen = new Set();
for (const file of tomls) {
  const text = read(path.join(".codex/agents", file));
  const name = tomlValue(text, "name");
  const sandbox = tomlValue(text, "sandbox_mode");
  if (!name) {
    fail(`.codex/agents/${file}: missing name`);
    continue;
  }
  if (sandbox !== "read-only") {
    fail(`.codex/agents/${file}: sandbox_mode is "${sandbox}" (expected read-only)`);
  }
  const kebab = name.replace(/_/g, "-");
  const mdRel = path.join(".claude/agents", `${kebab}.md`);
  if (!exists(mdRel)) {
    fail(`${file}: no Claude mirror at ${mdRel}`);
    continue;
  }
  mdSeen.add(`${kebab}.md`);
  const fm = frontmatter(read(mdRel));
  if (fm.name !== kebab) {
    fail(`${mdRel}: frontmatter name "${fm.name}" != "${kebab}"`);
  } else if (!fm.tools) {
    fail(`${mdRel}: read-only mirror should declare a tools: line`);
  } else if (/\b(Edit|Write|MultiEdit)\b/.test(fm.tools)) {
    fail(`${mdRel}: read-only mirror must not grant ${fm.tools.match(/\b(Edit|Write|MultiEdit)\b/)[0]}`);
  } else {
    ok(`agent ${name} <-> ${kebab}.md (read-only, mirrored)`);
  }
}
for (const md of listFiles(".claude/agents", ".md")) {
  if (!mdSeen.has(md)) fail(`.claude/agents/${md}: orphan (no matching .codex/agents/*.toml)`);
}

// --- hooks referenced by both harnesses exist ---
function checkHookRefs(rel) {
  if (!exists(rel)) {
    fail(`${rel} missing`);
    return;
  }
  const refs = [...new Set([...read(rel).matchAll(/\.codex\/hooks\/[\w-]+\.js/g)].map((m) => m[0]))];
  if (!refs.length) {
    fail(`${rel}: references no .codex/hooks scripts`);
    return;
  }
  for (const r of refs) {
    if (exists(r)) ok(`${rel} -> ${r} exists`);
    else fail(`${rel} -> ${r} MISSING`);
  }
}
checkHookRefs(".codex/hooks.json");
checkHookRefs(".claude/settings.json");

// --- orchestrator + example specs ---
if (exists("tools/codex-orchestrator/fanout.mjs")) ok("orchestrator fanout.mjs present");
else fail("tools/codex-orchestrator/fanout.mjs missing");

for (const spec of listFiles("tools/codex-orchestrator/examples", ".json")) {
  const rel = path.join("tools/codex-orchestrator/examples", spec);
  try {
    const parsed = JSON.parse(read(rel));
    if (!Array.isArray(parsed.units) || parsed.units.length === 0) fail(`${rel}: no units`);
    else ok(`${rel}: valid (${parsed.units.length} units)`);
  } catch (e) {
    fail(`${rel}: invalid JSON: ${e.message}`);
  }
}

// --- skills frontmatter ---
const skillsDir = path.join(ROOT, ".claude/skills");
if (fs.existsSync(skillsDir)) {
  for (const name of fs.readdirSync(skillsDir)) {
    const rel = path.join(".claude/skills", name, "SKILL.md");
    if (!exists(rel)) {
      fail(`${rel} missing`);
      continue;
    }
    const fm = frontmatter(read(rel));
    if (!fm.name || !fm.description) fail(`${rel}: needs name + description frontmatter`);
    else ok(`skill ${fm.name}`);
  }
}

// --- component profiles (optional) ---
const profiles = Array.isArray(cfg.profiles) ? cfg.profiles : [];
for (const p of profiles) {
  if (!p || !p.name) {
    fail("profile entry missing a name");
    continue;
  }
  if (p.status !== "active" && p.status !== "dormant") {
    fail(`profile ${p.name}: status must be "active" or "dormant" (got ${JSON.stringify(p.status)})`);
  } else if (!p.doc || !exists(p.doc)) {
    fail(`profile ${p.name}: doc "${p.doc || "(unset)"}" missing`);
  } else {
    ok(`profile ${p.name} (${p.status})`);
  }
}

// --- forbidden terms in active surface (optional) ---
const terms = Array.isArray(cfg.forbiddenTerms)
  ? cfg.forbiddenTerms.filter((t) => typeof t === "string" && t.trim())
  : [];
if (terms.length) {
  // Scan the policy surface only (instructions + skills + agents). README and
  // GETTING-STARTED are kit meta-docs that legitimately contain stack-name
  // examples, so they are deliberately excluded to avoid false positives.
  const active = ["AGENTS.md", "CLAUDE.md", "GEMINI.md"];
  if (fs.existsSync(skillsDir)) {
    for (const n of fs.readdirSync(skillsDir)) {
      const rel = `.claude/skills/${n}/SKILL.md`;
      if (exists(rel)) active.push(rel);
    }
  }
  for (const f of listFiles(".codex/agents", ".toml")) active.push(`.codex/agents/${f}`);
  for (const f of listFiles(".claude/agents", ".md")) active.push(`.claude/agents/${f}`);

  let hits = 0;
  for (const rel of active.filter(exists)) {
    const lines = read(rel).split(/\r?\n/);
    for (const term of terms) {
      const lc = term.toLowerCase();
      const idx = lines.findIndex((l) => l.toLowerCase().includes(lc));
      if (idx >= 0) {
        fail(`forbidden term "${term}" in ${rel}:${idx + 1}`);
        hits += 1;
      }
    }
  }
  if (!hits) ok(`no forbidden terms (${terms.length} checked across active files)`);
}

// --- report ---
const failed = results.filter((r) => !r.pass);
for (const r of results) process.stdout.write(`${r.pass ? "PASS" : "FAIL"}  ${r.msg}\n`);
process.stdout.write(`\n${results.length} checks, ${failed.length} failed.\n`);
process.exitCode = failed.length ? 1 : 0;
