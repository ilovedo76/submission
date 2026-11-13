// Desktop Python vs Pyodide parity: analyze_files() must give the same run + series.
// Desktop JSON comes from qreport's venv (desktop_dump.py), Pyodide JSON from the pinned Pyodide.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { bootPyodide, PY_HOME } from "./pyodide_env.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..", "..");
const DESKTOP_PY = path.join(repo, "qreport", ".venv", "Scripts", "python.exe");
const DUMP = path.join(here, "desktop_dump.py");
const COMPANY = path.join(repo, "qreport", "tests", "data", "company.xlsx");
const REL_TOL = 1e-9, ABS_TOL = 1e-15;

function desktop(args) {
  const r = spawnSync(DESKTOP_PY, ["-I", DUMP, ...args], { maxBuffer: 1 << 29 });
  assert.equal(r.status, 0, `desktop python failed: ${r.stderr}`);
  return JSON.parse(r.stdout.toString("utf8"));
}

// Weibull-derived numbers (forecast, cost, and the values/thresholds of rule R3 (value only)) come from an
// L-BFGS-B fit with finite-difference gradients; its stopping point differs across numpy/scipy
// builds (desktop native vs Pyodide wasm) by ~1e-5 relative. The displayed (rounded) text is
// identical, which is asserted exactly below. Everything else must match to 1e-9.
const WEIBULL_REL_TOL = 1e-4;
const WEIBULL_RULES = new Set(["R3"]);
const isWeibullPath = (p) =>
  /^run\.headline\.(forecast|cost)\[\d+\]$/.test(p);

export function diff(a, b, p = "$", out = [], tol = REL_TOL) {
  if (typeof a === "number" && typeof b === "number") {
    const d = Math.abs(a - b);
    const t = isWeibullPath(p) ? WEIBULL_REL_TOL : tol;
    if (!(d <= ABS_TOL || d <= t * Math.max(Math.abs(a), Math.abs(b)))) out.push([p, a, b]);
  } else if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) out.push([`${p}.length`, a.length, b.length]);
    for (let i = 0; i < Math.min(a.length, b.length); i++) diff(a[i], b[i], `${p}[${i}]`, out, tol);
  } else if (a && b && typeof a === "object" && typeof b === "object" && !Array.isArray(a) && !Array.isArray(b)) {
    for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) {
      if (!(k in a) || !(k in b)) out.push([`${p}.${k}`, k in a ? "present" : "missing", k in b ? "present" : "missing"]);
      else diff(a[k], b[k], `${p}.${k}`, out, tol);
    }
  } else if (a !== b) out.push([p, a, b]);
  return out;
}

function check(d, p) {
  assert.equal(d.ok, true, "desktop analysis not ok");
  assert.equal(p.ok, true, "pyodide analysis not ok");
  // Guard against vacuous comparisons.
  assert.ok(d.run.alerts.length > 0, "no alerts to compare");
  assert.ok(Object.keys(d.series).length > 0, "no series to compare");
  assert.ok(d.run.headline.forecast?.length > 0, "no forecast to compare");
  assert.ok(d.run.comments.length > 0, "no comments to compare");
  // User-visible text and identities: exact.
  for (const k of ["comments", "verdictLine", "forecastText", "costText", "verdict", "alertCount"]) {
    assert.deepEqual(p.run[k], d.run[k], `run.${k}`);
  }
  assert.equal(p.run.alerts.length, d.run.alerts.length, "alerts length");
  const bad = [];
  d.run.alerts.forEach((da, i) => {
    const pa = p.run.alerts[i];
    for (const k of ["rule", "severity", "target", "period", "chartKey"]) {
      assert.equal(pa[k], da[k], `run.alerts[${i}].${k}`);
    }
    const tol = WEIBULL_RULES.has(da.rule) ? WEIBULL_REL_TOL : REL_TOL;
    diff(da.value, pa.value, `run.alerts[${i}](${da.rule}).value`, bad, tol);
    diff(da.threshold, pa.threshold, `run.alerts[${i}](${da.rule}).threshold`, bad);
  });
  const dRun = { ...d.run, alerts: [] }, pRun = { ...p.run, alerts: [] };
  diff(dRun, pRun, "run", bad);
  diff(d.series, p.series, "series", bad);
  if (bad.length) {
    const msg = bad.slice(0, 10).map(([q, x, y]) => `${q}: desktop ${JSON.stringify(x)} != pyodide ${JSON.stringify(y)}`);
    assert.fail(`${bad.length} differing paths; first ${msg.length}:\n${msg.join("\n")}`);
  }
}

test("desktop vs pyodide parity", { timeout: 300_000, skip: !fs.existsSync(DESKTOP_PY) && `desktop python not found: ${DESKTOP_PY} (create qreport/.venv)` }, async (t) => {
  const pyodide = await bootPyodide({ extraPy: [path.join(repo, "web", "py", "web_entry.py")] });
  const pyAnalyze = (file) => {
    pyodide.FS.writeFile(`${PY_HOME}/parity.xlsx`, fs.readFileSync(file));
    return JSON.parse(pyodide.runPython(`
import json
from web_entry import analyze_files
data = open("${PY_HOME}/parity.xlsx", "rb").read()
json.dumps(analyze_files([("parity.xlsx", data)], None, None, "user"), ensure_ascii=False)
`));
  };
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "parity-"));
  try {
    await t.test("synthetic workbook", () => {
      const xlsx = path.join(tmp, "synth.xlsx");
      const d = desktop(["--synth", xlsx]);   // writes the bytes both sides analyse
      check(d, pyAnalyze(xlsx));
    });
    await t.test("company workbook", { skip: !fs.existsSync(COMPANY) && "company.xlsx not present" }, () => {
      check(desktop([COMPANY]), pyAnalyze(COMPANY));
    });
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
