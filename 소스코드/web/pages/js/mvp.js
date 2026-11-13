// 체험형 MVP page: upload -> analyze in the browser (Pyodide worker) -> results, notes, downloads,
// and a best-effort save to the shared db. Pure helpers are exported for node --test; the page
// wiring runs only in a browser (shared.js / store-read.js are imported lazily there).
import { loadEngine } from "./engine.js";
import { fetchPublished } from "./binfetch.js";
import { saveRun, findRun, saveNotes } from "./store-write.js";

// ------------------------------------------------------------------ pure helpers
export const NEEDS_MAPPING_MESSAGE =
  "엑셀의 열 이름을 인식하지 못했습니다. 기업 양식(RawData·생산량·판매량)과 같은 열 이름인지 확인해 주세요.";
export const PLAN_KEYS = ["담당", "일정", "대책"];
const TOTAL = "전체";

const SAVE_TEXT = {
  saving: "대시보드에 저장하는 중…",
  saved: "대시보드에 저장됨",
  no_permission: "저장 권한이 없어 대시보드에는 반영되지 않습니다 (참여 권한 필요)",
  offline: "로그인하지 않아 저장되지 않습니다",
  error: "저장 중 오류가 발생해 대시보드에 반영되지 않았습니다",
};
export function saveStatusText(status) {
  return SAVE_TEXT[status] ?? SAVE_TEXT.error;
}

/** Form values -> notes {issues, plan[]} (plan cells trimmed, empty rows dropped). */
export function notesFromForm(issues, rows) {
  const plan = [];
  for (const r of rows || []) {
    const item = Object.fromEntries(PLAN_KEYS.map((k) => [k, String(r?.[k] ?? "").trim()]));
    if (PLAN_KEYS.some((k) => item[k])) plan.push(item);
  }
  return { issues: String(issues ?? ""), plan };
}

/** Stored notes -> plan rows for the form (at least one empty row). */
export function planRows(notes) {
  const rows = (notes?.plan || []).map((p) => Object.fromEntries(PLAN_KEYS.map((k) => [k, String(p?.[k] ?? "")])));
  return rows.length ? rows : [Object.fromEntries(PLAN_KEYS.map((k) => [k, ""]))];
}

