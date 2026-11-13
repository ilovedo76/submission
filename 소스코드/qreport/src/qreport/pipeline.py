"""analyze(): files -> dataset -> metrics -> alerts, verdict and page-1 headline."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import pandas as pd

from qreport.ingest.sheets import ROLE_KO, RawInput, SheetReport, read_workbooks
from qreport.ingest.validate import Dataset, build_dataset
from qreport.metrics.cohort import MisTable, mis_table, pooled_6mis
from qreport.metrics.heatmap import heatmap
from qreport.metrics.pchart import p_chart
from qreport.metrics.surge import type_surge
from qreport.metrics.weibull import fit_weibull, forecast_range
from qreport.metrics.yoy import yoy
from qreport.periods import has_prior, prior_12, recent_12, weibull_segments
from qreport.rules.engine import Alert, Metrics, evaluate, verdict
from qreport.ruleset import RuleSet


class InputError(ValueError):
    """Uploaded files cannot be analysed as given; the message is Korean and shown as is."""


def check_files(files: list[tuple[str, bytes]]) -> None:
    seen: dict[str, str] = {}
    for name, data in files:
        h = hashlib.sha256(data).hexdigest()
        if h in seen:
            raise InputError(f"같은 파일이 두 번 올라왔습니다: {seen[h]}, {name}. 하나를 빼고 다시 올려 주세요.")
        seen[h] = name


def check_input(ri: RawInput) -> None:
    if ri.overlaps:
        raise InputError("여러 시트의 데이터가 겹쳐 합산할 수 없습니다. 겹치는 시트를 빼고 다시 올려 주세요.\n"
                         + "\n".join(f"- {o}" for o in ri.overlaps))
    found = {r.role for r in ri.reports}
    missing = [ROLE_KO[r] for r in ("defects", "production", "sales") if r not in found]
    if missing:
        detail = "\n".join(f"- {r.file} / {r.sheet}: {r.reason}" for r in ri.reports)
        raise InputError(f"{', '.join(missing)} 시트를 찾지 못했습니다. 올린 파일에 포함되어 있는지 "
                         f"확인해 주세요.\n시트별 판별 결과:\n{detail}")


@dataclass
class Headline:
    level: float                    # recent-12 market defect rate
    change_pct: float | None        # (p1/p0 - 1) x 100 vs the prior 12 months
    pvalue: float | None
    unstable_months: int            # overall test-1 months among the last 6 production months
    top_type: str
    top_type_share: float
    forecast: tuple | None
    cost: tuple | None


@dataclass
class AnalysisResult:
    dataset: Dataset
    base: pd.Period
    rules: RuleSet
    metrics: Metrics
    alerts: list[Alert]
    verdict: str
    headline: Headline
    input_hash: str
    reports: list[SheetReport]


def input_hash(files: list[tuple[str, bytes]]) -> str:
    h = hashlib.sha256()
    for _, data in sorted(files, key=lambda f: f[0]):
        h.update(data)
    return h.hexdigest()


def _market(ds: Dataset, window, model: str | None = None) -> float:
    s = ds.sales if model is None else ds.sales[ds.sales["model"] == model]
    d = ds.analyzable()
    if model is not None:
        d = d[d["model"] == model]
    n = int(s.loc[window.mask(s["month"]), "qty"].sum())
    x = int(window.mask(d["occur_month"]).sum())
    return x / n if n > 0 else float("nan")


def compute_metrics(ds: Dataset, base: pd.Period, rules: RuleSet) -> Metrics:
    models = ds.models
    seg = weibull_segments(base, rules.seg_months)
    r12, p12 = recent_12(base), prior_12(base)
    prior_ok = has_prior(ds.first_month, base)

    level = {}
    if prior_ok:
        for mdl in models:
            _, _, p0 = pooled_6mis(ds, base, p12, mdl)
            _, _, p1 = pooled_6mis(ds, base, r12, mdl)
            ratio = p1 / p0 if p0 and p0 > 0 and not math.isnan(p0) else float("nan")
            level[mdl] = (p0, p1, ratio)

    market_recent = {"전체": _market(ds, r12)}
    market_recent.update({mdl: _market(ds, r12, mdl) for mdl in models})

    return Metrics(
        pchart_total=p_chart(ds, base, rules),
        pchart_by_model={mdl: p_chart(ds, base, rules, mdl) for mdl in models},
        mis_total=mis_table(ds, base),
        mis_by_model={mdl: mis_table(ds, base, mdl) for mdl in models},
        heatmap=heatmap(ds, base, rules),
        surge=type_surge(ds, base, rules),
        yoy_total=yoy(ds, base, rules, "total"),
        yoy_model=yoy(ds, base, rules, "model"),
        yoy_type=yoy(ds, base, rules, "type"),
        weibull={k: fit_weibull(ds, base, w, label=k) for k, w in seg.items()},
        weibull_by_model={mdl: fit_weibull(ds, base, seg["RECENT6"], mdl, label=mdl)
                          for mdl in models},
        forecast=forecast_range(ds, base, rules),
        level=level,
        base=base,
        windows={**seg, "RECENT12": r12, "PRIOR12": p12},
        market_recent=market_recent,
    )


def headline(ds: Dataset, base: pd.Period, m: Metrics, as_cost: float | None) -> Headline:
    change = pv = None
    if not m.yoy_total.empty:
        r = m.yoy_total.iloc[0]
        if r["p0"] > 0 and math.isfinite(r["p1"]):
            change = float((r["p1"] / r["p0"] - 1) * 100)
        if math.isfinite(r["pvalue"]):
            pv = float(r["pvalue"])
    last6 = {base - i for i in range(6)}
    unstable = sum(1 for x in m.pchart_total.test1 if x in last6)

    d = ds.analyzable()
    d = d[recent_12(base).mask(d["occur_month"])]
    counts = d.groupby("defect_type").size().sort_values(ascending=False, kind="stable")
    top, share = ("", 0.0) if counts.empty else (str(counts.index[0]),
                                                  float(counts.iloc[0] / counts.sum()))
    fc = m.forecast
    cost = (fc[0] * as_cost, fc[1] * as_cost) if fc is not None and as_cost is not None else None
    return Headline(level=float(m.market_recent["전체"]), change_pct=change, pvalue=pv,
                    unstable_months=unstable, top_type=top, top_type_share=share,
                    forecast=fc, cost=cost)


def analyze(files: list[tuple[str, bytes]], base: pd.Period | None, rules: RuleSet,
            dictionary: dict, as_cost: float | None = None, overrides: dict | None = None,
            targets: dict[str, float] | None = None) -> AnalysisResult | RawInput:
    """overrides = {"roles": {(file, sheet): role}, "columns": {(file, sheet): {col: field}}}
    (passed to read_workbooks). Returns the RawInput unchanged when columns need mapping;
    raises InputError (Korean message) for duplicate/overlapping files or a missing sheet role."""
    ov = overrides or {}
    check_files(files)
    ri = read_workbooks(files, dictionary, role_overrides=ov.get("roles"),
                        column_overrides=ov.get("columns"))
    if ri.needs_mapping:
        return ri
    check_input(ri)
    ds = build_dataset(ri)
    base = ds.last_occur_month if base is None else pd.Period(base, "M")
    m = compute_metrics(ds, base, rules)
    alerts = evaluate(m, rules, targets)
    return AnalysisResult(dataset=ds, base=base, rules=rules, metrics=m, alerts=alerts,
                          verdict=verdict(alerts), headline=headline(ds, base, m, as_cost),
                          input_hash=input_hash(files), reports=ri.reports)
