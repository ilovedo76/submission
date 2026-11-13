import warnings

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import minimize

from qreport.metrics.cohort import KS, MisTable
from qreport.metrics.weibull import (
    WeibullFit,
    _neg_ll,
    _window_table,
    fit_from_table,
    fit_weibull,
    forecast_range,
)
from qreport.periods import Window, weibull_segments
from qreport.ruleset import DEFAULT_RULES
from tests.golden import BETA, BETA_MODEL, FORECAST

BASE = pd.Period("2025-12", "M")
H2_2025 = Window(pd.Period("2025-07", "M"), pd.Period("2025-12", "M"))


def P(s):
    return pd.Period(s, "M")


def _triple(f: WeibullFit):
    # Unrounded values within 0.01 of the golden (2-decimal) numbers. Comparing rounded values
    # with abs=0.01 would fail on float noise (0.48 - 0.47 > 0.01) while being looser overall.
    return (f.beta, f.beta_lo, f.beta_hi)


@pytest.mark.parametrize("seg", ["PRIOR", "S2", "S1"])
def test_overall_beta_golden(company_ds, seg):
    w = weibull_segments(BASE)[seg]
    f = fit_weibull(company_ds, BASE, w)
    assert _triple(f) == pytest.approx(BETA[seg], abs=0.01)


@pytest.mark.parametrize("model", ["2Door", "양문형", "4Door", "김치"])
def test_model_beta_golden(company_ds, model):
    f = fit_weibull(company_ds, BASE, H2_2025, model=model)
    assert _triple(f) == pytest.approx(BETA_MODEL[model], abs=0.01)


def test_kinds(company_ds):
    s1 = fit_weibull(company_ds, BASE, weibull_segments(BASE)["S1"])
    assert s1.kind() == "초기 고장"
    kimchi = fit_weibull(company_ds, BASE, H2_2025, model="김치")
    assert kimchi.kind() == "일정 고장"
    wear = WeibullFit(beta=2.0, eta=10.0, beta_lo=1.5, beta_hi=2.6, n_units=1, n_fail=1, label="")
    assert wear.kind() == "마모 고장"


def test_forecast_range(company_ds):
    lo, hi = forecast_range(company_ds, BASE, DEFAULT_RULES)
    assert round(lo) == pytest.approx(FORECAST[0], abs=2)
    assert round(hi) == pytest.approx(FORECAST[1], abs=2)


def _synthetic_table(beta, eta, n=200_000, n_cohorts=12):
    """Expected (rounded) monthly failures from a known Weibull, with staggered observation."""
    F = lambda t: 1 - np.exp(-((t / eta) ** beta))
    idx = pd.period_range("2025-01", periods=n_cohorts, freq="M", name="prod_month")
    counts = pd.DataFrame(np.nan, index=idx, columns=KS)
    for i, pm in enumerate(idx):
        obs = min(6, n_cohorts - i)
        for k in range(1, obs + 1):
            counts.loc[pm, k] = round(n * (F(k) - F(k - 1)))
    denom = pd.Series(float(n), index=idx)
    return MisTable(counts=counts, denom=denom, rate=counts.div(denom, axis=0))


def test_recovers_known_beta():
    f = fit_from_table(_synthetic_table(0.5, 40.0))
    assert f.beta_lo <= 0.5 <= f.beta_hi
    assert f.beta == pytest.approx(0.5, abs=0.02)
    assert f.eta == pytest.approx(40.0, rel=0.1)


def test_too_few_failures_returns_none():
    t = _synthetic_table(1.0, 1e6, n=1000)
    assert np.nansum(t.counts.to_numpy()) < 5
    assert fit_from_table(t) is None


@pytest.mark.parametrize("seg,model", [("PRIOR", None), ("S2", None), ("S1", None),
                                       ("H2", "2Door"), ("H2", "김치")])
def test_independent_check(company_ds, seg, model):
    w = H2_2025 if seg == "H2" else weibull_segments(BASE)[seg]
    f = fit_weibull(company_ds, BASE, w, model=model)
    tab = _window_table(company_ds, BASE, w, model)
    res = minimize(_neg_ll, x0=[0.0, np.log(100.0)], args=tab, method="Nelder-Mead",
                   options={"xatol": 1e-10, "fatol": 1e-12, "maxiter": 40000})
    assert round(float(np.exp(res.x[0])), 2) == round(f.beta, 2)


def _table(rows, n=10_000.0):
    """rows: per-cohort failure lists (len = observed MIS)."""
    idx = pd.period_range("2025-01", periods=len(rows), freq="M", name="prod_month")
    counts = pd.DataFrame(np.nan, index=idx, columns=KS)
    for pm, r in zip(idx, rows):
        counts.loc[pm, 1:len(r)] = r
    denom = pd.Series(n, index=idx)
    return MisTable(counts=counts, denom=denom, rate=counts.div(denom, axis=0))


@pytest.mark.parametrize("rows", [
    [[5]],                                  # one cohort, one month: beta unidentifiable
    [[3, 0, 0], [2, 0], [4, 0, 0, 0]],      # all failures at k=1: beta -> 0, eta -> inf
], ids=["single-month", "all-at-k1"])
def test_degenerate_fit_returns_none_without_warnings(rows):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert fit_from_table(_table(rows)) is None


def test_singular_hessian_returns_none(monkeypatch):
    import qreport.metrics.weibull as wb
    monkeypatch.setattr(wb, "_hessian", lambda f, x, h=1e-4: np.zeros((2, 2)))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert fit_from_table(_synthetic_table(0.5, 40.0)) is None


def test_kind_never_sees_nan_ci():
    f = fit_from_table(_synthetic_table(0.5, 40.0))
    assert np.isfinite([f.beta, f.beta_lo, f.beta_hi]).all()


def test_remaining_guard_when_cdf_saturates():
    from qreport.metrics.weibull import _remaining
    sat = WeibullFit(beta=5.0, eta=0.01, beta_lo=4.0, beta_hi=6.0, n_units=1, n_fail=1, label="")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert _remaining(sat, 100.0, 2) == 0.0


def test_forecast_uncovered_cohorts_use_recent6(company_ds):
    from qreport.metrics.weibull import forecast_additional
    rules = DEFAULT_RULES.replace(seg_months=(1, 1, 1))     # S1..S3 = 25.12, 25.11, 25.10
    seg = weibull_segments(BASE, (1, 1, 1))
    recent6 = fit_weibull(company_ds, BASE, seg["RECENT6"])
    expected = forecast_additional(company_ds, BASE, recent6, window=Window(P("2025-08"), P("2025-09")))
    for k in ("S1", "S2", "S3"):
        f = fit_weibull(company_ds, BASE, seg[k]) or recent6
        expected += forecast_additional(company_ds, BASE, f, window=seg[k])
    prior = forecast_additional(company_ds, BASE, fit_weibull(company_ds, BASE, seg["PRIOR"]))
    assert forecast_range(company_ds, BASE, rules) == pytest.approx(tuple(sorted((expected, prior))))


@pytest.mark.parametrize("p,beta", [(0.0015, 0.2), (0.0005, 0.2)])
def test_strong_infant_mortality_with_huge_eta(p, beta):
    eta = 6.0 / (-np.log1p(-p)) ** (1 / beta)               # F(6) = p  ->  eta ~ 1e15..1e17
    assert eta > 1e14
    f = fit_from_table(_synthetic_table(beta, eta, n=2_000_000))
    assert f is not None
    assert f.beta_lo <= beta <= f.beta_hi
    assert f.kind() == "초기 고장"
