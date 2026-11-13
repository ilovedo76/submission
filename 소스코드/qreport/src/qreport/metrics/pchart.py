"""P control chart on 1MIS with test 1, run rule and overdispersion check."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from qreport.ingest.validate import Dataset
from qreport.metrics.cohort import mis_table
from qreport.ruleset import RuleSet


@dataclass
class PChart:
    df: pd.DataFrame
    pbar: float
    test1: list
    run9: list
    sigma_z: float
    laney_suggested: bool


def _run_ends(above: list[bool], months: list, run_length: int) -> list:
    out, streak, prev = [], 0, None
    for m, a in zip(months, above):
        streak = streak + 1 if (prev is not None and a == prev) else 1
        prev = a
        if streak >= run_length:
            out.append(m)
    return out


def control_limits(x: pd.Series, n: pd.Series, sigma: float) -> tuple[float, pd.Series, pd.Series]:
    """Binomial p-chart centre line, upper and lower limits (sigma-wide) for counts x of sizes n."""
    pbar = float(x.sum() / n.sum())
    se = np.sqrt(pbar * (1 - pbar) / n)
    return pbar, pbar + sigma * se, (pbar - sigma * se).clip(lower=0)


def p_chart(ds: Dataset, base: pd.Period, rules: RuleSet,
            model: str | None = None) -> PChart:
    t = mis_table(ds, base, model)
    x = t.counts[1].dropna()
    n = t.denom.loc[x.index]
    if x.empty or n.sum() <= 0:  # e.g. a model with no production up to base
        cols = ["prod_month", "x", "n", "p", "ucl", "lcl", "above_center"]
        return PChart(df=pd.DataFrame(columns=cols), pbar=float("nan"), test1=[], run9=[],
                      sigma_z=float("nan"), laney_suggested=False)
    p = x / n
    pbar, ucl, lcl = control_limits(x, n, rules.ucl_sigma)
    se = np.sqrt(pbar * (1 - pbar) / n)
    above = p > pbar
    df = pd.DataFrame({
        "prod_month": x.index, "x": x.to_numpy(), "n": n.to_numpy(),
        "p": p.to_numpy(), "ucl": ucl.to_numpy(), "lcl": lcl.to_numpy(),
        "above_center": above.to_numpy(),
    }).reset_index(drop=True)
    months = list(df["prod_month"])
    test1 = [m for m, pi, u, xi in zip(months, df["p"], df["ucl"], df["x"])
             if pi > u and xi >= rules.min_count]
    run9 = _run_ends(list(df["above_center"]), months, rules.run_length)
    z = (p - pbar) / se
    sigma_z = float(z.std(ddof=1)) if len(z) > 1 else float("nan")
    return PChart(df=df, pbar=pbar, test1=test1, run9=run9, sigma_z=sigma_z,
                  laney_suggested=bool(sigma_z > rules.overdispersion_sigma_z))
