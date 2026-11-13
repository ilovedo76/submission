// Offline boot of the PUBLISHED Pyodide: fetch is a fake host serving only web/publish/ files (any
// other URL throws); archives exist there only as base64 parts and come through binfetch.js's shim
// (python_stdlib.zip, package wheels, micropip wheels, report.docx, sample). Install exactly
// manifest.json's packages + wheels, mount
// the manifest's python/fonts, analyze the sample and render the PDF. Proves the vendored set is
// complete and the OFL font gives Korean glyphs (no missing-glyph warnings; pypdf finds Korean text).
//
//   qreport\.venv\Scripts\python web\build.py   (first)
//   node --test web/tools/test_publish_offline.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import { installFetchShim, fetchPublished } from "../pages/js/binfetch.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const PUBLISH = path.resolve(here, "..", "publish");
const VENV_PY = path.resolve(here, "..", "..", "qreport", ".venv", "Scripts", "python.exe");
const HOME = "/home/pyodide";
const BASE = "http://publish.local/";
const haveBuild = fs.existsSync(path.join(PUBLISH, "manifest.json"));

test("published pyodide boots offline and analyzes the sample", { skip: !haveBuild && "run web/build.py first", timeout: 600_000 }, async () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(PUBLISH, "manifest.json"), "utf8"));
  const listed = new Set(Object.keys(JSON.parse(fs.readFileSync(path.join(PUBLISH, "files.json"), "utf8"))));
  const blocked = [];
  const unlisted = [];
  const served = [];
  const realFetch = globalThis.fetch;
  // the "artifact host": serves only files that exist in publish/ (archives exist only as .b64 parts)
  globalThis.fetch = async (url) => {
    const u = String(url instanceof Request ? url.url : url);
    if (!u.startsWith(BASE)) { blocked.push(u); throw new Error(`network blocked in test: ${u}`); }
    const rel = decodeURIComponent(new URL(u).pathname.slice(1));
    const file = path.join(PUBLISH, rel);
    if (!fs.existsSync(file)) return new Response("not found", { status: 404 });
    if (!listed.has(rel)) unlisted.push(rel);
    served.push(rel);
    return new Response(fs.readFileSync(file), { status: 200 });
  };
  const cacheDir = fs.mkdtempSync(path.join(os.tmpdir(), "qpkg-")) + path.sep;
  try {
    const t0 = performance.now();
    installFetchShim(manifest, BASE);                 // same module the worker bundles
    const at = (p) => new URL(p, BASE).href;
    const bytes = async (p) => new Uint8Array(await (await fetch(at(p))).arrayBuffer());
    const indexURL = path.join(PUBLISH, manifest.indexURL) + path.sep;   // .mjs / lock / wasm: on disk
    const { loadPyodide, version } = await import(pathToFileURL(path.join(indexURL, "pyodide.mjs")).href);
    assert.equal(version, manifest.pyodideVersion);
    const py = await loadPyodide({
      indexURL, stdout: () => {}, stderr: () => {},
      stdLibURL: at(manifest.indexURL + "python_stdlib.zip"),   // through the shim (base64 parts)
      packageBaseUrl: at(manifest.indexURL), packageCacheDir: cacheDir,   // wheels: through the shim
    });
    await py.loadPackage(manifest.packages, { messageCallback: () => {} });
    const site = py.runPython("import site; site.getsitepackages()[0]");
    for (const w of manifest.wheels) py.unpackArchive(await bytes(w), "zip", { extractDir: site });
    const mount = async (rel, dest) => {
      py.FS.mkdirTree(dest.slice(0, dest.lastIndexOf("/")));
      py.FS.writeFile(dest, await bytes(rel));
    };
    for (const { path: rel } of manifest.python) await mount(rel, `${HOME}/${rel.replace(/^py\//, "")}`);
    for (const rel of manifest.fonts) await mount(rel, `${HOME}/fonts/${path.posix.basename(rel)}`);
    py.FS.writeFile(`${HOME}/sample.xlsx`, await fetchPublished(manifest.sample, manifest, BASE));
    const bootMs = performance.now() - t0;

    const out = JSON.parse(py.runPython(`
import sys, json, time, warnings, base64
sys.path.insert(0, "${HOME}")
t0 = time.perf_counter()
import web_fonts
family = web_fonts.setup("${HOME}/fonts")
import web_entry as we
res = we.analyze_files([(${JSON.stringify(path.posix.basename(manifest.sample))}, open("${HOME}/sample.xlsx", "rb").read())], None, None, "sample")
t_an = time.perf_counter() - t0
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    pngs = we.chart_pngs()
    pdf = we.render_report({"inputHash": res["run"]["inputHash"], "baseMonth": res["run"]["baseMonth"]}, {}, "pdf")
missing = [str(x.message) for x in w if "missing from" in str(x.message)]
json.dumps({"ok": res["ok"], "verdict": res["run"]["verdict"], "alerts": res["run"]["alertCount"],
            "family": family, "charts": len(pngs), "missing": missing[:5], "analyze_s": t_an,
            "pdf": base64.b64encode(pdf).decode("ascii"),
            "mods": sorted(m for m in sys.modules if m.startswith(("qreport.app", "qreport.store")))}, ensure_ascii=False)
`));
    console.log(`offline boot ${(bootMs / 1000).toFixed(1)}s (pyodide + packages + wheels + mounts), ` +
      `analyze ${out.analyze_s.toFixed(1)}s, font ${out.family}, ${out.charts} charts`);
    assert.deepEqual(blocked, [], "no network fetch attempted");
    assert.deepEqual(unlisted, [], "fetched files missing from files.json");
    for (const rel of Object.keys(manifest.binaries)) {
      assert.ok(!served.includes(rel), `${rel} fetched directly`);
    }
    for (const want of ["pyodide/python_stdlib.zip.b64.0.txt", manifest.binaries[manifest.sample][0],
      manifest.binaries["py/qreport/templates/report.docx"][0],
      ...manifest.binaries[Object.keys(manifest.binaries).find((k) => k.startsWith("pyodide/scipy-"))],
      manifest.binaries[manifest.wheels[0]][0]]) {
      assert.ok(served.includes(want), `${want} not fetched through the shim`);
    }
    assert.ok(out.ok);
    assert.equal(out.verdict, "개선 필요");
    assert.ok(out.alerts >= 4);
    assert.equal(out.family, "NanumGothic");
    assert.deepEqual(out.missing, [], "matplotlib glyphs missing");
    assert.deepEqual(out.mods, []);

    const pdfPath = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "qpdf-")), "sample.pdf");
    fs.writeFileSync(pdfPath, Buffer.from(out.pdf, "base64"));
    if (fs.existsSync(VENV_PY)) {
      const r = spawnSync(VENV_PY, ["-I", "-c",
        "import sys, pypdf; sys.stdout.reconfigure(encoding='utf-8');" +
        "print(''.join(p.extract_text() for p in pypdf.PdfReader(sys.argv[1]).pages))", pdfPath],
        { encoding: "utf8" });
      assert.equal(r.status, 0, r.stderr);
      for (const word of ["가상 데이터", "개선 필요", "모델A", "beta", "p-bar", "Sum "]) {
        assert.ok(r.stdout.includes(word), `PDF lacks ${word}`);
      }
      // glyphs NanumGothic lacks are substituted (web_fonts.PDF_GLYPHS), never left as tofu
      for (const ch of ["β", "Σ", "−", "̄", "�"]) {
        assert.ok(!r.stdout.includes(ch), `PDF text has U+${ch.codePointAt(0).toString(16).toUpperCase()}`);
      }
    }
  } finally {
    globalThis.fetch = realFetch;
    fs.rmSync(cacheDir, { recursive: true, force: true });
  }
});
