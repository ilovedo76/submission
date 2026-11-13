import test from "node:test";
import assert from "node:assert/strict";
import {
  notesFromForm, planRows, reportFilename, chartLabel, styleOf, saveStatusText,
  excelFiles, chartKeyFor, NEEDS_MAPPING_MESSAGE,
} from "./mvp.js";
import { progressText, shortError } from "./engine.js";

test("notesFromForm trims and drops empty plan rows", () => {
  const n = notesFromForm(" 이슈 ", [
    { 담당: " 김 ", 일정: "1월", 대책: "교체" },
    { 담당: "", 일정: " ", 대책: "" },
  ]);
  assert.deepEqual(n, { issues: " 이슈 ", plan: [{ 담당: "김", 일정: "1월", 대책: "교체" }] });
});

test("planRows gives at least one empty row and copies stored rows", () => {
  assert.deepEqual(planRows(null), [{ 담당: "", 일정: "", 대책: "" }]);
  assert.deepEqual(planRows({ issues: "", plan: [] }), [{ 담당: "", 일정: "", 대책: "" }]);
  assert.deepEqual(planRows({ plan: [{ 담당: "이", 대책: "점검" }] }), [{ 담당: "이", 일정: "", 대책: "점검" }]);
});

test("reportFilename uses the run docNo", () => {
  assert.equal(reportFilename("QR-2512-ab", "docx"), "QR-2512-ab.docx");
  assert.equal(reportFilename("QR-2512-ab", "pdf"), "QR-2512-ab.pdf");
  assert.equal(reportFilename("QR-2512-ab", "zip"), "QR-2512-ab.zip");
  assert.equal(reportFilename("", "pdf"), "품질보고서.pdf");
  assert.equal(reportFilename("a/b:c", "pdf"), "a_b_c.pdf");
});

test("chartLabel names known keys and per-model p-charts", () => {
  assert.equal(chartLabel("trend"), "시장 불량률 추이");
  assert.equal(chartLabel("pchart_양문형"), "1MIS P 관리도 · 양문형");
  assert.equal(chartLabel("unknown_key"), "unknown_key");
});

test("styleOf accepts the tuple form and an object form", () => {
  assert.deepEqual(styleOf(["■", "높음", "#fff", "#cf2e2a"]), { mark: "■", label: "높음", color: "#fff", bg: "#cf2e2a" });
  assert.deepEqual(styleOf(["■", "#fff", "#cf2e2a"], "개선 필요"), { mark: "■", label: "개선 필요", color: "#fff", bg: "#cf2e2a" });
  assert.deepEqual(styleOf({ mark: "▲", label: "중간", color: "#363434", background: "#e0a526" }),
    { mark: "▲", label: "중간", color: "#363434", bg: "#e0a526" });
  assert.deepEqual(styleOf({ label: "높음", icon: "▲", color: "#d03b3b" }),
    { mark: "▲", label: "높음", color: "#d03b3b", bg: "transparent", border: "#d03b3b" });
  assert.deepEqual(styleOf({ color: "#fff", bg: "#d03b3b" }, "개선 필요"),
    { mark: "", label: "개선 필요", color: "#fff", bg: "#d03b3b" });
  assert.equal(styleOf(undefined, "x").label, "x");
});

test("saveStatusText covers every status", () => {
  assert.equal(saveStatusText("saved"), "대시보드에 저장됨");
  assert.equal(saveStatusText("no_permission"), "저장 권한이 없어 대시보드에는 반영되지 않습니다 (참여 권한 필요)");
  assert.equal(saveStatusText("offline"), "로그인하지 않아 저장되지 않습니다");
  assert.match(saveStatusText("error"), /오류/);
});

test("excelFiles keeps .xlsx/.xlsm only", () => {
  const files = [{ name: "a.xlsx" }, { name: "b.XLSM" }, { name: "c.csv" }, { name: "d.xls" }];
  assert.deepEqual(excelFiles(files).map((f) => f.name), ["a.xlsx", "b.XLSM"]);
});

test("chartKeyFor falls back to an available key", () => {
  assert.equal(chartKeyFor("pchart_A", ["trend", "pchart_A"]), "pchart_A");
  assert.equal(chartKeyFor("pchart_A", ["trend", "pchart"]), "pchart");
  assert.equal(chartKeyFor("nope", ["trend", "pchart"]), "trend");
});

test("needs-mapping message and engine helpers", () => {
  assert.match(NEEDS_MAPPING_MESSAGE, /열 이름을 인식하지 못했습니다/);
  assert.equal(progressText(3, 7), "분석 엔진 준비 중… (3/7 단계)");
  assert.equal(shortError("Traceback...\n  File x\nValueError: 다시 분석해 주세요."), "다시 분석해 주세요.");
});
