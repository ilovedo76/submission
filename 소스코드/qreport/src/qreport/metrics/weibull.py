"""Interval-censored + right-censored 2-parameter Weibull MLE on MIS cohorts.

Each cohort c (production n_c, observable MIS M_c, failures d_ck in use month k):
    LL = sum_c [ sum_{k<=M_c} d_ck log(F(k) - F(k-1)) + (n_c - sum_k d_ck) log(1 - F(M_c)) ]
Fitted in (log beta, log eta); the beta CI comes from a central-difference Hessian.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from qreport.ingest.validate import Dataset
from qreport.metrics.cohort import KS, MisTable, mis_table, observable_mis
from qreport.periods import Window, weibull_segments
from qreport.ruleset import RuleSet

MIN_FAILURES = 5
Z95 = 1.959963984540054
# Search box in (log beta, log eta). A beta optimum on its edge means the likelihood has no interior
# maximum (e.g. every failure in month 1 drives beta -> 0, eta -> inf): the fit is degenerate.
LOG_BOUNDS = ((np.log(0.05), np.log(20.0)), (np.log(1e-3), np.log(1e60)))
Kind = Literal["초기 고장", "일정 고장", "마모 고장"]


@dataclass
class WeibullFit:
    beta: float
    eta: float
    beta_lo: float
    beta_hi: float
    n_units: int
    n_fail: int
    label: str

    def F(self, t: float) -> float:
        with np.errstate(over="ignore", under="ignore"):
            return float(-np.expm1(-((t / self.eta) ** self.beta)))

    def kind(self) -> Kind:
        if self.beta_hi < 1:
            return "초기 고장"
        if self.beta_lo > 1:
            return "마모 고장"
        return "일정 고장"


def _arrays(table: MisTable) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(production, observed MIS, failures per use month with NaN -> 0) per cohort."""
    c = table.counts.to_numpy(dtype=float)
    obs = (~np.isnan(c)).sum(axis=1)
    keep = obs > 0
    return (table.denom.to_numpy(dtype=float)[keep], obs[keep], np.nan_to_num(c[keep]))


def _window_table(ds: Dataset, base: pd.Period, window: Window, model: str | None) -> tuple:
    t = mis_table(ds, base, model=model, cumulative=False)
    sel = [p for p in t.counts.index if window.contains(p)]
    return _arrays(MisTable(counts=t.counts.loc[sel], denom=t.denom.loc[sel], rate=t.rate.loc[sel]))


