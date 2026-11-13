import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { cardsFor, historyRows, pageState, chartForKey, warningItems, periodRows, MSG, OVERALL } from "./dashboard.js";
import { openStore, listRuns, loadSeries } from "./store-read.js";
import { fmtPct, fmtRatio, fmtChange } from "./shared.js";
import { fakeClaude } from "../../tools/fake_claude.js";

const fixture = JSON.parse(readFileSync(new URL("../../tools/dashboard_fixture.json", import.meta.url), "utf8"));
async function loaded(kind) {
  const store = await openStore(fakeClaude(fixture));
  const run = (await listRuns(store)).find((r) => r.kind === kind);
  return { run, series: await loadSeries(store, run.id, run) };
}
const card = (cards, key) => cards.find((c) => c.key === key);

test("cardsFor overall: headline values, no scope label on verdict", async () => {
  const { run, series } = await loaded("sample");
  const cards = cardsFor(run, null, series);
  assert.deepEqual(cards.map((c) => c.key), ["verdict", "alerts", "market", "mis1", "forecast"]);
  assert.equal(card(cards, "verdict").value, run.verdict);
  assert.equal(card(cards, "verdict").scope, null);
  assert.equal(card(cards, "alerts").value, `${run.alertCount}건`);
  assert.equal(card(cards, "market").value, fmtPct(run.headline.level));
  assert.match(card(cards, "market").sub, /^전년 대비 [+-]\d+%$/);
  assert.equal(card(cards, "forecast").scope, OVERALL);
  assert.equal(card(cards, "forecast").value, run.forecastText);
  // 1MIS card = the base month's overall P-chart point, the same number as the first comment
  const mis = card(cards, "mis1");
  assert.ok(run.comments[0].includes(mis.value), `${mis.value} in ${run.comments[0]}`);
});

test("cardsFor with a model: verdict/alerts/forecast labelled 전체 기준, market and 1MIS from the model", async () => {
  const { run, series } = await loaded("sample");
  const model = run.models[0];
  const cards = cardsFor(run, model, series);
  for (const k of ["verdict", "alerts", "forecast"]) assert.equal(card(cards, k).scope, OVERALL, k);
  assert.equal(card(cards, "verdict").value, run.verdict);                 // not recomputed per model
  assert.equal(card(cards, "market").scope, model);
  assert.equal(card(cards, "mis1").scope, model);
  // the model's own denominator: its P-chart row of the base month (not the overall one)
  const row = series.pchart[model].find((r) => r.m === run.baseMonth);
  assert.equal(card(cards, "mis1").value, fmtPct(row.p));
  assert.ok(card(cards, "mis1").sub.includes(fmtRatio(row.p / row.ucl)));
  // and the per-model 1MIS sentence of qreport's comments names the same value
  assert.ok(run.comments.some((c) => c.includes(`${model} ${fmtPct(row.p)}`)), "comment parity");
  assert.notEqual(card(cards, "market").value, card(cardsFor(run, null, series), "market").value);
});

test("market card reads stored modelHeadline (no arithmetic); missing -> dash", async () => {
  const { run, series } = await loaded("sample");
  for (const model of run.models) {
    const c = card(cardsFor(run, model, series), "market");
    const v = run.modelHeadline[model];
    assert.equal(c.value, fmtPct(v.level));
    assert.equal(c.sub, `전년 대비 ${fmtChange(v.changePct)}`);
  }
  const short = await loaded("user");                // 23 months -> no prior-year comparison
  assert.equal(card(cardsFor(short.run, short.run.models[0], short.series), "market").sub, "전년 비교 불가");
  const old = { ...run, modelHeadline: undefined };   // a run saved before modelHeadline existed
  const c = card(cardsFor(old, run.models[0], series), "market");
  assert.equal(c.value, "–");
  assert.match(c.sub, /모델별 값 없음/);
});

test("historyRows: newest updatedAt first, sample badge, uploader names with fallback", () => {
  const runs = [
    { id: "a", kind: "user", baseMonth: "2025-11", uploader: "u1", updatedAt: "2026-11-10T09:00:00Z", verdict: "주의", alertCount: 2 },
    { id: "b", kind: "sample", baseMonth: "2025-12", uploader: "u2", updatedAt: "2026-11-12T09:00:00Z", verdict: "개선 필요", alertCount: 7 },
    { id: "c", kind: "company", baseMonth: "2025-12", uploader: "", updatedAt: "2026-11-11T09:00:00Z", verdict: "정상", alertCount: 0 },
  ];
  const rows = historyRows(runs, { u1: "김품질", u2: "" });
  assert.deepEqual(rows.map((r) => r.id), ["b", "c", "a"]);
  assert.deepEqual(rows.map((r) => r.sample), [true, false, false]);
  assert.deepEqual(rows.map((r) => r.uploader), ["알 수 없음", "알 수 없음", "김품질"]);
  assert.equal(rows[0].baseLabel, "25.12");
  assert.equal(rows[0].alertCount, 7);
  assert.deepEqual(historyRows([]), []);
});

