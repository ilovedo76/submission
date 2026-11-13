from pathlib import Path

import io

import openpyxl
from streamlit.testing.v1 import AppTest

from tests.synth import make_workbook

APP = str(Path(__file__).parents[1] / "src" / "qreport" / "app" / "main.py")
SETTINGS = str(Path(__file__).parents[1] / "src" / "qreport" / "app" / "settings.py")


def _texts(at):
    return "".join(m.value for m in at.markdown) + "".join(m.value for m in at.sidebar.markdown)


def test_app_boots_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("QREPORT_DATA", str(tmp_path))
    at = AppTest.from_file(APP).run(timeout=60)
    assert not at.exception
    assert "엑셀" in _texts(at)


def test_app_with_company_file(tmp_path, monkeypatch, company_bytes):
    monkeypatch.setenv("QREPORT_DATA", str(tmp_path))
    at = AppTest.from_file(APP)
    at.session_state["files"] = [("c.xlsx", company_bytes)]
    at.run(timeout=180)
    assert not at.exception
    assert "개선 필요" in _texts(at)
    assert len([b for b in at.button if b.label == "근거 차트 보기"]) >= 4

    # clicking a card selects its chart
    card = next(b for b in at.button if b.label == "근거 차트 보기")
    card.click().run(timeout=180)
    assert not at.exception
    from qreport.pipeline import analyze
    from qreport.ingest.sheets import DEFAULT_DICTIONARY
    from qreport.ruleset import DEFAULT_RULES
    r = analyze([("c.xlsx", company_bytes)], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    assert at.radio(key="chart").value == r.alerts[0].chart_key

    # same input in a new session reuses the saved job
    at2 = AppTest.from_file(APP)
    at2.session_state["files"] = [("c.xlsx", company_bytes)]
    at2.run(timeout=180)
    assert any("지난 작업 #1 불러옴" in c.value for c in at2.caption)


def test_settings_page_saves_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("QREPORT_DATA", str(tmp_path))
    at = AppTest.from_file(SETTINGS).run(timeout=60)
    assert not at.exception
    at.number_input(key="r_ucl").set_value(2.5)
    next(b for b in at.button if "룰 저장" in b.label).click().run(timeout=60)
    assert not at.exception
    assert any("v2" in s.value for s in at.success)
    from qreport.store.db import Store
    v, r = Store(tmp_path / "qreport.db").current_rules()
    assert v == 2 and r.ucl_sigma == 2.5


_WB_BYTES = []


def _WB():
    if not _WB_BYTES:   # xlsx bytes embed timestamps: build once so the input hash is stable
        _WB_BYTES.append(make_workbook())
    return _WB_BYTES[0]


def _synth_app(tmp_path, monkeypatch, data=None):
    monkeypatch.setenv("QREPORT_DATA", str(tmp_path))
    at = AppTest.from_file(APP)
    at.session_state["files"] = [("w.xlsx", data or _WB())]
    return at


def test_jobs_keyed_by_base_month_and_job_click_keeps_state(tmp_path, monkeypatch):
    from qreport.store.db import Store
    at = _synth_app(tmp_path, monkeypatch).run(timeout=180)
    assert not at.exception
    store = Store(tmp_path / "qreport.db")
    first = store.list_jobs()[0]
    assert first["id"] == 1
    # notes on job 1, then switch base month -> a second job with its own notes
    at.text_area(key="issues_1").set_value("메모 일").run(timeout=180)
    assert store.load_notes(1).issues == "메모 일"
    options = at.selectbox(key="base_sel").options
    at.selectbox(key="base_sel").select(at.selectbox(key="base_sel").options[1]).run(timeout=180)
    assert not at.exception and len(store.list_jobs()) == 2
    assert store.load_notes(2).issues == ""
    assert len({j["base_month"] for j in store.list_jobs()}) == 2
    assert len(options) > 1
    # clicking job #1 restores its base and notes
    next(b for b in at.sidebar.button if b.label.startswith("#1 ")).click().run(timeout=180)
    assert not at.exception
    assert at.text_area(key="issues_1").value == "메모 일"
    assert str(at.selectbox(key="base_sel").value.strftime("%Y-%m")) == first["base_month"]
    assert len(store.list_jobs()) == 2
    # same base again reuses the same job
    at2 = _synth_app(tmp_path, monkeypatch).run(timeout=180)
    assert len(store.list_jobs()) == 2 and any("#1 불러옴" in c.value for c in at2.caption)


def test_cost_text_shown_with_as_cost(tmp_path, monkeypatch):
    at = _synth_app(tmp_path, monkeypatch)
    at.session_state["as_cost_in"] = 45000
    at.run(timeout=180)
    assert not at.exception
    text = _texts(at)
    assert "예상 비용" in text and "원" in text.split("예상 비용")[1][:40]


def test_mapping_form_flow(tmp_path, monkeypatch):
    wb = openpyxl.load_workbook(io.BytesIO(make_workbook()))
    wb["RawData"]["A1"] = "기종XYZ"
    wb["생산량"].title = "S1"
    buf = io.BytesIO()
    wb.save(buf)
    at = _synth_app(tmp_path, monkeypatch, buf.getvalue()).run(timeout=180)
    assert not at.exception and at.warning
    for sel in at.selectbox:
        if sel.key.startswith("col_") and sel.key.endswith("model"):
            sel.select("기종XYZ")
        if sel.key.startswith("role_") and "production" in sel.options:
            sel.select("production")
    next(b for b in at.button if "확정" in b.label).click().run(timeout=180)
    assert not at.exception and not at.warning
    assert "개선 필요" in _texts(at) or "주의" in _texts(at) or "정상" in _texts(at)
    from qreport.store.db import Store
    assert Store(tmp_path / "qreport.db").dictionary().get("기종XYZ") == "model"


def test_missing_sheet_shows_korean_error(tmp_path, monkeypatch):
    wb = openpyxl.load_workbook(io.BytesIO(make_workbook()))
    del wb["판매량"]
    buf = io.BytesIO()
    wb.save(buf)
    at = _synth_app(tmp_path, monkeypatch, buf.getvalue()).run(timeout=180)
    assert not at.exception
    assert any("판매량 시트를 찾지 못했습니다" in e.value for e in at.error)
    assert not at.code   # no traceback for an input problem
    assert "개선 필요" not in _texts(at) and "정상" not in _texts(at)


def test_validation_line_shows_duplicates(tmp_path, monkeypatch):
    at = _synth_app(tmp_path, monkeypatch).run(timeout=180)
    assert not at.exception
    assert "중복 의심" in _texts(at)


def _cards(at):
    return [m.value for m in at.markdown if "<b>R" in m.value]


def test_reopened_job_uses_its_rule_version(tmp_path, monkeypatch):
    import dataclasses

    from qreport.store.db import Store
    at = _synth_app(tmp_path, monkeypatch).run(timeout=180)
    assert not at.exception
    before = _cards(at)
    at.text_area(key="issues_1").set_value("v1 메모").run(timeout=180)
    store = Store(tmp_path / "qreport.db")
    _, r = store.current_rules()
    assert store.save_rules(dataclasses.replace(r, ucl_sigma=1.0, min_count=1)) == 2
    at.run(timeout=180)                       # same upload now analysed with rules v2
    assert not at.exception
    assert any("룰 v2" in c.value for c in at.caption) and _cards(at) != before
    next(b for b in at.sidebar.button if b.label.startswith("#1 ")).click().run(timeout=180)
    assert not at.exception
    assert any("룰 v1" in c.value for c in at.caption)
    assert _cards(at) == before
    assert at.text_area(key="issues_1").value == "v1 메모"
    assert store.rules_by_version(1) == r


def test_settings_has_no_prior_months_input(tmp_path, monkeypatch):
    monkeypatch.setenv("QREPORT_DATA", str(tmp_path))
    at = AppTest.from_file(SETTINGS).run(timeout=60)
    assert not at.exception
    keys = {w.key for w in at.number_input}
    assert "r_ucl" in keys and "r_pm" not in keys


def test_change_percent_rounded_like_report(tmp_path, monkeypatch):
    import re
    at = _synth_app(tmp_path, monkeypatch).run(timeout=180)
    assert not at.exception
    cap = next(c.value for c in at.caption if "전년 대비" in c.value)
    assert re.search(r"전년 대비 [+-]\d+%\)", cap), cap


def test_plan_editor_uses_page_inputs_not_grid(tmp_path, monkeypatch):
    """개선 계획 must be plain inputs: the data_editor's floating cell editor does not
    scroll with the page (it stays fixed on screen while the user scrolls)."""
    from qreport.store.db import Store
    at = _synth_app(tmp_path, monkeypatch).run(timeout=180)
    assert not at.exception
    assert len(at.get("arrow_data_frame")) == 0 or all(
        "plan" not in (getattr(e, "key", "") or "") for e in at.get("arrow_data_frame"))
    store = Store(tmp_path / "qreport.db")
    at.text_input(key="plan_1_0_담당").set_value("김품질").run(timeout=180)
    at.text_input(key="plan_1_0_대책").set_value("로트 추적").run(timeout=180)
    assert store.load_notes(1).plan == [{"담당": "김품질", "일정": "", "대책": "로트 추적"}]
    at.button(key="plan_add_1").click().run(timeout=180)
    at.text_input(key="plan_1_1_담당").set_value("이공정").run(timeout=180)
    assert [p["담당"] for p in store.load_notes(1).plan] == ["김품질", "이공정"]
    at.button(key="plan_del_1_0").click().run(timeout=180)
    assert not at.exception
    assert store.load_notes(1).plan == [{"담당": "이공정", "일정": "", "대책": ""}]
    assert at.text_input(key="plan_1_1_담당").value == "이공정"


def test_evidence_click_scrolls_to_chart_once(tmp_path, monkeypatch):
    at = _synth_app(tmp_path, monkeypatch).run(timeout=180)
    assert not at.exception
    assert len(at.get("iframe")) == 0          # no scroll script on a normal render
    card = next(b for b in at.button if (b.key or "").startswith("card_"))
    card.click().run(timeout=180)
    assert not at.exception
    assert at.session_state["chart_scroll"] == 1 and at.session_state["chart_scrolled"] == 1
    assert len(at.get("iframe")) == 1          # scroll script injected for this click
    at.run(timeout=180)                        # an unrelated rerun must not scroll again
    assert len(at.get("iframe")) == 0


def test_chart_image_height_capped():
    from qreport.app.theme import CSS
    assert ".st-key-chart_box" in CSS and "max-height" in CSS
