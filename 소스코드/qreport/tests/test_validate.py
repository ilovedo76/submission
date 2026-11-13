import copy

import pandas as pd
import pytest

from qreport.ingest.sheets import DEFAULT_DICTIONARY, read_workbooks
from qreport.ingest.validate import build_dataset
from tests.synth import make_workbook


@pytest.fixture(scope="module")
def raw():
    return read_workbooks([("s.xlsx", make_workbook(months=12, seed=1))], DEFAULT_DICTIONARY)


def _copy(raw):
    r = copy.copy(raw)
    r.defects = raw.defects.copy()
    r.production = raw.production.copy()
    r.sales = raw.sales.copy()
    return r


def test_company_all_valid(company_ds):
    assert company_ds.summary == {"유효": 1000, "검토": 0, "제외": 0,
                                  "duplicates": company_ds.summary["duplicates"]}
    assert str(company_ds.last_occur_month) == "2025-12"
    assert set(company_ds.models) == {"4Door", "2Door", "양문형", "김치"}


def test_use_months_mismatch_review(raw):
    r = _copy(raw)
    i = r.defects.index[5]
    orig = int(r.defects.at[i, "use_months"])
    r.defects.at[i, "use_months"] = orig + 7
    ds = build_dataset(r)
    row = ds.defects.loc[i]
    assert row.status == "검토"
    assert row.use_months == orig
    assert f"사용개월 불일치: 원본 {orig + 7} → 재계산 {orig}" in row.reason
    assert ds.summary["검토"] == 1


def test_occur_before_prod_excluded(raw):
    r = _copy(raw)
    i = r.defects.index[3]
    r.defects.at[i, "occur_month"] = r.defects.at[i, "prod_month"] - 1
    ds = build_dataset(r)
    assert ds.defects.loc[i, "status"] == "제외"
    assert "발생월이 생산월보다 앞섬" in ds.defects.loc[i, "reason"]
    assert i not in ds.analyzable().index


def test_missing_denominator_excluded(raw):
    r = _copy(raw)
    first = r.production["month"].min()
    r.production = r.production[r.production["month"] != first]
    ds = build_dataset(r)
    hit = ds.defects[ds.defects["prod_month"] == first]
    assert len(hit) > 0
    assert (hit["status"] == "제외").all()
    assert hit["reason"].str.contains("분모(생산량) 없음", regex=False).all()
    assert ds.first_month == r.production["month"].min()


def test_duplicates_kept(raw):
    base = build_dataset(raw)
    r = _copy(raw)
    r.defects = pd.concat([r.defects, r.defects.iloc[[0]]], ignore_index=True)
    ds = build_dataset(r)
    assert len(ds.defects) == len(base.defects) + 1
    assert ds.summary["duplicates"] == base.summary["duplicates"] + 1
    assert (ds.defects["status"] != "제외").all()
    assert "중복 의심" in ds.defects.iloc[-1]["reason"]


def test_source_tracking(company_ds):
    r = company_ds.defects.iloc[0]
    assert r.src_sheet == "RawData" and r.src_row == 2


@pytest.mark.parametrize("col", ["prod_month", "occur_month"])
def test_unreadable_month_excluded(raw, col):
    r = _copy(raw)
    i = r.defects.index[4]
    r.defects.at[i, col] = pd.NaT
    ds = build_dataset(r)
    assert ds.defects.loc[i, "status"] == "제외"
    assert ds.defects.loc[i, "reason"] == "생산월/발생월 인식 불가"
    assert i not in ds.analyzable().index


@pytest.mark.parametrize("col,blank", [("model", None), ("model", ""), ("defect_type", None),
                                       ("defect_type", "")])
def test_blank_model_or_type_excluded(raw, col, blank):
    r = _copy(raw)
    i = r.defects.index[6]
    r.defects.at[i, col] = blank
    ds = build_dataset(r)
    assert ds.defects.loc[i, "status"] == "제외"
    assert "모델/불량 유형 없음" in ds.defects.loc[i, "reason"]
    assert "" not in ds.models and None not in ds.models


def test_blank_months_in_workbook_analyze():
    import io

    import openpyxl

    from qreport.pipeline import AnalysisResult, analyze
    from qreport.ruleset import DEFAULT_RULES

    wb = openpyxl.load_workbook(io.BytesIO(make_workbook(seed=7)))
    ws = wb["RawData"]
    ws.cell(row=5, column=2).value = None
    ws.cell(row=6, column=3, value="미상")
    ws.cell(row=7, column=1).value = None
    buf = io.BytesIO()
    wb.save(buf)
    res = analyze([("b.xlsx", buf.getvalue())], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    assert isinstance(res, AnalysisResult)
    assert res.dataset.summary["제외"] == 3
