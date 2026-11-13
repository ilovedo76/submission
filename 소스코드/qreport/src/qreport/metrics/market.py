"""Market defect rate (occurrence-month basis)."""
from __future__ import annotations

import pandas as pd

from qreport.ingest.validate import Dataset
from qreport.metrics.pchart import control_limits
from qreport.ruleset import RuleSet


def market_rate(ds: Dataset, model: str | None = None,
                defect_type: str | None = None) -> pd.DataFrame:
    s = ds.sales if model is None else ds.sales[ds.sales["model"] == model]
    sales = s.groupby("month")["qty"].sum().sort_index()
    d = ds.analyzable()
    if model is not None:
        d = d[d["model"] == model]
    if defect_type is not None:
        d = d[d["defect_type"] == defect_type]
    cnt = d.groupby("occur_month").size().reindex(sales.index, fill_value=0)
    out = pd.DataFrame({"month": sales.index, "defects": cnt.to_numpy(dtype=int),
                        "sales": sales.to_numpy()})
    out["rate"] = out["defects"] / out["sales"].where(out["sales"] > 0)
    return out.reset_index(drop=True)


def market_control_limits(ds: Dataset, base: pd.Period, rules: RuleSet,
                          model: str | None = None) -> pd.DataFrame:
    """Monthly market rate up to base with the pooled centre line and p-chart UCL."""
    mr = market_rate(ds, model)
    mr = mr[mr["month"] <= base].reset_index(drop=True)
    if mr.empty or mr["sales"].sum() <= 0:
        return mr.assign(pbar=float("nan"), ucl=float("nan"))
    pbar, ucl, _ = control_limits(mr["defects"], mr["sales"].where(mr["sales"] > 0), rules.ucl_sigma)
    return mr.assign(pbar=pbar, ucl=ucl.to_numpy())
