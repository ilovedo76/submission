import pandas as pd

from qreport.app.viewmodel import (
    VERDICT_STYLE, alert_cards, analysis_key, as_cost_value, base_options, df_to_plan,
    doc_no_from_files, plan_to_df, total_diffs, validation_line,
)
from qreport.ingest.sheets import SheetReport
from qreport.rules.engine import Alert


def _alert(sev, key="trend"):
    return Alert("R1", sev, "전체", "2025-12", 0.01, 0.005, {}, key)


def test_alert_cards_pair_text_and_style_by_index():
    cards = alert_cards([_alert("높음", "pchart"), _alert("중간"), _alert("낮음")], ["a", "b", "c"])
    assert [c.text for c in cards] == ["a", "b", "c"]
    assert [c.label for c in cards] == ["높음", "중간", "낮음"]
    assert cards[0].chart_key == "pchart"
    assert len({c.color for c in cards}) == 3
    assert all(c.icon for c in cards)


def test_verdict_styles_cover_all_verdicts():
    assert set(VERDICT_STYLE) == {"개선 필요", "주의", "정상"}


def test_base_options_newest_first_default_is_last():
    opts = base_options(pd.Period("2025-10", "M"), pd.Period("2026-01", "M"))
    assert opts[0] == pd.Period("2026-01", "M") and opts[-1] == pd.Period("2025-10", "M")
    assert len(opts) == 4


def test_doc_no_from_files():
    assert doc_no_from_files([("b.xlsx", b""), ("a 보고.xlsx", b"")], pd.Period("2025-12", "M")) == "a 보고-2512"
    assert doc_no_from_files([], pd.Period("2025-12", "M")) == "QR-2512"


def test_as_cost_value():
    assert as_cost_value(None) is None and as_cost_value("0") is None and as_cost_value("") is None
    assert as_cost_value("45000") == 45000.0 and as_cost_value("abc") is None


def test_plan_roundtrip_drops_empty_rows():
    df = pd.DataFrame([{"담당": "김", "일정": "1주", "대책": "x"}, {"담당": "", "일정": None, "대책": ""}])
    assert df_to_plan(df) == [{"담당": "김", "일정": "1주", "대책": "x"}]
    assert list(plan_to_df([]).columns) == ["담당", "일정", "대책"]


def test_total_diffs_and_validation_line():
    reps = [SheetReport("f", "생산량", "production", "Total 대조: 차이 0"),
            SheetReport("f", "판매량", "sales", "Total 대조: 차이 12")]
    assert [r.sheet for r in total_diffs(reps)] == ["판매량"]
    line = validation_line({"유효": 90, "검토": 7, "제외": 3, "duplicates": 2}, reps)
    assert "검토 7" in line and "제외 3" in line and "Total 차이 1" in line
    assert "중복 의심 2건" in line
    noted = [SheetReport("f", "생산량", "production", "Total 대조: 차이 0; 모델명 없는 행 1개 제외")]
    assert total_diffs(noted) == []


def test_analysis_key_is_stable_and_sensitive():
    k = analysis_key("h", 1, pd.Period("2025-12", "M"), {"a": 0.1}, None)
    assert k == analysis_key("h", 1, pd.Period("2025-12", "M"), {"a": 0.1}, None)
    assert k != analysis_key("h", 2, pd.Period("2025-12", "M"), {"a": 0.1}, None)
    assert k != analysis_key("h", 1, pd.Period("2025-12", "M"), {"a": 0.2}, None)


def test_naming_helpers_live_in_engine_module():
    import qreport.naming as naming
    assert base_options is naming.base_options and doc_no_from_files is naming.doc_no_from_files
