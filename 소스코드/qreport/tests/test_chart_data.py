"""Chart data functions shared by the report charts and the web summary (synth: never skipped)."""
import pandas as pd
import pytest

from qreport.ingest.sheets import DEFAULT_DICTIONARY
from qreport.pipeline import analyze
from qreport.render.chart_data import model_type_counts, pareto_table
from qreport.render.charts import build_charts
from qreport.ruleset import DEFAULT_RULES
from tests.synth import make_workbook


@pytest.fixture(scope="module")
def result():
    data = make_workbook(months=24, seed=3, anomalies={"type_surge", "yoy_worse"})
    return analyze([("s.xlsx", data)], None, DEFAULT_RULES, DEFAULT_DICTIONARY)


@pytest.mark.parametrize("model", [None, "양문형"])
def test_charts_use_the_data_functions(result, model):
    specs = build_charts(result, model)
    ds, base = result.dataset, result.base
    pd.testing.assert_frame_equal(specs["pareto"].data, pareto_table(ds, base, model))
    recent, prior = model_type_counts(ds, base, model)
    pd.testing.assert_frame_equal(specs["model_type"].data, recent - prior)
    assert (recent >= 0).all().all() and (prior >= 0).all().all()
    assert list(recent.index) == ([model] if model else list(ds.models))


def test_pareto_top10_plus_other():
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(make_workbook(months=24, seed=1)))
    ws = wb["RawData"]
    for i, row in enumerate(ws.iter_rows(min_row=2, max_row=13)):
        row[3].value = f"유형{i:02d}"                # 12 extra one-off types
    buf = io.BytesIO()
    wb.save(buf)
    r = analyze([("s.xlsx", buf.getvalue())], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    t = pareto_table(r.dataset, r.base, None)
    assert len(t) <= 11
    if len(t) == 11:
        assert t.index[-1] == "기타"
    assert t["cum_share"].iloc[-1] == pytest.approx(1.0)