def _neg_ll(x, n, obs, d) -> float:
    t = np.arange(0, len(KS) + 1, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore", under="ignore"):
        beta, eta = np.exp(x[0]), np.exp(x[1])
        H = (t / eta) ** beta                               # cumulative hazard, H(0) = 0
        # log(F(k) - F(k-1)) = -H(k-1) + log(1 - exp(-(H(k) - H(k-1))))
        log_p = -H[:-1] + np.log(-np.expm1(-np.diff(H)))
        fail_ll = np.where(d > 0, d * log_p[None, :], 0.0).sum()
        surv_ll = ((n - d.sum(axis=1)) * -H[obs]).sum()
    v = -(fail_ll + surv_ll)
    return float(v) if np.isfinite(v) else 1e300


def _hessian(f, x, h=1e-4) -> np.ndarray:
    k = len(x)
    H = np.empty((k, k))
    e = np.eye(k) * h
    for i in range(k):
        for j in range(i, k):
            v = (f(x + e[i] + e[j]) - f(x + e[i] - e[j])
                 - f(x - e[i] + e[j]) + f(x - e[i] - e[j])) / (4 * h * h)
            H[i, j] = H[j, i] = v
    return H


def _fit_arrays(n, obs, d, label: str) -> WeibullFit | None:
    n_fail = int(d.sum())
    if n_fail < MIN_FAILURES:
        return None
    eta0 = float((n * obs).sum() / n_fail)                  # exponential (beta=1) MLE
    args = (n, obs, d)
    x0 = np.clip([0.0, np.log(eta0)], *np.array(LOG_BOUNDS).T)
    res = minimize(_neg_ll, x0=x0, args=args, method="L-BFGS-B", bounds=LOG_BOUNDS)
    if not res.success:
        return None
    x = res.x
    lo_b, hi_b = LOG_BOUNDS[0]       # eta is boxed only for numerical safety and never binds
    if min(abs(x[0] - lo_b), abs(x[0] - hi_b)) < 1e-3:
        return None
    H = _hessian(lambda y: _neg_ll(y, *args), x)
    if not np.all(np.isfinite(H)):
        return None
    try:
        var = float(np.linalg.inv(H)[0, 0])
    except np.linalg.LinAlgError:
        return None
    if not np.isfinite(var) or var <= 0:
        return None
    # A beta CI reaching past the search box means beta is not identified by the data (e.g. a
    # single cohort observed for one month: any beta fits), so the CI would be meaningless.
    se = np.sqrt(var)
    if x[0] - Z95 * se < lo_b or x[0] + Z95 * se > hi_b:
        return None
    with np.errstate(over="ignore"):
        vals = np.exp([x[0], x[1], x[0] - Z95 * se, x[0] + Z95 * se])
    if not np.all(np.isfinite(vals)):
        return None
    beta, eta, lo, hi = (float(v) for v in vals)
    return WeibullFit(beta=beta, eta=eta, beta_lo=lo, beta_hi=hi,
                      n_units=int(n.sum()), n_fail=n_fail, label=label)


def fit_from_table(table: MisTable, label: str = "") -> WeibullFit | None:
    """Fit on a non-cumulative MIS table (NaN = not yet observable).

    None when fewer than 5 failures or the fit is degenerate (optimizer failure, optimum on the
    beta edge of LOG_BOUNDS or beta CI beyond it, singular or non-positive-definite Hessian, non-finite beta/CI).
    """
    return _fit_arrays(*_arrays(table), label)


def fit_weibull(ds: Dataset, base: pd.Period, window: Window, model: str | None = None,
                label: str = "") -> WeibullFit | None:
    """Fit cohorts produced in `window`; None when fewer than 5 failures or degenerate."""
    return _fit_arrays(*_window_table(ds, base, window, model), label)


def _remaining(fit: WeibullFit, surv: float, m: int) -> float:
    """Survivors x conditional probability of failing in (m, 6]; 0 if 1 - F(m) is ~0."""
    Fm = fit.F(m)
    if 1 - Fm <= 1e-12:
        return 0.0
    return surv * (fit.F(6) - Fm) / (1 - Fm)


def _forecast(ds: Dataset, base: pd.Period, pick, model: str | None = None) -> float:
    """Sum of _remaining over cohorts with 0 < m < 6; pick(prod_month) gives the fit
    to use, or None to skip that cohort."""
    t = mis_table(ds, base, model=model, cumulative=False)
    total = 0.0
    for pm in t.counts.index:
        m = observable_mis(pm, base)
        if not 0 < m < 6:
            continue
        fit = pick(pm)
        if fit is None:
            continue
        surv = t.denom[pm] - float(t.counts.loc[pm].sum())   # NaN columns are skipped
        total += _remaining(fit, surv, m)
    return total


def forecast_additional(ds: Dataset, base: pd.Period, fit: WeibullFit, model: str | None = None,
                        window: Window | None = None) -> float:
    """Expected further claims up to 6MIS from cohorts whose observable MIS < 6.

    Each cohort contributes (n - cumulative failures) x (F(6) - F(m)) / (1 - F(m)).
    `window` optionally restricts the cohorts.
    """
    return _forecast(ds, base, lambda pm: fit if window is None or window.contains(pm) else None,
                     model)


def forecast_range(ds: Dataset, base: pd.Period, rules: RuleSet) -> tuple[float, float] | None:
    """(min, max) of two whole-data forecasts for cohorts with observable MIS < 6.

    ① current trend: each cohort is projected with the fit of its own Weibull segment
       (S1 = last 2 months, S2 = the 4 before, S3); cohorts whose segment fit is None, or that
       lie in no segment, fall back to the RECENT6 fit; ② the prior-12-month fit applied to
       all cohorts. None if the prior fit, or a needed fallback fit, is unavailable.
    """
    seg = weibull_segments(base, rules.seg_months)
    prior = fit_weibull(ds, base, seg["PRIOR"])
    if prior is None:
        return None
    recent6 = fit_weibull(ds, base, seg["RECENT6"])
    seg_fits = [(seg[k], fit_weibull(ds, base, seg[k])) for k in ("S1", "S2", "S3")]
    missing = []

    def pick(pm):
        f = next((f for w, f in seg_fits if w.contains(pm)), None) or recent6
        if f is None:
            missing.append(pm)
        return f

    current = _forecast(ds, base, pick)
    if missing:
        return None
    vals = (current, forecast_additional(ds, base, prior))
    return min(vals), max(vals)
