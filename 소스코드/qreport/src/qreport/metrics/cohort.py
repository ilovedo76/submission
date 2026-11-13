"""Observation maturity, k-MIS tables and pooled 6MIS."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from qreport.ingest.validate import Dataset
from qreport.periods import Window

KS = list(range(1, 7))


def observable_mis(prod_month: pd.Period, base: pd.Period) -> int:
    """Months of use observable by base for a cohort, capped at 6 (0 if produced after base)."""
    if prod_month > base:
        return 0
    return min(6, (base - prod_month).n + 1)


@dataclass
class MisTable:
    counts: pd.DataFrame
    denom: pd.Series
    rate: pd.DataFrame


def _production(ds: Dataset, model: str | None) -> pd.Series:
    p = ds.production if model is None else ds.production[ds.production["model"] == model]
    return p.groupby("month")["qty"].sum()


def mis_table(ds: Dataset, base: pd.Period, model: str | None = None,
              cumulative: bool = True) -> MisTable:
    prod = _production(ds, model)
    prod = prod[(prod.index <= base) & (prod > 0)].sort_index()
    idx = pd.PeriodIndex(prod.index, freq="M", name="prod_month")
    d = ds.analyzable()
    if model is not None:
        d = d[d["model"] == model]
    d = d[(d["occur_month"] <= base) & d["prod_month"].isin(idx)]
    use = d["use_months"].astype(int)

    counts = pd.DataFrame(0.0, index=idx, columns=KS)
    for k in KS:
        sel = d[use <= k] if cumulative else d[use == k]
        c = sel.groupby("prod_month").size().reindex(idx, fill_value=0)
        counts[k] = c.to_numpy(dtype=float)
    obs = np.array([observable_mis(p, base) for p in idx])
    mask = np.array(KS)[None, :] > obs[:, None]
    counts = counts.mask(mask)
    denom = pd.Series(prod.to_numpy(), index=idx, name="production")
    rate = counts.div(denom, axis=0)
    return MisTable(counts=counts, denom=denom, rate=rate)


def pooled_6mis(ds: Dataset, base: pd.Period, window: Window,
                model: str | None = None) -> tuple[int, int, float]:
    prod = _production(ds, model)
    cohorts = [m for m in window.months()
               if observable_mis(m, base) == 6 and prod.get(m, 0) > 0]
    d = ds.analyzable()
    if model is not None:
        d = d[d["model"] == model]
    d = d[d["prod_month"].isin(cohorts) & (d["use_months"] <= 6) & (d["occur_month"] <= base)]
    n = int(len(d))
    q = int(sum(prod[m] for m in cohorts))
    return n, q, (n / q if q else float("nan"))
