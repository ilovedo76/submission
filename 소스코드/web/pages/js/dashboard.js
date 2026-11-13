// One-page dashboard (index.html). Pure helpers first (unit-tested), then the DOM layer.
// Loads on open and on "새로고침" (no live subscription). Shows only stored numbers; display rounding
// via shared.js. Model filter: verdict / alerts / forecast stay overall ("전체 기준" label, spec 4.6);
// market rate (run.modelHeadline), 1MIS and the charts switch to the model's stored values.
import {
  TOTAL, NA, fmtPct, fmtRatio, fmtChange, fmtTime, ymLabel, sortRuns, defaultRun, seriesFor,
  SEVERITY_STYLE, VERDICT_STYLE,
} from "./shared.js";
import { openStore, listRuns, loadSeries, userNames } from "./store-read.js";
import { trendChart, pchartChart, heatmapChart, paretoChart, modelTypeChart, resizeAll } from "./charts.js";

export const MSG = Object.freeze({
  loading: "데이터를 불러오는 중…",
  nodb: "로그인한 상태에서 열어 주세요 (Claude 계정 필요)",
  empty: "아직 데이터가 없습니다 — 체험형 MVP에서 샘플로 분석해 보세요",
  loadError: "데이터를 불러오지 못했습니다. 잠시 후 새로고침해 주세요.",
});
export const OVERALL = "전체 기준";
const SEV_ORDER = { "높음": 0, "중간": 1, "낮음": 2 };

// ------------------------------------------------------------------ pure helpers
/** "nodb" (no store) | "empty" (no runs) | "ready". */
export function pageState(store, runs) {
  if (!store) return "nodb";
  return runs && runs.length ? "ready" : "empty";
}

/** The pchart row of the base month (else the last row). */
export function baseMisRow(rows, baseMonth) {
  return rows.find((r) => r.m === baseMonth) ?? rows[rows.length - 1] ?? null;
}

function severityLine(alerts) {
  const n = { "높음": 0, "중간": 0, "낮음": 0 };
  for (const a of alerts || []) if (a.severity in n) n[a.severity] += 1;
  return Object.entries(n).filter(([, v]) => v).map(([k, v]) => `${k} ${v}`).join(" · ");
}

/** Card row for a run. model = null | model name. series optional (needed for model values). */
export function cardsFor(run, model = null, series = null) {
  if (!run) return [];
  const h = run.headline || {};
  const scoped = model ? OVERALL : null;
  const cards = [];
  const vs = VERDICT_STYLE[run.verdict] || { color: "#363434", bg: "#e9e7e0" };
  cards.push({ key: "verdict", title: "종합 판정", value: run.verdict || NA, scope: scoped,
    sub: `경고 ${run.alertCount ?? 0}건`, style: vs });
  cards.push({ key: "alerts", title: "경고 수", value: `${run.alertCount ?? 0}건`, scope: scoped,
    sub: severityLine(run.alerts) || "경고 없음" });

  // stored engine values only: run.headline (전체) or run.modelHeadline[model] (web_entry)
  const mh = model ? run.modelHeadline?.[model] : h;
  const level = mh?.level ?? null, change = mh?.changePct ?? null;
  cards.push({ key: "market", title: "최근 12개월 시장 불량률", value: fmtPct(level), scope: model || null,
    sub: !mh ? "모델별 값 없음 (다시 분석하면 표시)" : change == null ? "전년 비교 불가" : `전년 대비 ${fmtChange(change)}`,
    tone: change > 0 ? "up" : null });

  const mis = baseMisRow(seriesFor(series, "pchart", model), run.baseMonth);
  const over = mis && mis.p != null && mis.ucl != null && mis.p > mis.ucl;
  cards.push({ key: "mis1", title: `기준월 1MIS${mis ? ` (${ymLabel(mis.m)} 생산)` : ""}`,
    value: fmtPct(mis?.p), scope: model || null,
    sub: mis && mis.ucl ? `상한 ${fmtPct(mis.ucl)} · ${fmtRatio(mis.p / mis.ucl)}${over ? " 이탈" : ""}` : "상한 –",
    tone: over ? "breach" : null });

  const hasCost = Array.isArray(h.cost) && h.cost.some((v) => v != null);
  cards.push({ key: "forecast", title: "추가 접수 예상", value: run.forecastText || NA, scope: OVERALL,
    sub: hasCost ? `예상 비용 ${run.costText}` : "AS 처리비 미입력" });
  return cards;
}

