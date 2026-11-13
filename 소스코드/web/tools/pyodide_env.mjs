// Node harness: boot the pinned Pyodide, install the qreport engine's dependencies and
// mount qreport/src/qreport (engine + Word template, no Streamlit UI / sqlite store)
// at /home/pyodide/qreport. Task 5/6 reuse PYODIDE_VERSION / PY_PACKAGES / MICROPIP_PACKAGES
// so the browser page loads exactly the same set.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Newest stable Pyodide (Python 3.14.2, pandas 3.0.2, numpy 2.4.6, scipy 1.18.0, matplotlib 3.10.8).
export const PYODIDE_VERSION = "314.0.7";
export const PYODIDE_CDN = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

// Packages bundled with Pyodide (loadPackage resolves their bundled dependencies:
// python-dateutil, pytz, tzdata, six, contourpy, kiwisolver, fonttools, ...).
export const PY_PACKAGES = [
  "micropip", "numpy", "pandas", "scipy", "matplotlib",
  "lxml", "jinja2", "markupsafe", "pillow", "typing-extensions",
];

// Pure-Python wheels from PyPI, exact versions (= qreport/requirements-lock.txt),
// installed with deps=False so the set is fully pinned; their dependencies are
// either listed here or in PY_PACKAGES.
export const MICROPIP_PACKAGES = [
  "et-xmlfile==2.0.0",          // openpyxl
  "openpyxl==3.1.5",
  "python-docx==1.2.0",         // lxml, typing-extensions
  "docxtpl==0.20.2",            // jinja2, lxml, python-docx
  "charset-normalizer==3.5.2",  // reportlab
  "reportlab==5.1.0",           // pillow, charset-normalizer
];

export const PY_HOME = "/home/pyodide";
const here = path.dirname(fileURLToPath(import.meta.url));
export const DEFAULT_QREPORT_SRC = path.resolve(here, "..", "..", "qreport", "src", "qreport");

// Top-level subpackages never shipped to the browser (Streamlit UI, sqlite store).
const EXCLUDE_TOP = new Set(["app", "store"]);
const KEEP_EXT = new Set([".py", ".docx"]);

/** Files of the qreport package to mount: [{abs, rel}] with posix rel paths. */
export function collectQreportFiles(qreportSrc = DEFAULT_QREPORT_SRC) {
  const out = [];
  const walk = (dir, relDir) => {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      if (ent.name === "__pycache__") continue;
      const rel = relDir ? `${relDir}/${ent.name}` : ent.name;
      const abs = path.join(dir, ent.name);
      if (ent.isDirectory()) {
        if (!relDir && EXCLUDE_TOP.has(ent.name)) continue;
        walk(abs, rel);
      } else if (KEEP_EXT.has(path.extname(ent.name))) {
        out.push({ abs, rel });
      }
    }
  };
  walk(qreportSrc, "");
  return out.sort((a, b) => a.rel.localeCompare(b.rel));
}

function writeTree(pyodide, files, destRoot) {
  for (const { abs, rel } of files) {
    const dest = `${destRoot}/${rel}`;
    pyodide.FS.mkdirTree(dest.slice(0, dest.lastIndexOf("/")));
    pyodide.FS.writeFile(dest, fs.readFileSync(abs));
  }
}

/**
 * Boot Pyodide with qreport mounted.
 * @param {object} opts
 * @param {string} [opts.qreportSrc]  path to qreport/src/qreport on disk
 * @param {string[]} [opts.extraPy]   extra .py files or directories (copied to /home/pyodide/<basename>)
 * @param {(msg: string) => void} [opts.log]
 * @returns {Promise<any>} the pyodide instance; `import qreport` works
 */
export async function bootPyodide({ qreportSrc = DEFAULT_QREPORT_SRC, extraPy = [], log = () => {} } = {}) {
  const { loadPyodide, version } = await import("pyodide");
  if (version !== PYODIDE_VERSION) {
    throw new Error(`installed pyodide ${version} != pinned ${PYODIDE_VERSION}; run npm ci in web/`);
  }
  const pyodide = await loadPyodide({ stdout: () => {}, stderr: (s) => log(s) });
  await pyodide.loadPackage(PY_PACKAGES, { messageCallback: () => {}, errorCallback: log });
  const micropip = pyodide.pyimport("micropip");
  await micropip.install.callKwargs(MICROPIP_PACKAGES, { deps: false });
  micropip.destroy();

  writeTree(pyodide, collectQreportFiles(qreportSrc), `${PY_HOME}/qreport`);
  for (const p of extraPy) {
    const st = fs.statSync(p);
    const name = path.basename(p);
    if (st.isDirectory()) {
      const files = [];
      const walk = (dir, relDir) => {
        for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
          if (ent.name === "__pycache__") continue;
          const abs = path.join(dir, ent.name);
          const rel = relDir ? `${relDir}/${ent.name}` : ent.name;
          if (ent.isDirectory()) walk(abs, rel);
          else files.push({ abs, rel });
        }
      };
      walk(p, "");
      writeTree(pyodide, files, `${PY_HOME}/${name}`);
    } else {
      writeTree(pyodide, [{ abs: p, rel: name }], PY_HOME);
    }
  }
  pyodide.runPython(`
import sys, importlib
if ${JSON.stringify(PY_HOME)} not in sys.path:
    sys.path.insert(0, ${JSON.stringify(PY_HOME)})
importlib.invalidate_caches()
`);
  return pyodide;
}
