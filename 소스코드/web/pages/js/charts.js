// Interactive dashboard charts (ECharts 5.6.0 UMD, global `echarts`, loaded by index.html).
// Numbers come only from the stored series; JS formats them (shared.js) and never computes statistics.
// Rules (qreport chart-style): every chart carries its reference line, "관찰 전" cells are gray,
// categorical colors are fixed per entity in run.models order, status red only for limits/breaches,
// one y-axis per chart, a legend from two series up, hover tooltips. Single light look (qreport theme).
import { fmtPct, fmtInt, ymLabel } from "./shared.js";

export const PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
export const C = Object.freeze({
  ink: "#363434", muted: "#6b6865", grid: "#d9d6cd", paper: "#fbfaf6",
  limit: "#d03b3b", center: "#6b6865", unobserved: "#cfcdc6", unobservedText: "#5d5a56",
});
const FONT = '"Noto Sans KR", "Malgun Gothic", "Apple SD Gothic Neo", sans-serif';

/** Fixed color of an entity (model) by its position in the run's model list. */
export function colorFor(entity, entities) {
  const i = (entities || []).indexOf(entity);
  return PALETTE[(i < 0 ? 0 : i) % PALETTE.length];
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function base(extra = {}) {
  return {
    animation: false,
    textStyle: { fontFamily: FONT, color: C.ink },
    grid: { left: 8, right: 16, top: 40, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis", confine: true, backgroundColor: "#ffffff", borderColor: C.grid,
      textStyle: { color: C.ink, fontFamily: FONT, fontSize: 12 },
    },
    ...extra,
  };
}

const legend = (show) => ({
  show, top: 0, left: 0, type: "scroll", itemWidth: 16, itemHeight: 8,
  textStyle: { color: C.ink, fontFamily: FONT, fontSize: 11 },
});

const pctAxis = () => ({
  type: "value", scale: false, splitLine: { lineStyle: { color: C.grid } },
  axisLabel: { color: C.muted, fontSize: 10, formatter: (v) => fmtPct(v) },
});

const monthAxis = (months) => ({
  type: "category", data: months.map(ymLabel), boundaryGap: false,
  axisLine: { lineStyle: { color: C.muted } }, axisLabel: { color: C.muted, fontSize: 10 },
});

const refLine = (name, data, color, type = "dashed", step = false) => ({
  name, type: "line", data, step: step ? "middle" : false, symbol: "none", z: 1,
  lineStyle: { color, width: 1.5, type }, itemStyle: { color }, emphasis: { disabled: true },
});

function axisTooltip(lines) {
  return (params) => {
    const ps = [].concat(params);
    if (!ps.length) return "";
    return `<b>${esc(ps[0].axisValueLabel ?? ps[0].name)}</b><br>` + lines(ps).join("<br>");
  };
}
const dot = (color) => `<span style="display:inline-block;width:8px;height:8px;border-radius:4px;background:${color};margin-right:4px"></span>`;

// ------------------------------------------------------------------ market trend
/** rows: market rows of the shown scope; opts.models = run.models, opts.model = selected model | null,
 *  opts.modelRows = {model: rows} drawn as thin lines when model is null (same months). */
export function trendOption(rows, opts = {}) {
  const months = rows.map((r) => r.m);
  const at = (rs) => { const by = new Map(rs.map((r) => [r.m, r.rate])); return months.map((m) => by.get(m) ?? null); };
  const mainName = opts.model || "전체";
  const mainColor = opts.model ? colorFor(opts.model, opts.models) : C.ink;
  const series = [
    { name: mainName, type: "line", data: rows.map((r) => r.rate), symbol: "circle", symbolSize: 5, z: 3,
      lineStyle: { color: mainColor, width: 2.5 }, itemStyle: { color: mainColor } },
  ];
  if (!opts.model && opts.modelRows) {
    for (const m of opts.models || []) {
      const rs = opts.modelRows[m];
      if (!rs) continue;
      const col = colorFor(m, opts.models);
      series.push({ name: m, type: "line", data: at(rs), symbol: "none", z: 2,
        lineStyle: { color: col, width: 1.2 }, itemStyle: { color: col } });
    }
  }
  series.push(refLine("중심선", rows.map((r) => r.pbar), C.center));
  series.push(refLine("상한", rows.map((r) => r.ucl), C.limit, "dashed", true));
  const byMonth = new Map(rows.map((r) => [r.m, r]));
  return base({
    legend: legend(series.length >= 2),
    xAxis: monthAxis(months),
    yAxis: pctAxis(),
    tooltip: { ...base().tooltip, formatter: axisTooltip((ps) => {
      const r = byMonth.get(months[ps[0].dataIndex]);
      const out = ps.map((p) => `${dot(p.color)}${esc(p.seriesName)} ${fmtPct(p.value)}`);
      if (r) out.push(`${esc(mainName)} 불량 ${fmtInt(r.defects)}건 / 판매 ${fmtInt(r.sales)}`);
      return out;
    }) },
    series,
  });
}

// ------------------------------------------------------------------ 1MIS P chart
export function pchartOption(rows, opts = {}) {
  const months = rows.map((r) => r.m);
  const color = opts.model ? colorFor(opts.model, opts.models) : C.ink;
  const pts = rows.map((r) => (r.out
    ? { value: r.p, symbol: "circle", symbolSize: 10, itemStyle: { color: C.limit, borderColor: "#ffffff", borderWidth: 1.5 } }
    : r.run ? { value: r.p, symbolSize: 8, itemStyle: { color: "#ffffff", borderColor: C.limit, borderWidth: 2 } }
      : { value: r.p }));
  return base({
    legend: { ...legend(true), data: ["1MIS 불량률", "중심선", "관리상한", "상한 이탈"] },
    xAxis: monthAxis(months),
    yAxis: pctAxis(),
    tooltip: { ...base().tooltip, formatter: axisTooltip((ps) => {
      const r = rows[ps[0].dataIndex];
      if (!r) return [];
      const flag = r.out ? `<br><b style="color:${C.limit}">관리상한 이탈</b>` : r.run ? `<br><b style="color:${C.limit}">연속 규칙</b>` : "";
      return [`1MIS ${fmtPct(r.p)} (${fmtInt(r.x)}건 / ${fmtInt(r.n)}대)`, `중심선 ${fmtPct(r.pbar)}`,
        `관리상한 ${fmtPct(r.ucl)}${flag}`];
    }) },
    series: [
      { name: "1MIS 불량률", type: "line", data: pts, symbol: "circle", symbolSize: 5, z: 3,
        lineStyle: { color, width: 2.2 }, itemStyle: { color } },
      refLine("중심선", rows.map((r) => r.pbar), C.center),
      refLine("관리상한", rows.map((r) => r.ucl), C.limit, "dashed", true),
      // legend-only entry for the breach marker
      { name: "상한 이탈", type: "scatter", data: [], itemStyle: { color: C.limit } },
    ],
  });
}

// ------------------------------------------------------------------ production month x MIS heatmap
const SEQ = ["#f3f6fb", "#c9dcf3", "#8db8e8", "#4d8fd8", "#2a78d6", "#14508f"];
function seqColor(t) {
  const x = Math.max(0, Math.min(1, t)) * (SEQ.length - 1);
  const i = Math.min(SEQ.length - 2, Math.floor(x));
  const f = x - i;
  const a = SEQ[i].match(/\w\w/g).map((h) => parseInt(h, 16));
  const b = SEQ[i + 1].match(/\w\w/g).map((h) => parseInt(h, 16));
  return "#" + a.map((v, j) => Math.round(v + (b[j] - v) * f).toString(16).padStart(2, "0")).join("");
}

export const BASELINE_ROW = "전년 같은 MIS";

/** rows: heatmap rows {m, k, rate, observed, highlight}; baseline: heatmapBaseline rows {k, rate}. */
export function heatmapOption(rows, baseline = [], opts = {}) {
  const ks = [...new Set(rows.map((r) => r.k).concat(baseline.map((b) => b.k)))].sort((a, b) => a - b);
  const months = [...new Set(rows.map((r) => r.m))];
  const yCats = (baseline.length ? [BASELINE_ROW] : []).concat(months.map(ymLabel));
  const rates = rows.filter((r) => r.observed && r.rate != null).map((r) => r.rate)
    .concat(baseline.map((b) => b.rate).filter((v) => v != null));
  const hi = rates.length ? Math.max(...rates) : 0;
  const cell = (xi, yi, rate, extra = {}) => {
    const t = hi > 0 && rate != null ? rate / hi : 0;
    return { value: [xi, yi, rate], itemStyle: { color: seqColor(t), borderColor: "#ffffff", borderWidth: 1 },
      label: { color: t > 0.55 ? "#ffffff" : C.ink }, ...extra };
  };
  const data = [];
  baseline.forEach((b) => {
    const xi = ks.indexOf(b.k);
    data.push(cell(xi, 0, b.rate, { kind: "baseline", itemStyle: {
      color: seqColor(hi > 0 && b.rate != null ? b.rate / hi : 0), borderColor: C.ink, borderWidth: 1.5 } }));
  });
  const off = baseline.length ? 1 : 0;
  for (const r of rows) {
    const xi = ks.indexOf(r.k), yi = months.indexOf(r.m) + off;
    if (!r.observed) {
      data.push({ value: [xi, yi, null], kind: "unobserved", observed: false,
        itemStyle: { color: C.unobserved, borderColor: "#ffffff", borderWidth: 1 },
        label: { color: C.unobservedText } });
    } else if (r.highlight) {
      data.push(cell(xi, yi, r.rate, { kind: "highlight", itemStyle: {
        color: seqColor(hi > 0 && r.rate != null ? r.rate / hi : 0), borderColor: C.limit, borderWidth: 3 } }));
    } else {
      data.push(cell(xi, yi, r.rate));
    }
  }
  return base({
    grid: { left: 8, right: 8, top: 8, bottom: 8, containLabel: true },
    tooltip: {
      ...base().tooltip, trigger: "item",
      formatter: (p) => {
        const d = p.data;
        const [xi, yi] = d.value;
        const head = `<b>${esc(yCats[yi])} · ${ks[xi]}MIS</b><br>`;
        if (d.kind === "unobserved") return head + "관찰 전 (아직 경과하지 않은 MIS)";
        const note = d.kind === "highlight" ? `<br><b style="color:${C.limit}">전년 같은 MIS 대비 초과</b>` : "";
        return head + `불량률 ${fmtPct(d.value[2])}` + note;
      },
    },
    xAxis: { type: "category", data: ks.map((k) => `${k}MIS`), position: "top", splitArea: { show: false },
      axisLabel: { color: C.muted, fontSize: 10 }, axisLine: { show: false }, axisTick: { show: false } },
    yAxis: { type: "category", data: yCats, inverse: true,
      axisLabel: { color: C.ink, fontSize: 10 },
      axisLine: { show: false }, axisTick: { show: false } },
    series: [{
      type: "heatmap", data,
      label: { show: true, fontSize: opts.compact ? 8 : 9, fontFamily: FONT,
        formatter: (p) => (p.data.kind === "unobserved" ? "관찰 전" : fmtPct(p.data.value[2])) },
      emphasis: { itemStyle: { borderColor: C.ink, borderWidth: 2 } },
    }],
  });
}

// ------------------------------------------------------------------ type pareto
/** rows: pareto rows {type, count, cum}. One axis (share of cases, %): bars = each type's share
 *  (cum steps), line = cumulative share, 80 % reference line. */
export function paretoOption(rows) {
  const types = rows.map((r) => r.type);
  const share = rows.map((r, i) => (r.cum == null ? null : r.cum - (i ? rows[i - 1].cum ?? 0 : 0)));
  return base({
    legend: legend(true),
    grid: { left: 8, right: 16, top: 40, bottom: 8, containLabel: true },
    xAxis: { type: "category", data: types, axisLabel: { color: C.ink, fontSize: 10, interval: 0, rotate: types.length > 6 ? 30 : 0 },
      axisLine: { lineStyle: { color: C.muted } } },
    yAxis: { type: "value", max: 1, splitLine: { lineStyle: { color: C.grid } },
      axisLabel: { color: C.muted, fontSize: 10, formatter: (v) => `${Math.round(v * 100)}%` } },
    tooltip: { ...base().tooltip, formatter: axisTooltip((ps) => {
      const r = rows[ps[0].dataIndex];
      return r ? [`${fmtInt(r.count)}건 · 비중 ${fmtPct(share[ps[0].dataIndex])}`, `누적 ${fmtPct(r.cum)}`] : [];
    }) },
    series: [
      { name: "유형 비중", type: "bar", data: share, barMaxWidth: 36, itemStyle: { color: PALETTE[0] },
        label: { show: true, position: "top", fontSize: 9, color: C.ink, formatter: (p) => `${fmtInt(rows[p.dataIndex].count)}건` } },
      { name: "누적 비중", type: "line", data: rows.map((r) => r.cum), symbol: "circle", symbolSize: 5,
        lineStyle: { color: C.ink, width: 2 }, itemStyle: { color: C.ink },
        markLine: { symbol: "none", silent: true, data: [{ yAxis: 0.8, name: "80%" }],
          lineStyle: { color: C.center, type: "dashed", width: 1.5 },
          label: { formatter: "80% 누적", color: C.muted, fontSize: 10, position: "insideEndTop" } } },
    ],
  });
}

// ------------------------------------------------------------------ model x type change
/** rows: modelType rows {model, type, recent, prior, diff}; bars = diff (recent 12 - prior 12) per model. */
export function modelTypeOption(rows, opts = {}) {
  const types = [...new Set(rows.map((r) => r.type))];
  const order = opts.models || [];
  const models = [...new Set(rows.map((r) => r.model))].sort((a, b) => order.indexOf(a) - order.indexOf(b));
  const key = new Map(rows.map((r) => [`${r.model}\u0000${r.type}`, r]));
  const series = models.map((m, i) => ({
    name: m, type: "bar", barMaxWidth: 18,
    data: types.map((t) => key.get(`${m}\u0000${t}`)?.diff ?? null),
    itemStyle: { color: colorFor(m, order.length ? order : models) },
    ...(i === 0 ? { markLine: { symbol: "none", silent: true, data: [{ yAxis: 0 }],
      lineStyle: { color: C.ink, type: "solid", width: 1.5 }, label: { show: false } } } : {}),
  }));
  return base({
    legend: legend(series.length >= 2),
    xAxis: { type: "category", data: types, axisLabel: { color: C.ink, fontSize: 10, interval: 0, rotate: types.length > 6 ? 30 : 0 },
      axisLine: { onZero: false, lineStyle: { color: C.muted } } },
    yAxis: { type: "value",
      splitLine: { lineStyle: { color: C.grid } }, axisLabel: { color: C.muted, fontSize: 10 } },
    tooltip: { ...base().tooltip, formatter: axisTooltip((ps) => ps.map((p) => {
      const r = key.get(`${p.seriesName}\u0000${types[p.dataIndex]}`);
      if (!r) return `${dot(p.color)}${esc(p.seriesName)} –`;
      const sign = r.diff > 0 ? "+" : "";
      return `${dot(p.color)}${esc(r.model)} ${sign}${fmtInt(r.diff)}건 (최근 ${fmtInt(r.recent)} / 전년 ${fmtInt(r.prior)})`;
    })) },
    series,
  });
}

// ------------------------------------------------------------------ mounting
const mounted = new Map();

function mount(el, option) {
  const ec = globalThis.echarts;
  if (!ec) { el.textContent = "차트 라이브러리를 불러오지 못했습니다."; return null; }
  let chart = mounted.get(el);
  if (!chart || chart.isDisposed?.()) {
    chart = ec.init(el, null, { renderer: "canvas" });
    mounted.set(el, chart);
  }
  chart.setOption(option, true);
  return chart;
}

export function resizeAll() { for (const c of mounted.values()) c.resize(); }

export const trendChart = (el, rows, opts) => mount(el, trendOption(rows, opts));
export const pchartChart = (el, rows, opts) => mount(el, pchartOption(rows, opts));
export const heatmapChart = (el, rows, opts = {}) => mount(el, heatmapOption(rows, opts.baseline || [], opts));
export const paretoChart = (el, rows, opts) => mount(el, paretoOption(rows, opts));
export const modelTypeChart = (el, rows, opts) => mount(el, modelTypeOption(rows, opts));
