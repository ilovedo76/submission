// Main-thread side of the analysis engine (Pyodide in a module Web Worker, engine.worker.js).
// loadEngine(onStatus) -> Promise<{analyze, report, charts}>; onStatus receives
//   {state: "loading", step, total, text} | {state: "ready"} | {state: "failed", message}.

export const LOAD_FAILED = "분석 엔진을 불러오지 못했습니다. 네트워크 상태나 기기 메모리를 확인한 뒤 다시 시도해 주세요.";
export const ENGINE_STOPPED = "분석 엔진이 중단되었습니다(메모리 부족일 수 있습니다). 페이지를 다시 불러와 주세요.";

export function progressText(step, total) {
  return `분석 엔진 준비 중… (${step}/${total} 단계)`;
}

/** Python traceback text -> its last meaningful line (the readable message). */
export function shortError(text) {
  const lines = String(text ?? "").split("\n").map((s) => s.trim()).filter(Boolean);
  const last = lines[lines.length - 1] || "알 수 없는 오류";
  return last.replace(/^[A-Za-z_.]*(Error|Exception):\s*/, "");
}

export function loadEngine(onStatus = () => {}, { base = new URL(".", location.href).href } = {}) {
  let worker;
  try {
    worker = new Worker(new URL("./engine.worker.js", import.meta.url), { type: "module" });
  } catch (err) {
    console.error(err);
    onStatus({ state: "failed", message: LOAD_FAILED });
    return Promise.reject(err);
  }
  const pending = new Map();
  let nextId = 0;
  let dead = null;

  const call = (type, payload = {}, transfer = []) => {
    if (dead) return Promise.reject(new Error(dead));
    const id = ++nextId;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      worker.postMessage({ id, type, ...payload }, transfer);
    });
  };

  const die = (message) => {
    dead = message;
    for (const p of pending.values()) p.reject(new Error(message));
    pending.clear();
    try { worker.terminate(); } catch { /* already gone */ }
  };

  worker.onmessage = (e) => {
    const m = e.data;
    if (m.type === "progress") {
      onStatus({ state: "loading", step: m.step, total: m.total, text: progressText(m.step, m.total) });
      return;
    }
    const p = pending.get(m.id);
    if (!p) return;
    pending.delete(m.id);
    if (m.ok) p.resolve(m.value);
    else p.reject(new Error(shortError(m.error)));
  };
  let ready = false;
  worker.onerror = (e) => {
    console.error("engine worker error", e.message || e);
    e.preventDefault?.();
    die(ready ? ENGINE_STOPPED : LOAD_FAILED);
    onStatus({ state: "failed", message: ready ? ENGINE_STOPPED : LOAD_FAILED });
  };

  const api = {
    /** files: [{name, bytes: Uint8Array}] (a copy is transferred; the caller keeps its bytes). */
    async analyze(files, base, asCost, kind, overrides = null) {
      const list = files.map((f) => ({ name: f.name, bytes: new Uint8Array(f.bytes) }));
      const out = await call("analyze", { files: list, base, asCost, kind, overrides },
        list.map((f) => f.bytes.buffer));
      return JSON.parse(out);
    },
    /** fmt docx | pdf | zip -> Uint8Array */
    report(fmt, notes, state) {
      return call("report", { fmt, notes, state });
    },
    /** model null = 전체 -> {key: base64 png} */
    async charts(model, state) {
      return JSON.parse(await call("charts", { model, state }));
    },
  };

  onStatus({ state: "loading", step: 0, total: 7, text: progressText(0, 7) });
  return call("init", { base }).then(
    () => { ready = true; onStatus({ state: "ready" }); return api; },
    (err) => {
      console.error("engine init failed", err);
      die(LOAD_FAILED);
      onStatus({ state: "failed", message: LOAD_FAILED, detail: err.message });
      throw err;
    },
  );
}
