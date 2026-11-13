"""Write a fake-db fixture for the dashboard preview: {docPath: doc} built by split_for_db.

  qreport/.venv/Scripts/python web/tools/make_dashboard_fixture.py web/tools/dashboard_fixture.json
Two runs from the SYNTH workbook: kind "sample" (newer) and kind "user" (older, other base month),
plus MVP-style fields (uploader, createdAt, updatedAt, notes). Never uses the company workbook.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO / "web" / "py", REPO / "qreport" / "src", REPO / "qreport"):
    sys.path.insert(0, str(p))

from tests.synth import make_workbook  # noqa: E402
from web_entry import analyze_files, split_for_db  # noqa: E402

data = make_workbook(months=24, seed=0, anomalies={"1mis_spike", "type_surge"})
docs = {}
for kind, base, cost, uploader, ts in (("user", "2025-11", 45000, "u_alice", "2026-11-10T09:12:00Z"),
                                       ("sample", None, None, "u_bob", "2026-11-12T15:40:00Z")):
    res = analyze_files([("synth.xlsx" if kind == "user" else "sample.xlsx", data)], base, cost, kind)
    assert res["ok"], res
    for path, doc in split_for_db(res, limit=8 * 1024):   # small limit -> exercise __k chunks
        if "/" not in path[5:]:
            doc = {**doc, "uploader": uploader, "createdAt": ts, "updatedAt": ts,
                   "notes": {"issues": "", "plan": []}}
        docs[path] = doc
Path(sys.argv[1]).write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")
print(len(docs), "docs")
