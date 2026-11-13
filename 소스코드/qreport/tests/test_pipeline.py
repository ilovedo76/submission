import hashlib
import time

import pandas as pd

from qreport.ingest.sheets import DEFAULT_DICTIONARY, RawInput
from qreport.pipeline import AnalysisResult, analyze
from qreport.periods import recent_12
from qreport.render.charts import build_charts
from qreport.ruleset import DEFAULT_RULES
from tests.synth import make_workbook


def test_headline(company_result):
    h = company_result.headline
    assert round(h.level * 100, 3) == 0.191
    assert round(h.change_pct) == 57
    assert 0 <= h.pvalue < 0.05
    d = company_result.dataset.analyzable()
    d = d[d["occur_month"].map(recent_12(company_result.base).contains)]
    counts = d.groupby("defect_type").size()
    assert h.top_type == counts.idxmax()
    assert h.top_type_share == counts.max() / counts.sum()
    assert h.unstable_months == 2
    assert h.forecast is not None and h.cost is None


def test_base_defaults_to_last_occur_month(company_result):
    assert company_result.base == pd.Period("2025-12", "M")
    assert company_result.base == company_result.dataset.last_occur_month


def test_input_hash_name_order():
    data = make_workbook(seed=0)   # two non-overlapping parts of one workbook

    def keep(names):
        def f(wb):
            for n in list(wb.sheetnames):
                if n not in names:
                    del wb[n]
        return f

    a, b = _edit(data, keep({"RawData"})), _edit(data, keep({"생산량", "판매량"}))
    r1 = analyze([("b.xlsx", b), ("a.xlsx", a)], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    assert isinstance(r1, AnalysisResult)
    assert r1.input_hash == hashlib.sha256(a + b).hexdigest()


def test_cost_when_as_cost_given():
    r = analyze([("s.xlsx", make_workbook())], None, DEFAULT_RULES, DEFAULT_DICTIONARY,
                as_cost=100000)
    lo, hi = r.headline.forecast
    assert r.headline.cost == (lo * 1e5, hi * 1e5)


def test_needs_mapping_returns_rawinput():
    import io

    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(make_workbook()))
    wb["RawData"]["D1"] = "현상"
    buf = io.BytesIO()
    wb.save(buf)
    r = analyze([("f.xlsx", buf.getvalue())], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    assert isinstance(r, RawInput) and r.needs_mapping
    ok = analyze([("f.xlsx", buf.getvalue())], None, DEFAULT_RULES, DEFAULT_DICTIONARY,
                 overrides={"columns": {("f.xlsx", "RawData"): {"현상": "defect_type"}}})
    assert isinstance(ok, AnalysisResult)


def test_short_history_skips_yoy():
    r = analyze([("s.xlsx", make_workbook(months=14))], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    assert isinstance(r, AnalysisResult)
    assert not {"R4", "R6", "R7"} & {a.rule for a in r.alerts}
    assert r.headline.change_pct is None and r.headline.pvalue is None


def test_explicit_base():
    r = analyze([("s.xlsx", make_workbook())], pd.Period("2025-06", "M"), DEFAULT_RULES,
                DEFAULT_DICTIONARY)
    assert r.base == pd.Period("2025-06", "M")


def test_runtime_under_60s(company_bytes):
    t = time.perf_counter()
    analyze([("company.xlsx", company_bytes)], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    assert time.perf_counter() - t < 30


def _edit(data: bytes, fn) -> bytes:
    import io

    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data))
    fn(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_unknown_model_in_rawdata_excluded(company_bytes):
    def typo(wb):
        wb["RawData"].append(["4door", 2512, 2512, "소음", 1])

    r = analyze([("c.xlsx", _edit(company_bytes, typo))], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    assert isinstance(r, AnalysisResult)
    assert "4door" not in r.dataset.models
    row = r.dataset.defects[r.dataset.defects["model"] == "4door"].iloc[0]
    assert row.status == "제외" and "분모(생산량) 없음" in row.reason
    assert r.verdict


def _add_model(wb, name, qty, cols=None):
    for sheet in ("생산량", "판매량"):
        ws = wb[sheet]
        ws.insert_rows(ws.max_row)  # just above the Total row
        r = ws.max_row - 1
        ws.cell(row=r, column=1, value=name)
        for c in (cols or range(2, ws.max_column + 1)):
            ws.cell(row=r, column=c, value=qty)


def test_model_without_claims_analyzes_and_renders(company_bytes):
    from qreport.notes import Notes
    from qreport.render.bundle import render_chart_pngs
    from qreport.render.docx_report import render_docx
    from qreport.render.pdf_report import render_pdf

    data = _edit(company_bytes, lambda wb: _add_model(wb, "신모델", 1000))
    r = analyze([("c.xlsx", data)], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    assert isinstance(r, AnalysisResult)
    assert "신모델" in r.dataset.models
    assert r.metrics.market_recent["신모델"] == 0
    assert len(build_charts(r, "신모델")) >= 1
    pngs = render_chart_pngs(r, dpi=40)
    assert render_docx(r, Notes(), "QR-N", pngs=pngs)[:2] == b"PK"
    assert render_pdf(r, Notes(), "QR-N", pngs=pngs)[:4] == b"%PDF"


def test_model_produced_only_after_base(company_bytes):
    # production only in the last month, analysed at an earlier base -> empty p-chart
    def f(wb):
        _add_model(wb, "신모델", 1000, cols=[wb["생산량"].max_column])

    r = analyze([("c.xlsx", _edit(company_bytes, f))], pd.Period("2025-06", "M"), DEFAULT_RULES,
                DEFAULT_DICTIONARY)
    assert isinstance(r, AnalysisResult)
    pc = r.metrics.pchart_by_model["신모델"]
    assert pc.df.empty and pc.test1 == [] and pc.run9 == []
    assert len(build_charts(r, "신모델")) >= 1
    from qreport.notes import Notes
    from qreport.render.bundle import render_chart_pngs
    from qreport.render.pdf_report import render_pdf

    assert render_pdf(r, Notes(), "QR-A", pngs=render_chart_pngs(r, dpi=40))[:4] == b"%PDF"


def test_missing_sales_sheet_clear_error():
    import pytest

    from qreport.pipeline import InputError

    def drop(wb):
        del wb["판매량"]

    with pytest.raises(InputError) as e:
        analyze([("ns.xlsx", _edit(make_workbook(seed=7), drop))], None, DEFAULT_RULES,
                DEFAULT_DICTIONARY)
    msg = str(e.value)
    assert "판매량" in msg and "찾지 못했습니다" in msg
    assert "ns.xlsx / Graph" in msg and "표 형식 아님" in msg


def test_trailing_total_column_analyzes():
    def f(wb):
        for n in ("생산량", "판매량"):
            ws = wb[n]
            ws.cell(row=1, column=ws.max_column + 1, value="합계")

    r = analyze([("tc.xlsx", _edit(make_workbook(seed=7), f))], None, DEFAULT_RULES,
                DEFAULT_DICTIONARY)
    assert isinstance(r, AnalysisResult)


def test_identical_files_rejected():
    import pytest

    from qreport.pipeline import InputError

    data = make_workbook(seed=3)
    with pytest.raises(InputError) as e:
        analyze([("a.xlsx", data), ("b.xlsx", data)], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    assert "a.xlsx" in str(e.value) and "b.xlsx" in str(e.value) and "같은 파일" in str(e.value)


def test_overlapping_production_sheets_rejected():
    import pytest

    from qreport.pipeline import InputError

    data = make_workbook(seed=3)

    def only(keep):
        def f(wb):
            for n in list(wb.sheetnames):
                if n not in keep:
                    del wb[n]
        return f

    files = [("all.xlsx", data), ("prod2.xlsx", _edit(data, only({"생산량"})))]
    with pytest.raises(InputError) as e:
        analyze(files, None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    msg = str(e.value)
    assert "all.xlsx / 생산량" in msg and "prod2.xlsx / 생산량" in msg and "겹" in msg
    # non-overlapping split files are still fine
    ok = [("raw.xlsx", _edit(data, only({"RawData"}))), ("p.xlsx", _edit(data, only({"생산량"}))),
          ("s.xlsx", _edit(data, only({"판매량"})))]
    assert isinstance(analyze(ok, None, DEFAULT_RULES, DEFAULT_DICTIONARY), AnalysisResult)
