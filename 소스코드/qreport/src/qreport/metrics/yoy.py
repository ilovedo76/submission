"""Year-over-year (prior 12 vs recent 12 months) two-proportion z-tests."""
from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
from scipy.stats import norm

from qreport.ingest.validate import Dataset
from qreport.periods import has_prior, prior_12, recent_12
from qreport.ruleset import RuleSet

COLUMNS = ["group", "x1", "n1", "p1", "x0", "n0", "p0", "ratio", "z", "pvalue", "worse"]


def _row(group, x1, n1, x0, n0, alpha):
    p1 = x1 / n1 if n1 > 0 else np.nan
    p0 = x0 / n0 if n0 > 0 else np.nan
    pool = (x1 + x0) / (n1 + n0) if (n1 + n0) > 0 else np.nan
    se = np.sqrt(pool * (1 - pool) * (1 / n1 + 1 / n0)) if n1 > 0 and n0 > 0 else np.nan
    z = (p1 - p0) / se if se and se > 0 else np.nan
    pv = float(2 * norm.sf(abs(z))) if not np.isnan(z) else np.nan
    ratio = p1 / p0 if p0 and p0 > 0 else np.nan
    worse = bool(p1 > p0 and pv < alpha) if not np.isnan(pv) else False
    return (group, int(x1), int(n1), p1, int(x0), int(n0), p0, ratio, z, pv, worse)


def yoy(ds: Dataset, base: pd.Period, rules: RuleSet,
        by: Literal["total", "model", "type"]) -> pd.DataFrame:
    if not has_prior(ds.first_month, base):
        return pd.DataFrame(columns=COLUMNS)
    w1, w0 = recent_12(base), prior_12(base)
    d = ds.analyzable()
    s = ds.sales

    def count(df, w):
        return int(w.mask(df["occur_month"]).sum())

    def sold(df, w):
        return int(df.loc[w.mask(df["month"]), "qty"].sum())

    rows = []
    if by == "total":
        rows.append(_row("전체", count(d, w1), sold(s, w1), count(d, w0), sold(s, w0), rules.alpha))
    elif by == "model":
        for m in ds.models:
            sm, dm = s[s["model"] == m], d[d["model"] == m]
            rows.append(_row(m, count(dm, w1), sold(sm, w1), count(dm, w0), sold(sm, w0), rules.alpha))
    else:
        n1, n0 = sold(s, w1), sold(s, w0)
        for t, g in d.groupby("defect_type"):
            rows.append(_row(t, count(g, w1), n1, count(g, w0), n0, rules.alpha))
    return pd.DataFrame(rows, columns=COLUMNS)
