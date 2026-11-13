"""Chart data shared by the report charts and the web summary (pandas only, no Matplotlib)."""
from __future__ import annotations

import pandas as pd

from qreport.periods import prior_12, recent_12

PARETO_TOP = 10
OTHER = "기타"


def pareto_table(ds, base: pd.Period, model: str | None = None) -> pd.DataFrame:
    """Recent-12-month defect counts by type (desc, top 10 + 기타) with the cumulative share."""
    d = ds.analyzable()
    if model is not None:
        d = d[d["model"] == model]
    d = d[recent_12(base).mask(d["occur_month"])]
    c = d.groupby("defect_type").size().sort_values(ascending=False, kind="stable")
    if len(c) > PARETO_TOP:
        c = pd.concat([c.iloc[:PARETO_TOP], pd.Series({OTHER: int(c.iloc[PARETO_TOP:].sum())})])
    cum = c.cumsum() / c.sum() if c.sum() else c * 0.0
    return pd.DataFrame({"count": c.values, "cum_share": cum.values}, index=c.index)


def model_type_counts(ds, base: pd.Period, model: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(recent 12, prior 12) defect counts, index = models (or [model]), columns = all defect types."""
    models = [model] if model else list(ds.models)
    d = ds.analyzable()
    w1, w0 = recent_12(base), prior_12(base)
    types = sorted(d["defect_type"].unique())
    recent = pd.DataFrame(0, index=models, columns=types, dtype=float)
    prior = pd.DataFrame(0, index=models, columns=types, dtype=float)
    for m in models:
        dm = d[d["model"] == m]
        for t in types:
            g = dm[dm["defect_type"] == t]["occur_month"]
            recent.loc[m, t] = sum(w1.contains(x) for x in g)
            prior.loc[m, t] = sum(w0.contains(x) for x in g)
    return recent, prior