test("empty / signed-out states", async () => {
  assert.equal(pageState(null, []), "nodb");
  const store = await openStore(fakeClaude({}));
  const runs = await listRuns(store);
  assert.deepEqual(runs, []);
  assert.equal(pageState(store, runs), "empty");
  assert.equal(pageState(store, [{ id: "x" }]), "ready");
  assert.equal(MSG.empty, "아직 데이터가 없습니다 — 체험형 MVP에서 샘플로 분석해 보세요");
  assert.equal(MSG.nodb, "로그인한 상태에서 열어 주세요 (Claude 계정 필요)");
  assert.deepEqual(cardsFor(null), []);
});

test("chartForKey maps alert chart keys to dashboard charts", () => {
  assert.equal(chartForKey("pchart"), "pchart");
  assert.equal(chartForKey("pchart_양문형"), "pchart");
  assert.equal(chartForKey("mis_curve"), "heatmap");
  assert.equal(chartForKey("heatmap"), "heatmap");
  assert.equal(chartForKey("pareto"), "pareto");
  assert.equal(chartForKey("model_type"), "modelType");
  for (const k of ["trend", "yoy", "weibull", "", undefined]) assert.equal(chartForKey(k), "trend");
});

test("warningItems: comment sentences paired with alerts, severity order kept stable", async () => {
  const { run } = await loaded("sample");
  const items = warningItems(run);
  assert.equal(items.length, run.alerts.length);
  for (const it of items) assert.equal(it.text, run.comments[it.i]);
  const sev = items.map((i) => i.severity);
  const rank = { "높음": 0, "중간": 1, "낮음": 2 };
  assert.deepEqual(sev, [...sev].sort((a, b) => rank[a] - rank[b]));
  assert.equal(warningItems({ alerts: [{ severity: "중간", target: "A", period: "2025-12", chartKey: "pareto" }], comments: [] })[0].text, "A 2025-12");
});

test("periodRows: last 12 ending at the base month, or all", () => {
  const rows = Array.from({ length: 30 }, (_, i) => ({ m: `20${24 + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}` }));
  const r12 = periodRows(rows, "2025-12", "12");
  assert.equal(r12.length, 12);
  assert.equal(r12[0].m, "2025-01");
  assert.equal(r12[11].m, "2025-12");
  assert.equal(periodRows(rows, "2025-12", "all").length, 30);
  assert.equal(periodRows(rows.slice(0, 5), "2024-05", "12").length, 5);
});

test("every chart option carries its reference line (charts.js, fixture series)", async () => {
  const c = await import("./charts.js");
  const { run, series } = await loaded("sample");
  const opts = { models: run.models, model: null };
  const names = (o) => o.series.map((s) => s.name);
  assert.ok(["중심선", "상한"].every((n) => names(c.trendOption(series.market["전체"], opts)).includes(n)));
  const pc = c.pchartOption(series.pchart["전체"], opts);
  assert.ok(["중심선", "관리상한"].every((n) => names(pc).includes(n)));
  assert.ok(pc.series[0].data.some((d) => d.itemStyle?.color === c.C.limit), "breach points in status red");
  const hm = c.heatmapOption(series.heatmap["전체"], series.heatmapBaseline["전체"]);
  assert.equal(hm.yAxis.data[0], c.BASELINE_ROW);
  assert.ok(hm.series[0].data.some((d) => d.kind === "unobserved" && d.itemStyle.color === c.C.unobserved));
  const pa = c.paretoOption(series.pareto["전체"]);
  assert.equal(pa.series[1].markLine.data[0].yAxis, 0.8);
  const mt = c.modelTypeOption(series.modelType["전체"], opts);
  assert.equal(mt.series[0].markLine.data[0].yAxis, 0);
  assert.deepEqual(mt.series.map((s) => s.itemStyle.color), run.models.map((_, i) => c.PALETTE[i]));
});
