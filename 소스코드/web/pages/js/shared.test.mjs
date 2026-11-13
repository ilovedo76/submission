import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fmtPct, fmtRatio, fmtChange, runId, defaultRun, seriesFor, SEVERITY_STYLE, VERDICT_STYLE, pyFixed, ymLabel } from "./shared.js";

const fixture = JSON.parse(readFileSync(new URL("../../tools/dashboard_fixture.json", import.meta.url), "utf8"));

test("fmtPct: rate x 100, 3 decimals; null -> dash", () => {
  assert.equal(fmtPct(0.00136), "0.136%");
  assert.equal(fmtPct(0.0024139), "0.241%");
  assert.equal(fmtPct(0), "0.000%");
  assert.equal(fmtPct(null), "–");
  assert.equal(fmtPct(undefined), "–");
  assert.equal(fmtPct(NaN), "–");
});

test("fmtRatio / fmtChange like qreport", () => {
  assert.equal(fmtRatio(1.94), "1.9배");
  assert.equal(fmtRatio(3), "3.0배");
  assert.equal(fmtRatio(null), "–");
  assert.equal(fmtChange(57.2), "+57%");
  assert.equal(fmtChange(199.796), "+200%");
  assert.equal(fmtChange(-3.4), "-3%");
  assert.equal(fmtChange(0), "+0%");
  assert.equal(fmtChange(null), "–");
});

test("pyFixed rounds exact ties to even like Python format", () => {
  assert.equal(pyFixed(0.5, 0), "0");
  assert.equal(pyFixed(1.5, 0), "2");
  assert.equal(pyFixed(2.5, 0), "2");
  assert.equal(pyFixed(-2.5, 0), "-2");
  assert.equal(pyFixed(0.125, 2), "0.12");
  assert.equal(pyFixed(0.135, 2), (0.135).toFixed(2));   // not an exact tie in binary
});

test("runId matches web_entry.run_id for every fixture run", () => {
  const runs = Object.entries(fixture).filter(([p]) => p.split("/").length === 2);
  assert.ok(runs.length >= 2);
  for (const [path, run] of runs) {
    assert.equal(runId(run.inputHash, run.baseMonth), run.runId);
    assert.equal(`runs/${runId(run.inputHash, run.baseMonth)}`, path);
  }
  assert.equal(runId("0123456789abcdef0123", "2025-12"), "0123456789abcdef_2025-12");
});

test("defaultRun skips samples, newest updatedAt; only samples -> newest; none -> null", () => {
  const runs = [
    { id: "a", kind: "user", updatedAt: "2026-11-01T00:00:00Z" },
    { id: "b", kind: "sample", updatedAt: "2026-11-12T00:00:00Z" },
    { id: "c", kind: "company", updatedAt: "2026-11-05T00:00:00Z" },
  ];
  assert.equal(defaultRun(runs).id, "c");
  assert.equal(defaultRun([runs[1], { id: "d", kind: "sample", updatedAt: "2026-10-01" }]).id, "b");
  assert.equal(defaultRun([]), null);
  assert.equal(defaultRun(undefined), null);
});

test("seriesFor picks 전체 for null model, the model rows otherwise", () => {
  const s = { market: { "전체": [1], "양문형": [2] } };
  assert.deepEqual(seriesFor(s, "market", null), [1]);
  assert.deepEqual(seriesFor(s, "market", ""), [1]);
  assert.deepEqual(seriesFor(s, "market", "양문형"), [2]);
  assert.deepEqual(seriesFor(s, "market", "없음"), []);
  assert.deepEqual(seriesFor(s, "pareto", null), []);
});

test("style tables cover every severity and verdict", () => {
  for (const k of ["높음", "중간", "낮음"]) {
    assert.equal(SEVERITY_STYLE[k].label, k);
    assert.ok(SEVERITY_STYLE[k].icon && SEVERITY_STYLE[k].color.startsWith("#"));
  }
  for (const k of ["개선 필요", "주의", "정상"]) assert.ok(VERDICT_STYLE[k].color && VERDICT_STYLE[k].bg);
  assert.equal(ymLabel("2025-12"), "25.12");
});
