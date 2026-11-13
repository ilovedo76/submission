import io

import pandas as pd
from PIL import Image

from qreport.render.charts import build_charts, to_png
from qreport.render.fonts import setup_fonts


def test_all_charts_have_reference_lines(company_result):
    charts = build_charts(company_result)
    assert len(charts) >= 10 + 4
    assert all(c.ref_lines for c in charts.values())
    assert all(c.title for c in charts.values())
    for m in company_result.dataset.models:
        assert f"pchart_{m}" in charts


def test_png_300dpi(company_result):
    spec = build_charts(company_result)["trend"]
    img = Image.open(io.BytesIO(to_png(spec)))
    assert round(img.info["dpi"][0]) == 300 and round(img.info["dpi"][1]) == 300


def test_model_filter_changes_denominator(company_result):
    charts = build_charts(company_result, model="양문형")
    assert round(charts["mis_curve"].data.loc[pd.Period("2025-12", "M"), 1], 5) == 0.00241
    assert not any(k.startswith("pchart_") for k in charts)


def test_target_line_added_to_trend(company_result):
    t = build_charts(company_result, targets={"전체": 0.01})["trend"]
    assert "목표" in t.ref_lines
    assert "목표" not in build_charts(company_result)["trend"].ref_lines


def test_korean_font_registered():
    import matplotlib
    setup_fonts()
    setup_fonts()
    assert matplotlib.rcParams["font.family"] == ["Malgun Gothic"]
    assert matplotlib.rcParams["axes.unicode_minus"] is False


def test_short_history_builds_all_charts():
    from qreport.ingest.sheets import DEFAULT_DICTIONARY
    from qreport.pipeline import analyze
    from qreport.ruleset import DEFAULT_RULES
    from tests.synth import make_workbook

    r = analyze([("s.xlsx", make_workbook(months=14))], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    charts = build_charts(r)
    assert len(charts) >= 10
    assert all(c.ref_lines and c.title for c in charts.values())
    for c in charts.values():
        assert to_png(c, dpi=50)
