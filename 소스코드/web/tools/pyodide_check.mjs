// Feasibility check: qreport analyze + Word/PDF/PNG-zip renderers under Pyodide (Node).
//
//   node web/tools/pyodide_check.mjs                       # workbook generated in Pyodide by tests/synth.py
//   node web/tools/pyodide_check.mjs book.xlsx             # analyse these bytes
//   node web/tools/pyodide_check.mjs book.xlsx --expect expected.json
//        expected.json = {"verdict", "alerts", "rules"?, "input_hash"?, "headline_level"?} from a
//        desktop run on the same bytes; exits 1 on any mismatch.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { bootPyodide, PYODIDE_VERSION, MICROPIP_PACKAGES } from "./pyodide_env.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const SYNTH = path.resolve(here, "..", "..", "qreport", "tests", "synth.py");

const args = process.argv.slice(2);
const ei = args.indexOf("--expect");
const expectPath = ei >= 0 ? args.splice(ei, 2)[1] : null;
const xlsxPath = args[0] ?? null;

const now = () => performance.now();
const secs = (ms) => `${(ms / 1000).toFixed(2)}s`;

let t = now();
const pyodide = await bootPyodide({ extraPy: [SYNTH], log: (m) => process.stderr.write(m + "\n") });
const bootMs = now() - t;

if (xlsxPath) pyodide.FS.writeFile("/home/pyodide/input.xlsx", fs.readFileSync(xlsxPath));

const res = pyodide.runPython(`
import json, sys, time, platform, warnings
warnings.filterwarnings("ignore", message=r"Glyph [0-9]+ .* missing from font")  # expected until Task 5 font
import numpy, pandas, scipy, matplotlib

def _t():
    return time.perf_counter()

out = {"python": platform.python_version(), "pandas": pandas.__version__, "numpy": numpy.__version__,
       "scipy": scipy.__version__, "matplotlib": matplotlib.__version__, "timings": {}}

if ${xlsxPath ? "True" : "False"}:
    data = open("/home/pyodide/input.xlsx", "rb").read()
else:
    from synth import make_workbook
    t0 = _t()
    data = make_workbook(months=24, seed=0, anomalies={"1mis_spike", "type_surge"})
    out["timings"]["make_workbook"] = _t() - t0

t0 = _t()
from qreport.pipeline import analyze
from qreport.ingest.sheets import DEFAULT_DICTIONARY
from qreport.ruleset import DEFAULT_RULES
from qreport.notes import Notes
from qreport.render.bundle import render_chart_pngs, render_png_zip
from qreport.render.docx_report import render_docx
from qreport.render import pdf_report
from qreport.render.pdf_report import render_pdf
out["timings"]["import"] = _t() - t0
out["ui_modules_loaded"] = sorted(m for m in sys.modules
                                  if m.startswith(("qreport.app", "qreport.store", "streamlit", "sqlite3")))

# Malgun Gothic (C:/Windows/Fonts) does not exist here. Harness-only fallback: register
# matplotlib's DejaVuSans under qreport's PDF font names so _register_fonts() is a no-op
# (Korean glyphs missing in the PDF). Task 5 replaces this with the bundled OFL Korean font.
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os
_ttf = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
pdfmetrics.registerFont(TTFont(pdf_report.REG, os.path.join(_ttf, "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont(pdf_report.BOLD, os.path.join(_ttf, "DejaVuSans-Bold.ttf")))
pdfmetrics.registerFontFamily(pdf_report.REG, normal=pdf_report.REG, bold=pdf_report.BOLD,
                              italic=pdf_report.REG, boldItalic=pdf_report.BOLD)

t0 = _t()
r = analyze([("synth.xlsx", data)], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
out["timings"]["analyze"] = _t() - t0
out.update(verdict=r.verdict, alerts=len(r.alerts), rules=sorted(a.rule for a in r.alerts),
           base=str(r.base), input_hash=r.input_hash, headline_level=r.headline.level,
           xlsx_bytes=len(data))

t0 = _t(); pngs = render_chart_pngs(r); out["timings"]["chart_pngs"] = _t() - t0
out["png_count"] = len(pngs)
t0 = _t(); docx = render_docx(r, Notes(), "WEB-CHECK", pngs); out["timings"]["render_docx"] = _t() - t0
t0 = _t(); pdf = render_pdf(r, Notes(), "WEB-CHECK", pngs); out["timings"]["render_pdf"] = _t() - t0
t0 = _t(); zp = render_png_zip(r, pngs); out["timings"]["render_png_zip"] = _t() - t0
out.update(docx_bytes=len(docx), pdf_bytes=len(pdf), zip_bytes=len(zp),
           docx_magic=docx[:2] == b"PK", pdf_magic=pdf[:5] == b"%PDF-", zip_magic=zp[:2] == b"PK")
json.dumps(out, ensure_ascii=False)
`);
const out = JSON.parse(res);

console.log(`pyodide ${PYODIDE_VERSION} | python ${out.python} | pandas ${out.pandas} | numpy ${out.numpy}` +
  ` | scipy ${out.scipy} | matplotlib ${out.matplotlib}`);
console.log(`micropip: ${MICROPIP_PACKAGES.join(" ")}`);
console.log(`input: ${xlsxPath ?? "synth (generated in pyodide)"} (${out.xlsx_bytes} bytes)`);
console.log(`verdict: ${out.verdict} | alerts: ${out.alerts} [${out.rules.join(",")}] | base ${out.base}`);
console.log(`docx: ${out.docx_bytes} bytes | pdf: ${out.pdf_bytes} bytes | png zip: ${out.zip_bytes} bytes` +
  ` (${out.png_count} charts)`);
console.log(`time: boot ${secs(bootMs)} | ` +
  Object.entries(out.timings).map(([k, v]) => `${k} ${v.toFixed(2)}s`).join(" | "));

const problems = [];
if (out.ui_modules_loaded.length) problems.push(`UI/store modules imported: ${out.ui_modules_loaded.join(", ")}`);
if (!out.verdict) problems.push("empty verdict");
for (const k of ["docx", "pdf", "zip"]) {
  if (!(out[`${k}_bytes`] > 0) || !out[`${k}_magic`]) problems.push(`${k} output invalid`);
}
if (expectPath) {
  const exp = JSON.parse(fs.readFileSync(expectPath, "utf8"));
  for (const [k, v] of Object.entries(exp)) {
    const got = out[k];
    const same = typeof v === "number" && !Number.isInteger(v)
      ? Math.abs(got - v) <= 1e-12 * Math.max(1, Math.abs(v))
      : JSON.stringify(got) === JSON.stringify(v);
    if (!same) problems.push(`${k}: desktop ${JSON.stringify(v)} != pyodide ${JSON.stringify(got)}`);
  }
  if (!problems.length) console.log(`matches desktop: ${Object.keys(exp).join(", ")}`);
}
if (problems.length) {
  console.error("FAIL\n- " + problems.join("\n- "));
  process.exit(1);
}
console.log("OK");