/** Upload history, newest first. names = {uploaderId: name}. */
export function historyRows(runs, names = {}) {
  return sortRuns(runs).map((r) => ({
    id: r.id,
    baseMonth: r.baseMonth,
    baseLabel: ymLabel(r.baseMonth),
    uploader: (r.uploader && names[r.uploader]) || "알 수 없음",
    time: fmtTime(r.updatedAt ?? r.createdAt),
    verdict: r.verdict || NA,
    alertCount: r.alertCount ?? 0,
    sample: r.kind === "sample",
    fileNames: r.fileNames || [],
  }));
}

/** Which dashboard chart an alert's chartKey points at. */
export function chartForKey(key) {
  const k = String(key || "");
  if (k === "pchart" || k.startsWith("pchart_")) return "pchart";
  if (k === "mis_curve" || k === "heatmap" || k.startsWith("heatmap")) return "heatmap";
  if (k === "pareto") return "pareto";
  if (k === "model_type" || k === "modelType") return "modelType";
  return "trend";
}

/** Warning list: qreport comment sentences in alert order, then stable by severity. */
export function warningItems(run) {
  const alerts = run?.alerts || [];
  const comments = run?.comments || [];
  const paired = comments.length === alerts.length;
  return alerts
    .map((a, i) => ({
      i, severity: a.severity, style: SEVERITY_STYLE[a.severity] || SEVERITY_STYLE["낮음"],
      text: paired ? comments[i] : `${a.target} ${a.period}`.trim(),
      chart: chartForKey(a.chartKey), chartKey: a.chartKey, rule: a.rule,
    }))
    .sort((a, b) => (SEV_ORDER[a.severity] ?? 9) - (SEV_ORDER[b.severity] ?? 9) || a.i - b.i);
}

/** Period filter (market, pchart): "12" -> 12 rows ending at baseMonth; "all" -> all rows. */
export function periodRows(rows, baseMonth, period) {
  if (period !== "12") return rows;
  const end = rows.findIndex((r) => r.m === baseMonth);
  const last = end < 0 ? rows.length - 1 : end;
  return rows.slice(Math.max(0, last - 11), last + 1);
}

export function runLabel(r) {
  const parts = [ymLabel(r.baseMonth), (r.fileNames || [])[0] || r.id, fmtTime(r.updatedAt ?? r.createdAt)];
  return parts.join(" · ") + (r.kind === "sample" ? " (샘플)" : "");
}

export const CHART_TITLES = Object.freeze({
  trend: "시장 불량률 추이", pchart: "1MIS P 관리도", heatmap: "생산월 × MIS 불량률",
  pareto: "불량 유형 파레토", modelType: "모델 × 유형 증감 (최근 12개월 − 전년)",
});

// ------------------------------------------------------------------ DOM layer
const $ = (doc, id) => doc.getElementById(id);

function h(doc, tag, attrs = {}, ...kids) {
  const el = doc.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) el.append(c.nodeType ? c : doc.createTextNode(String(c)));
  return el;
}

