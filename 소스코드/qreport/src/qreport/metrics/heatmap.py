"""Production-month x MIS heatmap with highlighting vs the prior-year baseline."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from qreport.ingest.validate import Dataset
from qreport.metrics.cohort import KS, mis_table
from qreport.periods import has_prior, prior_12, recent_12
from qreport.ruleset import RuleSet


@dataclass
class Heatmap:
    rate: pd.DataFrame
    baseline: pd.Series
    highlight: pd.DataFrame


def heatmap(ds: Dataset, base: pd.Period, rules: RuleSet,
            model: str | None = None) -> Heatmap:
    t = mis_table(ds, base, model=model, cumulative=True)
    rate = t.rate
    highlight = pd.DataFrame(False, index=rate.index, columns=rate.columns)
    if not has_prior(ds.first_month, base):
        return Heatmap(rate, pd.Series(dtype=float, name="baseline"), highlight)

    prior = [m for m in prior_12(base).months() if m in rate.index]
    vals = {}
    for k in KS:
        coh = [m for m in prior if not np.isnan(rate.loc[m, k])]
        if not coh:
            vals[k] = np.nan
        elif rules.heatmap_basis == "mean":
            vals[k] = t.counts.loc[coh, k].sum() / t.denom.loc[coh].sum()
        else:
            vals[k] = rate.loc[coh, k].max()
    baseline = pd.Series(vals, name="baseline", dtype=float)
    baseline.index.name = "mis"

    recent = [m for m in recent_12(base).months() if m in rate.index]
    for k in KS:
        highlight.loc[recent, k] = (rate.loc[recent, k] > baseline[k]).to_numpy()
    return Heatmap(rate, baseline, highlight)
