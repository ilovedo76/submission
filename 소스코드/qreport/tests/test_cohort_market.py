import pandas as pd

from qreport.metrics.cohort import mis_table, observable_mis, pooled_6mis
from qreport.metrics.market import market_rate
from qreport.periods import prior_12, recent_12
from tests import golden


def P(s):
    return pd.Period(s, "M")


def test_observable_mis():
    assert observable_mis(P("2025-12"), P("2025-12")) == 1
    assert observable_mis(P("2025-07"), P("2025-12")) == 6
    assert observable_mis(P("2024-01"), P("2025-12")) == 6
    assert observable_mis(P("2026-01"), P("2025-12")) == 0


def test_model_denominator(company_ds):
    t = mis_table(company_ds, P("2025-12"), model="양문형")
    assert t.counts.loc[P("2025-12"), 1] == 10 and t.denom[P("2025-12")] == 4155
    assert round(t.rate.loc[P("2025-12"), 1] * 100, 3) == round(golden.MODEL_2512["양문형"][2] * 100, 3) == 0.241


def test_every_model_filter_uses_model_denominator(company_ds):
    prod = company_ds.production.set_index(["model", "month"])["qty"]
    for m in company_ds.models:
        t = mis_table(company_ds, P("2025-12"), model=m)
        assert len(t.denom) == 24
        for pm, v in t.denom.items():
            assert v == prod[(m, pm)]


def test_all_models_sums(company_ds):
    t = mis_table(company_ds, P("2025-12"))
    tm = [mis_table(company_ds, P("2025-12"), model=m) for m in company_ds.models]
    pm = P("2025-07")
    assert t.denom[pm] == sum(x.denom[pm] for x in tm)
    assert t.counts.loc[pm, 6] == sum(x.counts.loc[pm, 6] for x in tm)


def test_observation_mask(company_ds):
    t = mis_table(company_ds, P("2025-12"))
    assert t.rate.loc[P("2025-12")].isna().tolist() == [False, True, True, True, True, True]
    assert t.rate.loc[P("2025-07")].notna().all()


def test_non_december_base_month(company_ds):
    t = mis_table(company_ds, P("2025-11"))
    assert P("2025-12") not in t.rate.index and t.rate.loc[P("2025-11"), 2:].isna().all()


def test_non_cumulative_sums_to_cumulative(company_ds):
    c = mis_table(company_ds, P("2025-12"), cumulative=True)
    n = mis_table(company_ds, P("2025-12"), cumulative=False)
    row = P("2025-06")
    assert n.counts.loc[row].sum() == c.counts.loc[row, 6]


def test_mis3_recent(company_ds):
    t = mis_table(company_ds, P("2025-12"))
    r = t.rate.loc[[P("2025-08"), P("2025-09"), P("2025-10")], 3] * 100
    lo, hi = golden.MIS3_2508_2510
    assert round(r.min(), 3) == round(lo * 100, 3)
    assert round(r.max(), 3) == round(hi * 100, 3)


def test_kimchi_level(company_ds):
    base = P("2025-12")
    prior = pooled_6mis(company_ds, base, prior_12(base), model="김치")[2]
    recent = pooled_6mis(company_ds, base, recent_12(base), model="김치")[2]
    assert round(prior * 100, 3) == round(golden.KIMCHI_6MIS[0] * 100, 3)
    assert round(recent * 100, 3) == round(golden.KIMCHI_6MIS[1] * 100, 3)


def test_pooled_excludes_immature(company_ds):
    base = P("2025-12")
    n, q, r = pooled_6mis(company_ds, base, recent_12(base))
    t = mis_table(company_ds, base)
    mature = t.denom[(t.denom.index >= P("2025-01")) & (t.denom.index <= P("2025-07"))]
    assert q == mature.sum()


def test_market_rate_years(company_ds):
    m = market_rate(company_ds)
    for year, g in zip((2024, 2025), golden.MARKET):
        s = m[m["month"].map(lambda p: p.year) == year]
        assert round(s["defects"].sum() / s["sales"].sum() * 100, 3) == round(g * 100, 3)


def test_market_control_limits(company_ds):
    import numpy as np
    from qreport.metrics.market import market_control_limits
    from qreport.ruleset import DEFAULT_RULES

    base = P("2025-12")
    for model in (None, "양문형"):
        t = market_control_limits(company_ds, base, DEFAULT_RULES, model)
        mr = market_rate(company_ds, model)
        assert list(t["month"]) == list(mr["month"][mr["month"] <= base])
        pbar = mr["defects"].sum() / mr["sales"].sum()
        assert np.allclose(t["pbar"], pbar)
        assert np.allclose(t["ucl"], pbar + 3 * np.sqrt(pbar * (1 - pbar) / t["sales"]))
        assert (t["ucl"] > t["pbar"]).all()
