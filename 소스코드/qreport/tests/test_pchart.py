import pandas as pd

from qreport.ingest.validate import Dataset
from qreport.metrics.pchart import p_chart
from qreport.ruleset import DEFAULT_RULES
from tests import golden
from tests.synth import make_workbook


def P(s):
    return pd.Period(s, "M")


def test_overall_pchart(company_ds):
    pc = p_chart(company_ds, P("2025-12"), DEFAULT_RULES)
    assert round(pc.pbar * 100, 3) == 0.035 and pc.df.x.sum() == 226
    assert round(pc.df.set_index("prod_month").ucl[P("2025-12")] * 100, 3) == 0.070
    assert pc.test1 == [P("2025-11"), P("2025-12")]
    assert not any(m.year == 2024 for m in pc.test1)
    assert list(pc.df.columns) == ["prod_month", "x", "n", "p", "ucl", "lcl", "above_center"]


def test_model_pcharts(company_ds):
    for model, (x, n, p1, ucl) in golden.MODEL_2512.items():
        pc = p_chart(company_ds, P("2025-12"), DEFAULT_RULES, model=model)
        row = pc.df.set_index("prod_month").loc[P("2025-12")]
        assert (row.x, row.n) == (x, n)
        assert round(row.p, 5) == p1 and round(row.ucl, 5) == ucl
    for model in company_ds.models:
        pc = p_chart(company_ds, P("2025-12"), DEFAULT_RULES, model=model)
        if "김치" in model:
            assert P("2025-12") not in pc.test1
        else:
            assert P("2025-12") in pc.test1


def test_backtest_base_2511(company_ds):
    pc = p_chart(company_ds, P("2025-11"), DEFAULT_RULES)
    assert P("2025-11") in pc.test1
    assert round(pc.df.set_index("prod_month").ucl[P("2025-11")] * 100, 3) == 0.063  # plan said 0.069, which is the base-2512 value of this month


def _tiny_ds(x_last: int) -> Dataset:
    months = pd.period_range("2025-01", "2025-12", freq="M")
    prod = pd.DataFrame({"model": "A", "month": months, "qty": 1000})
    prod.loc[prod.month == P("2025-12"), "qty"] = 200
    rows = [(P("2025-%02d" % m),) * 2 for m in range(1, 12) for _ in range(1)]
    rows += [(P("2025-12"),) * 2 for _ in range(x_last)]
    d = pd.DataFrame(rows, columns=["prod_month", "occur_month"])
    d["model"], d["use_months"], d["status"] = "A", 1, "유효"
    return Dataset(defects=d, production=prod, sales=pd.DataFrame(), models=["A"],
                   first_month=months[0], last_occur_month=months[-1], summary={})


def test_min_count_blocks_small_breach():
    ds = _tiny_ds(2)
    pc = p_chart(ds, P("2025-12"), DEFAULT_RULES)
    last = pc.df.iloc[-1]
    assert last.p > last.ucl and last.x == 2
    assert pc.test1 == []
    pc3 = p_chart(_tiny_ds(3), P("2025-12"), DEFAULT_RULES)
    assert pc3.test1 == [P("2025-12")]


def test_run9_synthetic():
    from qreport.ingest.sheets import DEFAULT_DICTIONARY, read_workbooks
    from qreport.ingest.validate import build_dataset

    def build(an):
        b = make_workbook(months=24, seed=7, anomalies=frozenset(an))  # see test_engine.SEED
        return build_dataset(read_workbooks([("s.xlsx", b)], DEFAULT_DICTIONARY))

    base = P("2025-12")
    pc = p_chart(build({"run_up"}), base, DEFAULT_RULES)
    d = pc.df.set_index("prod_month")
    assert base in pc.run9 and d.above_center[base]
    assert pc.test1 == []
    assert p_chart(build(set()), base, DEFAULT_RULES).run9 == []
