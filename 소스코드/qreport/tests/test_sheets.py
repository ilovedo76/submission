import io

import openpyxl
import pandas as pd

from qreport.ingest.sheets import DEFAULT_DICTIONARY, read_workbooks
from synth import make_workbook


def _wb(data: bytes):
    return openpyxl.load_workbook(io.BytesIO(data))


def _save(wb) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _only(data: bytes, keep: str) -> bytes:
    wb = _wb(data)
    for n in wb.sheetnames:
        if n != keep:
            del wb[n]
    return _save(wb)


def _norm(df, drop):
    drop = [drop] if isinstance(drop, str) else drop
    return df.drop(columns=[c for c in drop if c in df.columns]).reset_index(drop=True)


def test_company_file_roles(company_bytes):
    ri = read_workbooks([("company.xlsx", company_bytes)], DEFAULT_DICTIONARY)
    roles = {r.sheet: r.role for r in ri.reports}
    assert roles == {"Graph": "ignored", "RawData": "defects", "생산량": "production", "판매량": "sales"}
    assert len(ri.defects) == 1000 and ri.production.qty.sum() == 641808
    assert all("차이 0" in r.reason for r in ri.reports if r.role in ("production", "sales"))
    assert ri.needs_mapping == []
    assert list(ri.defects.columns[:5]) == ["model", "prod_month", "occur_month", "defect_type", "use_months"]
    assert ri.defects.src_row.min() == 2 and ri.defects.src_row.max() == 1001
    assert ri.production.month.nunique() == 24 and ri.production.model.nunique() == 4


def test_synth_roles_and_shapes():
    ri = read_workbooks([("s.xlsx", make_workbook())], DEFAULT_DICTIONARY)
    roles = {r.sheet: r.role for r in ri.reports}
    assert roles == {"Graph": "ignored", "RawData": "defects", "생산량": "production", "판매량": "sales"}
    assert ri.needs_mapping == []
    assert ri.production.month.dtype == "period[M]"
    assert ri.defects.prod_month.dtype == "period[M]"
    assert "Total" not in set(ri.production.model)
    assert len(ri.defects) > 100


def test_split_files_same_result():
    data = make_workbook()
    whole = read_workbooks([("all.xlsx", data)], DEFAULT_DICTIONARY)
    parts = read_workbooks(
        [(f"{n}.xlsx", _only(data, n)) for n in ("RawData", "생산량", "판매량")], DEFAULT_DICTIONARY
    )
    for name in ("defects", "production", "sales"):
        pd.testing.assert_frame_equal(
            _norm(getattr(whole, name), "src_file"), _norm(getattr(parts, name), "src_file")
        )


def test_renamed_sheets():
    data = make_workbook()
    base = read_workbooks([("a.xlsx", data)], DEFAULT_DICTIONARY)
    wb = _wb(data)
    wb["RawData"].title = "Raw"
    wb["생산량"].title = "생산 실적"
    ri = read_workbooks([("a.xlsx", _save(wb))], DEFAULT_DICTIONARY)
    roles = {r.sheet: r.role for r in ri.reports}
    assert roles == {"Graph": "ignored", "Raw": "defects", "생산 실적": "production", "판매량": "sales"}
    for name in ("defects", "production", "sales"):
        pd.testing.assert_frame_equal(
            _norm(getattr(base, name), ["src_sheet"]), _norm(getattr(ri, name), ["src_sheet"])
        )


def _rename_defect_col(data: bytes) -> bytes:
    wb = _wb(data)
    ws = wb["RawData"]
    for c in ws[1]:
        if c.value == "불량 종류":
            c.value = "현상"
    return _save(wb)


def test_unknown_column_needs_mapping():
    ri = read_workbooks([("f", _rename_defect_col(make_workbook()))], DEFAULT_DICTIONARY)
    assert len(ri.needs_mapping) == 1
    m = ri.needs_mapping[0]
    assert m.missing_fields == ["defect_type"] and m.sheet == "RawData" and "현상" in m.columns
    assert len(ri.defects) == 0


def test_column_override_resolves():
    ri = read_workbooks(
        [("f", _rename_defect_col(make_workbook()))],
        DEFAULT_DICTIONARY,
        column_overrides={("f", "RawData"): {"현상": "defect_type"}},
    )
    assert ri.needs_mapping == [] and len(ri.defects) > 100


def test_no_total_row_autosum():
    wb = _wb(make_workbook())
    ws = wb["생산량"]
    ws.delete_rows(ws.max_row)
    ri = read_workbooks([("f", _save(wb))], DEFAULT_DICTIONARY)
    r = next(r for r in ri.reports if r.sheet == "생산량")
    assert "자동 합산" in r.reason and r.role == "production"


def test_ambiguous_horizontal_needs_mapping_and_override():
    wb = _wb(make_workbook())
    wb["판매량"].title = "실적"
    data = _save(wb)
    ri = read_workbooks([("f", data)], DEFAULT_DICTIONARY)
    assert [m.sheet for m in ri.needs_mapping] == ["실적"]
    ri2 = read_workbooks([("f", data)], DEFAULT_DICTIONARY, role_overrides={("f", "실적"): "sales"})
    assert ri2.needs_mapping == [] and len(ri2.sales) > 0


def test_blank_model_rows_dropped_in_wide_sheet():
    wb = _wb(make_workbook())
    ws = wb["생산량"]
    ws.insert_rows(ws.max_row)
    r = ws.max_row - 1
    for c in range(2, ws.max_column + 1):
        ws.cell(row=r, column=c, value=5)
    ri = read_workbooks([("f", _save(wb))], DEFAULT_DICTIONARY)
    assert "" not in set(ri.production.model)
    assert not ri.production.model.isna().any()
    rep = next(x for x in ri.reports if x.sheet == "생산량")
    assert rep.role == "production" and "모델명 없는 행 1개 제외" in rep.reason


def test_trailing_total_column_ignored():
    for label in ("합계", "Total", "계"):
        wb = _wb(make_workbook())
        for n in ("생산량", "판매량"):
            ws = wb[n]
            c = ws.max_column + 1
            ws.cell(row=1, column=c, value=label)
            for r in range(2, ws.max_row + 1):
                ws.cell(row=r, column=c, value=999999)
        ri = read_workbooks([("f", _save(wb))], DEFAULT_DICTIONARY)
        roles = {x.sheet: x.role for x in ri.reports}
        assert roles["생산량"] == "production" and roles["판매량"] == "sales", label
        assert ri.production.month.nunique() == 24 and 999999 not in set(ri.production.qty)
