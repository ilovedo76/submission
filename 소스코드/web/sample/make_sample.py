"""Deterministic virtual sample workbook ("가상 데이터") for the hands-on MVP page.

make_sample() -> xlsx bytes with the company file's four-sheet layout (Graph, RawData, 생산량, 판매량),
virtual models 모델A..모델D, 24 months and planted anomalies (1MIS spike over the control limit in the
last 2 production months, infant-mortality shape, one model's level rise, a defect-type surge), so the
engine's verdict is "개선 필요" with >= 4 alerts. Fixed seed, fixed workbook properties and zip entry
timestamps: the bytes are identical on every run.

Built on the desktop at build time from qreport/tests/synth.py (that generator never ships).
"""
from __future__ import annotations

import datetime as dt
import io
import re
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FILE_NAME = "가상_샘플_데이터.xlsx"
MARKER = "가상 데이터"
SEED = 7
ANOMALIES = frozenset({"1mis_spike", "infant", "level_rise", "type_surge"})
VIRTUAL_MODELS = ["모델A", "모델B", "모델C", "모델D"]
DATA_SHEETS = ("RawData", "생산량", "판매량")
FIXED_TIME = dt.datetime(2026, 1, 1, 0, 0, 0)


def _synth():
    for p in (REPO / "qreport" / "src", REPO / "qreport"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    from tests import synth
    return synth


def _marker_cell(ws, wide: bool) -> None:
    """'가상 데이터' where sheet detection ignores it: right of the RawData header row; for the wide
    생산량/판매량 sheets two rows below the Total row (a header-row cell would break detection)."""
    if wide:
        ws.cell(row=ws.max_row + 2, column=1, value=MARKER)
    else:
        ws.cell(row=1, column=ws.max_column + 2, value=MARKER)


def _normalize_zip(data: bytes) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            zi = zipfile.ZipInfo(info.filename, date_time=FIXED_TIME.timetuple()[:6])
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o600 << 16
            body = src.read(info.filename)
            if info.filename == "docProps/core.xml":   # openpyxl stamps "modified" with the save time
                body = re.sub(rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)",
                              rb"\g<1>" + FIXED_TIME.strftime("%Y-%m-%dT%H:%M:%SZ").encode() + rb"\g<2>", body)
            dst.writestr(zi, body)
    return out.getvalue()


def make_sample() -> bytes:
    import openpyxl

    synth = _synth()
    raw = synth.make_workbook(months=24, start="2024-01", seed=SEED, anomalies=ANOMALIES)
    wb = openpyxl.load_workbook(io.BytesIO(raw))
    rename = dict(zip(synth.MODELS, VIRTUAL_MODELS))
    for name in DATA_SHEETS:
        ws = wb[name]
        for (cell,) in ws.iter_rows(min_row=2, max_col=1):
            cell.value = rename.get(cell.value, cell.value)
        _marker_cell(ws, wide=name != "RawData")
    g = wb["Graph"]
    g["B1"] = f"{MARKER} — 체험용 샘플 (실제 기업 데이터가 아닙니다)"
    p = wb.properties
    p.title = f"{MARKER} 샘플"
    p.subject = MARKER
    p.description = f"{MARKER}: 체험용 가상 품질 데이터"
    p.creator = p.lastModifiedBy = "qreport-web"
    p.created = p.modified = FIXED_TIME
    buf = io.BytesIO()
    wb.save(buf)
    return _normalize_zip(buf.getvalue())


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name(FILE_NAME)
    out.write_bytes(make_sample())
    print(out)