export function createDashboard(doc = document, claude = globalThis.claude) {
  const st = { store: null, runs: [], names: {}, runId: null, model: null, period: "12", series: null, seq: 0 };

  function showState(kind, text) {
    const box = $(doc, "dash-state");
    const main = $(doc, "dash-main");
    box.hidden = kind === "ready";
    main.hidden = kind !== "ready";
    box.className = `state state-${kind}`;
    box.replaceChildren();
    if (kind === "ready") return;
    box.append(h(doc, "p", {}, text));
    if (kind === "empty") box.append(h(doc, "a", { class: "btn", href: "./mvp.html" }, "체험형 MVP 열기 →"));
  }

  const current = () => st.runs.find((r) => r.id === st.runId) || null;

  function renderControls() {
    const runSel = $(doc, "f-run");
    runSel.replaceChildren(...sortRuns(st.runs).map((r) =>
      h(doc, "option", { value: r.id, selected: r.id === st.runId }, runLabel(r))));
    const run = current();
    const modelSel = $(doc, "f-model");
    modelSel.replaceChildren(h(doc, "option", { value: "" }, TOTAL),
      ...(run?.models || []).map((m) => h(doc, "option", { value: m, selected: m === st.model }, m)));
    modelSel.value = st.model || "";
    $(doc, "f-period").value = st.period;
  }

  function renderBanner(run) {
    const b = $(doc, "dash-sample");
    b.hidden = run?.kind !== "sample";
    const meta = $(doc, "dash-meta");
    meta.textContent = run ? `${run.period || ""} · 기준월 ${ymLabel(run.baseMonth)} · ${(run.fileNames || []).join(", ")}` : "";
  }

  function renderCards(run) {
    const box = $(doc, "dash-cards");
    box.replaceChildren(...cardsFor(run, st.model, st.series).map((c) => {
      const style = c.style ? { background: c.style.bg, color: c.style.color } : null;
      return h(doc, "div", { class: `card card-${c.key}${c.tone ? ` tone-${c.tone}` : ""}` },
        h(doc, "div", { class: "card-title" }, c.title,
          c.scope ? h(doc, "span", { class: c.scope === OVERALL ? "scope scope-all" : "scope" }, c.scope) : null),
        h(doc, "div", { class: "card-value", style }, c.value),
        h(doc, "div", { class: "card-sub" }, c.sub));
    }));
  }

  function renderCharts(run) {
    const s = st.series || {};
    const opts = { models: run.models || [], model: st.model };
    const market = periodRows(seriesFor(s, "market", st.model), run.baseMonth, st.period);
    const modelRows = {};
    if (!st.model) for (const m of opts.models) modelRows[m] = periodRows(seriesFor(s, "market", m), run.baseMonth, st.period);
    const draw = (id, fn, rows, o) => {
      const el = $(doc, id);
      if (!rows.length) { el.replaceChildren(h(doc, "p", { class: "chart-empty" }, "표시할 데이터가 없습니다.")); return; }
      if (el.querySelector(".chart-empty")) el.replaceChildren();
      fn(el, rows, o);
    };
    draw("c-trend", trendChart, market, { ...opts, modelRows });
    draw("c-pchart", pchartChart, periodRows(seriesFor(s, "pchart", st.model), run.baseMonth, st.period), opts);
    draw("c-heatmap", heatmapChart, seriesFor(s, "heatmap", st.model), { ...opts, baseline: seriesFor(s, "heatmapBaseline", st.model) });
    $(doc, "heatmap-note").textContent = seriesFor(s, "heatmapBaseline", st.model).length
      ? "맨 윗줄 = 전년 같은 MIS(기준선). 빨간 테두리 = 전년 같은 MIS 대비 초과. 회색 = 관찰 전."
      : "전년 데이터가 없어 기준선 줄이 없습니다. 회색 = 관찰 전.";
    draw("c-pareto", paretoChart, seriesFor(s, "pareto", st.model), opts);
    draw("c-modelType", modelTypeChart, seriesFor(s, "modelType", st.model), opts);
    $(doc, "pchart-title").textContent = `1MIS P 관리도 (${st.model || TOTAL})`;
    $(doc, "trend-title").textContent = `시장 불량률 추이 (${st.model || TOTAL})`;
  }

  function flash(chart) {
    const panel = $(doc, `panel-${chart}`);
    if (!panel) return;
    panel.scrollIntoView({ behavior: "smooth", block: "center" });
    panel.classList.remove("flash");
    void panel.offsetWidth;
    panel.classList.add("flash");
    setTimeout(() => panel.classList.remove("flash"), 1800);
  }

  function renderWarnings(run) {
    const box = $(doc, "dash-warnings");
    const items = warningItems(run);
    $(doc, "warn-count").textContent = `${items.length}건${st.model ? ` · ${OVERALL}` : ""}`;
    if (!items.length) { box.replaceChildren(h(doc, "li", { class: "warn-none" }, "경고가 없습니다.")); return; }
    box.replaceChildren(...items.map((w) => h(doc, "li", {},
      h(doc, "button", { type: "button", class: "warn", onclick: () => flash(w.chart),
        "aria-label": `${w.style.label} 경고: ${w.text} — ${CHART_TITLES[w.chart]}로 이동` },
        h(doc, "span", { class: "chip", style: { color: w.style.color, borderColor: w.style.color } },
          `${w.style.icon} ${w.style.label}`),
        h(doc, "span", { class: "warn-text" }, w.text),
        h(doc, "span", { class: "warn-go" }, `${CHART_TITLES[w.chart]} →`)))));
  }

  function renderHistory() {
    const body = $(doc, "dash-history");
    body.replaceChildren(...historyRows(st.runs, st.names).map((r) => {
      const go = () => selectRun(r.id);
      return h(doc, "tr", { class: r.id === st.runId ? "current" : null, tabindex: "0", onclick: go,
        onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } } },
        h(doc, "td", {}, r.baseLabel, r.sample ? h(doc, "span", { class: "badge-sample" }, "샘플") : null),
        h(doc, "td", {}, r.uploader),
        h(doc, "td", { class: "num" }, r.time),
        h(doc, "td", {}, h(doc, "span", { class: "verdict-dot", style: { background: (VERDICT_STYLE[r.verdict] || {}).bg || "#999" } }), r.verdict),
        h(doc, "td", { class: "num" }, `${r.alertCount}건`));
    }));
  }

  function renderAll() {
    const run = current();
    renderControls();
    renderHistory();
    if (!run) return;
    renderBanner(run);
    renderCards(run);
    renderWarnings(run);
    if (st.series) renderCharts(run);
  }

  async function selectRun(id) {
    if (!id) return;
    const seq = ++st.seq;
    st.runId = id;
    const run = current();
    if (st.model && !(run?.models || []).includes(st.model)) st.model = null;
    st.series = null;
    $(doc, "dash-charts").classList.add("loading");
    renderAll();
    try {
      const series = await loadSeries(st.store, id, run);
      if (seq !== st.seq) return;
      st.series = series;
      $(doc, "dash-charts").classList.remove("loading");
      renderAll();
    } catch (e) {
      if (seq !== st.seq) return;
      console.error(e);
      $(doc, "dash-charts").classList.remove("loading");
      $(doc, "dash-chart-error").hidden = false;
    }
  }

  async function load() {
    showState("loading", MSG.loading);
    try {
      if (!st.store) st.store = await openStore(claude);
      if (!st.store) { showState("nodb", MSG.nodb); return; }
      st.runs = await listRuns(st.store);
    } catch (e) {
      console.error(e);
      showState("error", MSG.loadError);
      return;
    }
    const state = pageState(st.store, st.runs);
    if (state === "empty") { showState("empty", MSG.empty); return; }
    showState("ready");
    st.names = await userNames(st.store, st.runs.map((r) => r.uploader));
    const keep = st.runs.some((r) => r.id === st.runId) ? st.runId : defaultRun(st.runs).id;
    $(doc, "dash-chart-error").hidden = true;
    await selectRun(keep);
  }

  function wire() {
    $(doc, "f-run").addEventListener("change", (e) => selectRun(e.target.value));
    $(doc, "f-model").addEventListener("change", (e) => { st.model = e.target.value || null; renderAll(); });
    $(doc, "f-period").addEventListener("change", (e) => { st.period = e.target.value; renderAll(); });
    $(doc, "btn-refresh").addEventListener("click", () => load());
    let t;
    globalThis.addEventListener?.("resize", () => { clearTimeout(t); t = setTimeout(resizeAll, 120); });
  }

  return { state: st, load, wire, selectRun };
}

/** Entry point used by index.html. */
export function start(doc = document, claude = globalThis.claude) {
  const d = createDashboard(doc, claude);
  d.wire();
  d.load();
  return d;
}
