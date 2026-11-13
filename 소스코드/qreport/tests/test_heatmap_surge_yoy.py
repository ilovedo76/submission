import pandas as pd

from qreport.ingest.sheets import DEFAULT_DICTIONARY, read_workbooks
from qreport.ingest.validate import build_dataset
from qreport.metrics.heatmap import heatmap
from qreport.metrics.surge import type_surge
from qreport.metrics.yoy import yoy
from qreport.ruleset import DEFAULT_RULES
from tests.synth import make_workbook


def P(s):
    return pd.Period(s, "M")


def synth_ds(**kw):
    return build_dataset(read_workbooks([("s.xlsx", make_workbook(**kw))], DEFAULT_DICTIONARY))


def test_heatmap_mask_and_highlight(company_ds):
    hm = heatmap(company_ds, P("2025-12"), DEFAULT_RULES.replace(heatmap_basis="mean"))
    assert round(hm.baseline[3] * 100, 3) == 0.077
    assert hm.highlight.loc[[P("2025-08"), P("2025-09"), P("2025-10")], 3].all()
    assert not hm.highlight[hm.rate.isna()].any().any()


def test_heatmap_max_baseline_ge_mean(company_ds):
    b = P("2025-12")
    mx = heatmap(company_ds, b, DEFAULT_RULES).baseline
    mn = heatmap(company_ds, b, DEFAULT_RULES.replace(heatmap_basis="mean")).baseline
    assert (mx >= mn - 1e-12).all()


def test_yoy_total(company_ds):
    r = yoy(company_ds, P("2025-12"), DEFAULT_RULES, "total").iloc[0]
    assert (round(r.p0 * 100, 3), round(r.p1 * 100, 3)) == (0.122, 0.191) and r.worse


def test_yoy_type_ansiwonham(company_ds):
    r = yoy(company_ds, P("2025-12"), DEFAULT_RULES, "type").set_index("group").loc["안시원함"]
    assert (r.x0, r.x1) == (76, 177) and r.worse


def test_yoy_model_rows(company_ds):
    r = yoy(company_ds, P("2025-12"), DEFAULT_RULES, "model")
    assert set(r["group"]) == set(company_ds.models)


def test_short_history_skips_yoy():
    ds = synth_ds(months=14)
    b = ds.first_month + 13
    assert yoy(ds, b, DEFAULT_RULES, "total").empty
    assert yoy(ds, b, DEFAULT_RULES, "type").empty
    hm = heatmap(ds, b, DEFAULT_RULES)
    assert not hm.highlight.any().any() and hm.baseline.empty


def test_surge_synthetic():
    # Poisson noise in clean synth data does trip the 1.5x rule occasionally, so the
    # discriminating check is a >=3x surge of the injected type in the last month.
    ds = synth_ds(months=24, anomalies={"type_surge"})
    b = ds.first_month + 23
    s = type_surge(ds, b, DEFAULT_RULES)
    assert len(s) >= 1 and (s["count"] >= 8).all()
    hit = s[(s["month"] == b) & (s["defect_type"] == "누수")]
    assert len(hit) == 1 and hit.iloc[0]["ratio"] >= 3
    clean = synth_ds(months=24)
    cs = type_surge(clean, b, DEFAULT_RULES)
    assert (cs["ratio"] < 3).all()


def test_yoy_worse_synthetic():
    ds = synth_ds(months=24, anomalies={"yoy_worse"})
    assert yoy(ds, ds.first_month + 23, DEFAULT_RULES, "total").iloc[0].worse
