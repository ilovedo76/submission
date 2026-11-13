// Module worker: boots the self-hosted Pyodide from the published files (manifest.json) and runs
// web_entry for the page. Protocol (main -> worker): {id, type, ...}; replies {id, ok, value | error}.
//   init    {base}                                    -> "ready"; progress {type:"progress", step, total}
//   analyze {files:[{name, bytes}], base, asCost, kind, overrides} -> JSON string {ok, run, docs} | {ok:false,...}
//   report  {fmt, notes, state}                       -> Uint8Array (transferred)
//   charts  {model, state}                            -> JSON string {key: base64 png}
import { installFetchShim } from "./binfetch.js";

const HOME = "/home/pyodide";
export const STEPS = 7;

let py = null;
let bridge = null;

const BRIDGE = `
import json, os, web_entry

def _dumps(o):
    return json.dumps(o, ensure_ascii=False, allow_nan=False)

def bridge_analyze(req_json):
    req = json.loads(req_json)
    files = []
    for f in req["files"]:
        with open(f["path"], "rb") as fh:
            files.append((f["name"], fh.read()))
        os.remove(f["path"])
    r = web_entry.analyze_files(files, req.get("base"), req.get("asCost"), req["kind"], req.get("overrides"))
    if r.get("ok"):
        return _dumps({"ok": True, "run": r["run"], "docs": web_entry.split_for_db(r)})
    return _dumps(r)

def bridge_report(req_json, out_path):
    req = json.loads(req_json)
    data = web_entry.render_report(req["state"], req.get("notes") or {}, req["fmt"])
    with open(out_path, "wb") as fh:
        fh.write(data)

def bridge_charts(req_json):
    req = json.loads(req_json)
    return _dumps(web_entry.chart_pngs(req.get("model"), req.get("state")))
`;

function progress(step) {
  self.postMessage({ type: "progress", step, total: STEPS });
}

async function fetchBytes(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return new Uint8Array(await res.arrayBuffer());
}

function writeFile(path, bytes) {
  py.FS.mkdirTree(path.slice(0, path.lastIndexOf("/")));
  py.FS.writeFile(path, bytes);
}

/** "py/qreport/x.py" -> "/home/pyodide/qreport/x.py" */
export function mountPath(p) {
  return `${HOME}/${p.replace(/^\/+/, "").replace(/^py\//, "")}`;
}

async function inBatches(items, n, fn) {
  for (let i = 0; i < items.length; i += n) await Promise.all(items.slice(i, i + n).map(fn));
}

async function init(base, indexOverride) {
  const at = (p) => new URL(p, base).href;
  progress(1);
  const res = await fetch(at("manifest.json"));
  if (!res.ok) throw new Error(`manifest.json: HTTP ${res.status}`);
  const m = await res.json();
  // archives are published as base64 text parts (manifest.binaries): decode them on fetch, for
  // Pyodide's python_stdlib.zip / wheels as well as the wheels and report.docx fetched below
  installFetchShim(m, base);
  const indexURL = indexOverride || at(m.indexURL || "pyodide/");   // override: Node test harness only

  progress(2);
  const { loadPyodide } = await import(new URL("pyodide.mjs", at(m.indexURL || "pyodide/")).href);
  py = await loadPyodide({ indexURL, stdout: () => {}, stderr: (s) => console.warn(s) });

  progress(3);
  await py.loadPackage(m.packages || [], { messageCallback: () => {}, errorCallback: (s) => console.warn(s) });

  progress(4);   // vendored pure-Python wheels: unpack into site-packages (no network install)
  const site = py.runPython("import site; site.getsitepackages()[0]");
  for (const w of m.wheels || []) {
    py.unpackArchive(await fetchBytes(at(w)), "zip", { extractDir: site });
  }

  progress(5);
  await inBatches(m.python || [], 16, async (f) => writeFile(mountPath(f.path), await fetchBytes(at(f.path))));

  progress(6);
  for (const f of m.fonts || []) writeFile(`${HOME}/fonts/${f.split("/").pop()}`, await fetchBytes(at(f)));
  const setupMod = (m.setup || "py/web_fonts.py").split("/").pop().replace(/\.py$/, "");
  py.runPython(`
import sys, importlib
if ${JSON.stringify(HOME)} not in sys.path:
    sys.path.insert(0, ${JSON.stringify(HOME)})
importlib.invalidate_caches()
import ${setupMod}
${setupMod}.setup(${JSON.stringify(HOME + "/fonts")})
`);

  progress(7);
  py.runPython(BRIDGE);
  bridge = {
    analyze: py.globals.get("bridge_analyze"),
    report: py.globals.get("bridge_report"),
    charts: py.globals.get("bridge_charts"),
  };
  return "ready";
}

let seq = 0;
async function handle(msg) {
  switch (msg.type) {
    case "init":
      return { value: await init(msg.base, msg.indexURL) };
    case "analyze": {
      const files = msg.files.map((f, i) => {
        const path = `/tmp/in/${++seq}_${i}`;
        writeFile(path, f.bytes);
        return { name: f.name, path };
      });
      const req = { files, base: msg.base ?? null, asCost: msg.asCost ?? null, kind: msg.kind,
        overrides: msg.overrides ?? null };
      return { value: bridge.analyze(JSON.stringify(req)) };
    }
    case "report": {
      const out = `/tmp/out_${++seq}.bin`;
      bridge.report(JSON.stringify({ fmt: msg.fmt, notes: msg.notes, state: msg.state }), out);
      const bytes = py.FS.readFile(out);
      py.FS.unlink(out);
      return { value: bytes, transfer: [bytes.buffer] };
    }
    case "charts":
      return { value: bridge.charts(JSON.stringify({ model: msg.model ?? null, state: msg.state ?? null })) };
    default:
      throw new Error(`unknown message ${msg.type}`);
  }
}

// Messages run one at a time (Python is single-threaded; keeps init before the rest).
let queue = Promise.resolve();
self.onmessage = (e) => {
  const msg = e.data;
  queue = queue.then(async () => {
    try {
      const { value, transfer } = await handle(msg);
      self.postMessage({ id: msg.id, ok: true, value }, transfer || []);
    } catch (err) {
      self.postMessage({ id: msg.id, ok: false, error: String(err && err.message ? err.message : err) });
    }
  });
};
