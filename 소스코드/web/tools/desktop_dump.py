"""Desktop side of the parity test: print analyze_files() as canonical JSON.

  python desktop_dump.py --synth OUT.xlsx   # write the synthetic workbook to OUT.xlsx, analyse it
  python desktop_dump.py BOOK.xlsx          # analyse this workbook
Run with qreport's venv. The file name passed to analyze_files is always "parity.xlsx".
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO / "web" / "py", REPO / "qreport" / "src", REPO / "qreport"):
    sys.path.insert(0, str(p))

from web_entry import analyze_files  # noqa: E402

if sys.argv[1] == "--synth":
    from tests.synth import make_workbook
    data = make_workbook(months=24, seed=0, anomalies={"1mis_spike", "type_surge"})
    Path(sys.argv[2]).write_bytes(data)
else:
    data = Path(sys.argv[1]).read_bytes()
res = analyze_files([("parity.xlsx", data)], None, None, "user")
sys.stdout.buffer.write(json.dumps(res, ensure_ascii=False, sort_keys=True).encode("utf-8"))
