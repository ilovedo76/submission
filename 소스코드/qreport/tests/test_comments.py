import dataclasses
import math
import re

import pandas as pd

from qreport.ingest.sheets import DEFAULT_DICTIONARY
from qreport.pipeline import analyze
from qreport.rules.comments import comment_lines, verdict_line
from qreport.ruleset import DEFAULT_RULES
from tests.synth import make_workbook


def _numbers(obj, out):
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numbers(v, out)
    elif isinstance(obj, pd.DataFrame):
        for c in obj.columns:
            if pd.api.types.is_numeric_dtype(obj[c]):
                out.update(float(v) for v in obj[c].dropna())


def _candidates(r):
    """Every rate that alerts, metrics or the headline carry, as a rounded percentage."""
    out = set()
    for a in r.alerts:
        _numbers([a.value, a.threshold, a.detail], out)
    m = r.metrics
    for obj in (m.pchart_total.df, [p.df for p in m.pchart_by_model.values()], m.level,
                m.market_recent, m.yoy_total, m.yoy_model, m.yoy_type, r.headline.level):
        _numbers(obj, out)
    return {round(v * 100, 3) for v in out if math.isfinite(v)}


def test_r1_sentence(company_result):
    lines = comment_lines(company_result)
    assert lines[0].startswith("25.12 생산분 1MIS 0.136%(36건)로 관리상한 0.070% 초과")
    assert "25.11(0.083%, 23건)에 이어 2개월 연속 이탈." in lines[0]


def test_hypothesis_tag(company_result):
    lines = comment_lines(company_result)
    tagged = [l for l in lines if "점검 권고" in l]
    assert tagged and all(l.endswith("(가설)") for l in tagged)


def test_one_line_per_alert_and_verdict(company_result):
    assert len(comment_lines(company_result)) == len(company_result.alerts)
    assert verdict_line(company_result) == \
        f"종합 판정: 개선 필요 (경고 {len(company_result.alerts)}건)"


def test_numbers_come_from_metrics(company_result):
    cand = _candidates(company_result)
    found = [float(s) for l in comment_lines(company_result)
             for s in re.findall(r"(\d+\.\d{3})%", l)]
    assert found
    for v in found:
        assert any(abs(v - c) < 1e-9 for c in cand), v


def test_no_alerts_line():
    r = analyze([("s.xlsx", make_workbook(seed=7))], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    r = dataclasses.replace(r, alerts=[], verdict="정상")
    assert comment_lines(r) == ["관리 기준을 벗어난 항목이 없습니다."]
    assert verdict_line(r) == "종합 판정: 정상 (경고 0건)"


def test_shared_number_formats(company_result):
    from qreport.render.content import headline_rows, yoy_rows
    from qreport.rules.comments import change_text, ratio_text
    assert ratio_text(1.5734) == "1.6배" and change_text(57.3) == "+57%" and change_text(-4.6) == "-5%"
    rows = yoy_rows(company_result)
    assert all(r["ratio"] == "-" or r["ratio"].endswith("배") and len(r["ratio"].split(".")[1]) == 2
               for r in rows)   # one decimal + "배"
    change = next(r for r in headline_rows(company_result) if r["name"] == "변화")["value"]
    assert change.startswith(change_text(company_result.headline.change_pct))