export function reportFilename(docNo, fmt) {
  const stem = String(docNo || "").replace(/[\\/:*?"<>|]/g, "_").trim() || "품질보고서";
  return `${stem}.${fmt}`;
}

const CHART_LABELS = {
  trend: "시장 불량률 추이",
  pchart: "1MIS P 관리도",
  mis_curve: "MIS 누적 불량률 곡선",
  heatmap: "생산월×MIS 히트맵",
  pareto: "불량 유형 파레토",
  model_type: "모델×유형 증감",
  histogram: "시장 불량률 분포",
  boxplot: "모델별 분포",
  weibull: "와이블 β",
  yoy: "전년 대비",
};
export function chartLabel(key) {
  if (CHART_LABELS[key]) return CHART_LABELS[key];
  if (key.startsWith("pchart_")) return `1MIS P 관리도 · ${key.slice(7)}`;
  return key;
}

/** Alert chart key -> a key present in the current chart set. */
export function chartKeyFor(key, keys) {
  if (keys.includes(key)) return key;
  if (key && key.startsWith("pchart") && keys.includes("pchart")) return "pchart";
  return keys[0];
}

/** SEVERITY_STYLE / VERDICT_STYLE entry (qreport tuple form or object form) -> {mark, label, color, bg}. */
export function styleOf(entry, label = "") {
  if (Array.isArray(entry)) {
    return entry.length >= 4
      ? { mark: entry[0], label: entry[1], color: entry[2], bg: entry[3] }
      : { mark: entry[0], label, color: entry[1], bg: entry[2] };
  }
  if (entry && typeof entry === "object") {
    const bg = entry.bg ?? entry.background;
    const color = entry.color ?? entry.fg ?? "#363434";
    // accent-only form ({icon, label, color}): an outlined chip in that color
    return bg ? { mark: entry.mark ?? entry.icon ?? "", label: entry.label ?? label, color, bg }
      : { mark: entry.mark ?? entry.icon ?? "", label: entry.label ?? label, color, bg: "transparent", border: color };
  }
  return { mark: "", label, color: "#363434", bg: "#dddcd7" };
}

export function excelFiles(files) {
  return [...(files || [])].filter((f) => /\.(xlsx|xlsm)$/i.test(f.name));
}

// Same values as qreport app/viewmodel.py; used only if shared.js cannot be loaded.
const FALLBACK_SEVERITY = {
  "높음": ["■", "높음", "#f6f4ee", "#cf2e2a"],
  "중간": ["▲", "중간", "#363434", "#e0a526"],
  "낮음": ["○", "낮음", "#57514a", "#dddcd7"],
};
const FALLBACK_VERDICT = {
  "개선 필요": ["■", "#f6f4ee", "#cf2e2a"],
  "주의": ["▲", "#363434", "#e0a526"],
  "정상": ["●", "#f6f4ee", "#1f5f5b"],
};

// ------------------------------------------------------------------ page
const NOTES_DEBOUNCE_MS = 1500;

function boot() {
  const $ = (id) => document.getElementById(id);
  const el = {
    engine: $("engine"), engineText: $("engine-text"), engineBar: $("engine-bar"), retry: $("engine-retry"),
    drop: $("drop"), fileInput: $("file-input"), fileList: $("file-list"),
    sampleRun: $("sample-run"), sampleSave: $("sample-save"),
    form: $("analyze-form"), base: $("base"), asCost: $("as-cost"), analyze: $("analyze"),
    message: $("message"), results: $("results"), saveChip: $("save-chip"), banner: $("sample-banner"),
    validation: $("validation"), verdict: $("verdict"), verdictMark: $("verdict-mark"),
    verdictWord: $("verdict-word"), verdictLine: $("verdict-line"), forecast: $("forecast"), cost: $("cost"),
    alertCount: $("alert-count"), alerts: $("alerts"),
    charts: $("charts"), model: $("model"), chartKeys: $("chart-keys"), chartImg: $("chart-img"),
    chartWait: $("chart-wait"),
    issues: $("issues"), planBody: $("plan-body"), planAdd: $("plan-add"), notesStatus: $("notes-status"),
    downButtons: $("down-buttons"), downNote: $("down-note"),
  };

  const S = {
    engine: null, store: null, downloads: undefined, manifest: null,
    severity: FALLBACK_SEVERITY, verdict: FALLBACK_VERDICT,
    files: [], kind: "user", run: null, docs: null, runId: null, saveStatus: null,
    model: TOTAL, pngs: null, key: null, busy: false, notesTimer: null, chartReq: 0,
  };

  // ---------------- capabilities (never block the page)
  import("./shared.js").then((m) => {
    if (m.SEVERITY_STYLE) S.severity = m.SEVERITY_STYLE;
    if (m.VERDICT_STYLE) S.verdict = m.VERDICT_STYLE;
    if (S.run) renderResults();
  }).catch((e) => console.warn("shared.js unavailable", e));

  const storeReady = import("./store-read.js")
    .then((m) => m.openStore())
    .then((s) => (S.store = s || null))
    .catch((e) => { console.warn("store unavailable", e); return (S.store = null); });

  const useCap = async (name) => {
    try {
      const c = globalThis.claude;
      if (!c || typeof c.use !== "function") return null;
      return await Promise.race([c.use(name), new Promise((r) => setTimeout(() => r(null), 10000))]);
    } catch { return null; }
  };
  useCap("downloads").then((d) => { S.downloads = d || null; renderDownloads(); });

  // ---------------- engine
  const renderEngine = (st) => {
    el.engine.classList.toggle("ready", st.state === "ready");
    el.engine.classList.toggle("failed", st.state === "failed");
    if (st.state === "loading") {
      el.engineText.textContent = st.text;
      el.engineBar.style.width = `${Math.round((st.step / st.total) * 100)}%`;
    } else if (st.state === "ready") {
      el.engineText.textContent = "분석 엔진 준비 완료 — 파일을 올리거나 샘플로 해 보세요.";
    } else {
      S.engine = null;                               // a dead worker never takes new work
      el.engineText.textContent = st.message;
      el.retry.hidden = false;
    }
    updateControls();
  };
  el.retry.addEventListener("click", () => location.reload());
  loadEngine(renderEngine).then((api) => { S.engine = api; updateControls(); }).catch(() => {});

  function updateControls() {
    const ok = !!S.engine && !S.busy;
    el.fileInput.disabled = !ok;
    el.drop.classList.toggle("disabled", !ok);
    el.sampleRun.disabled = !ok;
    el.analyze.disabled = !ok || S.files.length === 0;
    el.base.disabled = !ok || !S.run;
    el.downButtons.querySelectorAll("button").forEach((b) => (b.disabled = !ok || !S.run));
  }

  function showMessage(text, info = false) {
    el.message.hidden = !text;
    el.message.textContent = text || "";
    el.message.classList.toggle("info", info);
  }

  // ---------------- manifest / sample
  async function manifest() {
    if (!S.manifest) {
      const res = await fetch(new URL("manifest.json", location.href));
      if (!res.ok) throw new Error(`manifest.json: HTTP ${res.status}`);
      S.manifest = await res.json();
    }
    return S.manifest;
  }
  async function sampleFile() {
    const m = await manifest();
    let bytes;
    try {
      bytes = await fetchPublished(m.sample, m, location.href);   // base64 parts (manifest.binaries)
    } catch (e) {
      throw new Error(`샘플 파일을 불러오지 못했습니다 (${e.message})`);
    }
    return { name: m.sample.split("/").pop(), bytes };
  }
  manifest().then((m) => { el.sampleSave.dataset.ready = m.sample ? "1" : ""; renderDownloads(); })
    .catch((e) => console.warn("manifest", e));

  el.sampleRun.addEventListener("click", async () => {
    try {
      const f = await sampleFile();
      setFiles([f], "sample");
      await analyze(null);
    } catch (e) {
      showMessage(e.message);
    }
  });
  el.sampleSave.addEventListener("click", async () => {
    try {
      const f = await sampleFile();
      await S.downloads.save({ filename: f.name, data: f.bytes });
    } catch (e) {
      if (e?.code !== "declined") showMessage(`샘플 파일을 내려받지 못했습니다: ${e?.message || e}`);
    }
  });

  // ---------------- upload
  async function readFiles(list) {
    const files = excelFiles(list);
    if (!files.length) {
      showMessage(".xlsx 또는 .xlsm 엑셀 파일만 올릴 수 있습니다.");
      return;
    }
    const read = await Promise.all(files.map(async (f) => ({ name: f.name, bytes: new Uint8Array(await f.arrayBuffer()) })));
    setFiles(read, "user");
    showMessage("");
  }
  function setFiles(files, kind) {
    S.files = files;
    S.kind = kind;
    el.fileList.replaceChildren(...files.map((f) => {
      const li = document.createElement("li");
      li.textContent = `${f.name} (${Math.max(1, Math.round(f.bytes.length / 1024)).toLocaleString("ko-KR")} KB)`;
      return li;
    }));
    el.base.replaceChildren(new Option("자동 (마지막 발생월)", ""));
    updateControls();
  }
  el.fileInput.addEventListener("change", () => {
    if (el.fileInput.files.length) readFiles(el.fileInput.files).catch((e) => showMessage(e.message));
    el.fileInput.value = "";
  });
  ["dragenter", "dragover"].forEach((t) => el.drop.addEventListener(t, (e) => {
    e.preventDefault();
    if (!el.fileInput.disabled) el.drop.classList.add("over");
  }));
  ["dragleave", "drop"].forEach((t) => el.drop.addEventListener(t, () => el.drop.classList.remove("over")));
  el.drop.addEventListener("drop", (e) => {
    e.preventDefault();
    if (el.fileInput.disabled) return;
    readFiles(e.dataTransfer?.files || []).catch((err) => showMessage(err.message));
  });
  // a file dropped outside the zone must not navigate away
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());

  // ---------------- analyze
  el.form.addEventListener("submit", (e) => {
    e.preventDefault();
    analyze(el.base.value || null);
  });
  el.base.addEventListener("change", () => { if (S.run) analyze(el.base.value || null); });

  async function analyze(base) {
    if (!S.engine || S.busy || !S.files.length) return;
    S.busy = true;
    updateControls();
    showMessage("분석 중입니다… 데이터 크기에 따라 수십 초가 걸릴 수 있습니다.", true);
    const prevNotes = currentNotes();
    try {
      const res = await S.engine.analyze(S.files, base, el.asCost.value || null, S.kind, null);
      if (!res.ok) {
        S.run = null;
        el.results.hidden = true;
        showMessage(res.needs_mapping ? NEEDS_MAPPING_MESSAGE : res.error || "분석하지 못했습니다.");
        if (res.detail) console.warn(res.detail);
        return;
      }
      showMessage("");
      S.run = res.run;
      S.docs = res.docs;
      S.runId = res.docs[0][1].runId;
      S.saveStatus = null;
      fillBase(res.run);
      renderResults();
      fillNotes(prevNotes);
      el.notesStatus.textContent = "";
      loadCharts(TOTAL);
      save(prevNotes);
    } catch (e) {
      S.run = null;
      el.results.hidden = true;
      showMessage(`분석 중 오류가 발생했습니다: ${e.message}`);
    } finally {
      S.busy = false;
      updateControls();
    }
  }

  function fillBase(run) {
    const opts = (run.baseOptions || []).slice().reverse().map((m) => new Option(m, m));
    el.base.replaceChildren(...opts);
    el.base.value = run.baseMonth;
  }

  async function save(formNotes) {
    const runId = S.runId;
    renderSave("saving");
    await storeReady;
    if (runId !== S.runId) return;
    const existing = await findRun(S.store, runId);
    if (runId !== S.runId) return;
    if (existing?.notes) fillNotes(existing.notes);
    const status = await saveRun(S.store, { run: S.run, docs: S.docs },
      { kind: S.kind, fileNames: S.files.map((f) => f.name), notes: formNotes });
    if (runId === S.runId) renderSave(status);
  }

  function renderSave(status) {
    S.saveStatus = status;
    el.saveChip.hidden = false;
    el.saveChip.textContent = saveStatusText(status);
    el.saveChip.className = `chip ${status === "saved" ? "ok" : status === "saving" ? "" : "warn"}`;
  }

  // ---------------- results
  function renderResults() {
    const run = S.run;
    if (!run) return;
    el.results.hidden = false;
    el.banner.hidden = S.kind !== "sample";
    el.validation.textContent = run.validation || "";
    const v = styleOf(S.verdict[run.verdict], run.verdict);
    el.verdict.style.background = v.bg;
    el.verdict.style.color = v.color;
    el.verdictMark.textContent = v.mark;
    el.verdictWord.textContent = run.verdict;
    el.verdictLine.textContent = run.verdictLine || "";
    el.forecast.textContent = run.forecastText || "-";
    el.cost.textContent = run.costText || "-";

    const alerts = run.alerts || [];
    el.alertCount.textContent = `${alerts.length}건`;
    el.alerts.replaceChildren(...(alerts.length ? alerts.map((a, i) => alertCard(a, (run.comments || [])[i])) : [emptyAlert()]));

    el.model.replaceChildren(...[TOTAL, ...(run.models || [])].map((m) => new Option(m, m)));
    el.model.value = S.model;
  }
  function emptyAlert() {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "경고가 없습니다.";
    return p;
  }
  function alertCard(a, text) {
    const s = styleOf(S.severity[a.severity], a.severity);
    const card = document.createElement("div");
    card.className = "alert";
    const chip = document.createElement("span");
    chip.className = "sev";
    chip.style.background = s.bg;
    chip.style.color = s.color;
    if (s.border) chip.style.border = `1px solid ${s.border}`;
    chip.textContent = `${s.mark} ${s.label || a.severity}`;
    const p = document.createElement("p");
    p.textContent = text || `${a.rule} · ${a.target} · ${a.period}`;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn ghost";
    btn.textContent = "근거 차트 보기";
    btn.addEventListener("click", () => showChart(a.chartKey));
    card.append(chip, p, btn);
    return card;
  }

  // ---------------- charts
  el.model.addEventListener("change", () => loadCharts(el.model.value));

  async function loadCharts(model, wantKey = null) {
    const req = ++S.chartReq;
    S.model = model;
    el.model.value = model;
    el.chartWait.hidden = false;
    el.chartWait.textContent = "차트를 그리는 중…";
    el.chartImg.hidden = true;
    try {
      const pngs = await S.engine.charts(model === TOTAL ? null : model,
        { inputHash: S.run.inputHash, baseMonth: S.run.baseMonth });
      if (req !== S.chartReq) return;
      S.pngs = pngs;
      const keys = Object.keys(pngs);
      renderKeys(keys, chartKeyFor(wantKey || S.key, keys));
    } catch (e) {
      if (req === S.chartReq) el.chartWait.textContent = `차트를 그리지 못했습니다: ${e.message}`;
    }
  }
  function renderKeys(keys, selected) {
    el.chartKeys.replaceChildren(...keys.map((k) => {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "chart-key";
      input.value = k;
      input.checked = k === selected;
      input.addEventListener("change", () => selectKey(k));
      label.append(input, document.createTextNode(chartLabel(k)));
      return label;
    }));
    selectKey(selected);
  }
  function selectKey(k) {
    if (!S.pngs || !S.pngs[k]) return;
    S.key = k;
    el.chartImg.src = `data:image/png;base64,${S.pngs[k]}`;
    el.chartImg.alt = chartLabel(k);
    el.chartImg.hidden = false;
    el.chartWait.hidden = true;
    const input = el.chartKeys.querySelector(`input[value="${CSS.escape(k)}"]`);
    if (input) input.checked = true;
  }
  async function showChart(key) {
    if (S.model !== TOTAL || !S.pngs) await loadCharts(TOTAL, key);
    else selectKey(chartKeyFor(key, Object.keys(S.pngs)));
    el.charts.scrollIntoView({ behavior: "smooth", block: "start" });
    el.charts.classList.add("flash");
    setTimeout(() => el.charts.classList.remove("flash"), 1200);
  }

  // ---------------- notes
  function planRow(r) {
    const tr = document.createElement("tr");
    for (const k of PLAN_KEYS) {
      const td = document.createElement("td");
      const input = document.createElement("input");
      input.type = "text";
      input.dataset.key = k;
      input.value = r[k] || "";
      input.setAttribute("aria-label", k);
      td.append(input);
      tr.append(td);
    }
    const td = document.createElement("td");
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn ghost del";
    del.textContent = "삭제";
    del.addEventListener("click", () => {
      tr.remove();
      if (!el.planBody.children.length) el.planBody.append(planRow(planRows(null)[0]));
      notesChanged();
    });
    td.append(del);
    tr.append(td);
    return tr;
  }
  function fillNotes(notes) {
    el.issues.value = notes?.issues || "";
    el.planBody.replaceChildren(...planRows(notes).map(planRow));
  }
  function currentNotes() {
    const rows = [...el.planBody.querySelectorAll("tr")].map((tr) =>
      Object.fromEntries([...tr.querySelectorAll("input")].map((i) => [i.dataset.key, i.value])));
    return notesFromForm(el.issues.value, rows);
  }
  function notesChanged() {
    clearTimeout(S.notesTimer);
    if (S.saveStatus !== "saved") return;          // nothing stored to update (or no permission)
    el.notesStatus.textContent = "작성 중…";
    const runId = S.runId;
    S.notesTimer = setTimeout(async () => {
      if (runId !== S.runId) return;
      const st = await saveNotes(S.store, runId, currentNotes());
      if (runId === S.runId) el.notesStatus.textContent = st === "saved" ? "작성란 저장됨" : saveStatusText(st);
    }, NOTES_DEBOUNCE_MS);
  }
  el.issues.addEventListener("input", notesChanged);
  el.planBody.addEventListener("input", notesChanged);
  el.planAdd.addEventListener("click", () => {
    el.planBody.append(planRow(planRows(null)[0]));
    notesChanged();
  });
  fillNotes(null);

  // ---------------- downloads
  function renderDownloads() {
    if (S.downloads === undefined) return;
    const can = !!S.downloads;
    el.downButtons.hidden = !can;
    el.downNote.textContent = can
      ? "보고서에는 작성란의 이슈·개선 계획이 함께 들어갑니다."
      : "이 화면에서는 파일 내려받기를 사용할 수 없습니다 (claude.ai에서 열면 내려받을 수 있습니다).";
    el.sampleSave.hidden = !can || !el.sampleSave.dataset.ready;
  }
  el.downButtons.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-fmt]");
    if (!btn || !S.run || !S.downloads) return;
    const fmt = btn.dataset.fmt;
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "만드는 중…";
    try {
      const bytes = await S.engine.report(fmt, currentNotes(), { inputHash: S.run.inputHash, baseMonth: S.run.baseMonth });
      await S.downloads.save({ filename: reportFilename(S.run.docNo, fmt), data: bytes });
    } catch (err) {
      if (err?.code !== "declined") showMessage(`파일을 만들거나 내려받지 못했습니다: ${err?.message || err}`);
    } finally {
      btn.textContent = label;
      btn.disabled = false;
      updateControls();
    }
  });

  updateControls();
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
}
