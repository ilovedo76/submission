import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  PYODIDE_VERSION, PY_PACKAGES, MICROPIP_PACKAGES, collectQreportFiles, DEFAULT_QREPORT_SRC,
} from "./pyodide_env.mjs";

test("constants are pinned", () => {
  assert.match(PYODIDE_VERSION, /^\d+\.\d+\.\d+$/);
  for (const p of ["numpy", "pandas", "scipy", "matplotlib", "lxml", "jinja2", "pillow", "micropip"]) {
    assert.ok(PY_PACKAGES.includes(p), p);
  }
  for (const spec of MICROPIP_PACKAGES) assert.match(spec, /^[A-Za-z0-9_.-]+==\d[\w.]*$/, spec);
  const names = MICROPIP_PACKAGES.map((s) => s.split("==")[0]);
  for (const n of ["openpyxl", "python-docx", "docxtpl", "reportlab"]) assert.ok(names.includes(n), n);
});

test("package.json pins the same pyodide version", () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const pkg = JSON.parse(fs.readFileSync(path.join(here, "..", "package.json"), "utf8"));
  assert.equal(pkg.devDependencies.pyodide, PYODIDE_VERSION);
});

test("collectQreportFiles: engine + template only, no UI/store, no caches", () => {
  const files = collectQreportFiles(DEFAULT_QREPORT_SRC);
  const rels = files.map((f) => f.rel);
  assert.ok(rels.includes("__init__.py"));
  assert.ok(rels.includes("pipeline.py"));
  assert.ok(rels.includes("render/docx_report.py"));
  assert.ok(rels.includes("templates/report.docx"));
  assert.ok(!rels.some((r) => r.startsWith("app/") || r.startsWith("store/")), "no app/store");
  assert.ok(!rels.some((r) => r.includes("__pycache__") || r.endsWith(".pyc")), "no caches");
  assert.ok(rels.every((r) => !r.includes("\\")), "posix separators");
});
