"""Rule engine (R1-R8), alert grouping and overall verdict (spec 4.4, 4.7-2/6)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from qreport.metrics.weibull import WeibullFit
from qreport.ruleset import RuleSet

Severity = Literal["높음", "중간", "낮음"]
_SEV_ORDER = {"높음": 0, "중간": 1, "낮음": 2}


@dataclass
class Alert:
    rule: str            # "R1".."R8"
    severity: Severity
    target: str          # "전체" or model/type name(s), joined by ", "
    period: str
    value: float
    threshold: float
    detail: dict
    chart_key: str


@dataclass
class Metrics:
    pchart_total: object                 # PChart
    pchart_by_model: dict                # model -> PChart
    mis_total: object                    # MisTable (cumulative)
    mis_by_model: dict                   # model -> MisTable
    heatmap: object                      # Heatmap (overall)
    surge: pd.DataFrame                  # type_surge, full 12-month history
    yoy_total: pd.DataFrame
    yoy_model: pd.DataFrame
    yoy_type: pd.DataFrame
    weibull: dict                        # segment -> WeibullFit | None (S1,S2,S3,PRIOR,RECENT6)
    weibull_by_model: dict               # model -> RECENT6 WeibullFit | None
    forecast: tuple | None
    level: dict                          # model -> (prior 6MIS, recent 6MIS, ratio)
    base: pd.Period | None = None
    windows: dict = field(default_factory=dict)       # name -> Window (segments, recent/prior 12)
    market_recent: dict = field(default_factory=dict)  # "전체"/model -> recent-12 market rate


def _ym(p) -> str:
    return pd.Period(p, "M").strftime("%Y-%m")


def _span(w) -> str:
    return f"{_ym(w.start)}~{_ym(w.end)}"


def _ratio(a: Alert) -> float:
    try:
        r = a.value / a.threshold
    except ZeroDivisionError:
        return math.inf if a.value > 0 else 0.0
    return r if math.isfinite(r) else (math.inf if r == math.inf else -math.inf)


def _point(pc, month) -> dict:
    row = pc.df.loc[pc.df["prod_month"] == month].iloc[0]
    return {"month": _ym(month), "p": float(row["p"]), "ucl": float(row["ucl"]),
            "x": int(row["x"]), "n": int(row["n"])}


def _consecutive(months: list) -> list[list]:
    runs: list[list] = []
    for m in sorted(months):
        if runs and (m - runs[-1][-1]).n == 1:
            runs[-1].append(m)
        else:
            runs.append([m])
    return runs


RECENT_MONTHS = 6   # R1/R2 look only at the last 6 production months <= base


def _recent(m: Metrics, months: list) -> list:
    if m.base is None:
        return list(months)
    return [x for x in months if m.base - (RECENT_MONTHS - 1) <= x <= m.base]


def _pt_ratio(pt: dict) -> float:
    return pt["p"] / pt["ucl"] if pt["ucl"] > 0 else math.inf


def _r1(m: Metrics) -> list[Alert]:
    out = []
    pc = m.pchart_total
    recent = sorted(_recent(m, pc.test1))
    if recent:                                   # ONE alert over all recent breach months
        pts = [_point(pc, x) for x in recent]
        last = pts[-1]
        period = last["month"] if len(pts) == 1 else f"{pts[0]['month']}~{last['month']}"
        out.append(Alert("R1", "높음", "전체", period, last["p"], last["ucl"],
                         {"months": [p["month"] for p in pts], "points": pts,
                          "consecutive": len(_consecutive(recent)[-1])}, "pchart"))
    rows = [{"model": model, **_point(mpc, month)}
            for model, mpc in m.pchart_by_model.items() for month in _recent(m, mpc.test1)]
    if rows:
        rows.sort(key=_pt_ratio, reverse=True)
        names = list(dict.fromkeys(r["model"] for r in rows))      # by max ratio, desc
        months = sorted({r["month"] for r in rows})
        top = rows[0]
        period = months[0] if len(months) == 1 else f"{months[0]}~{months[-1]}"
        out.append(Alert("R1", "높음", ", ".join(names), period, top["p"], top["ucl"],
                         {"models": names, "months": months, "rows": rows},
                         f"pchart_{names[0]}"))
    return out


def _r2(m: Metrics, rules: RuleSet) -> list[Alert]:
    """Runs above the centre line whose run-rule month lies in the recent window."""
    out = []
    charts = [("전체", m.pchart_total, "pchart")] + [
        (k, v, f"pchart_{k}") for k, v in m.pchart_by_model.items()]
    for target, pc, key in charts:
        above = pc.df.set_index("prod_month")["above_center"]
        hits = [x for x in _recent(m, pc.run9) if bool(above[x])]
        for run in _consecutive(hits):
            end = run[-1]
            length = rules.run_length
            while end - length in above.index and bool(above[end - length]):
                length += 1                                     # full run length up to `end`
            start = end - (length - 1)
            out.append(Alert("R2", "중간", target, f"{_ym(start)}~{_ym(end)}", float(length),
                             float(rules.run_length),
                             {"side": "위", "length": length, "start": _ym(start),
                              "end": _ym(end)}, key))
    return out


def _fit_detail(f: WeibullFit) -> dict:
    return {"beta": f.beta, "beta_lo": f.beta_lo, "beta_hi": f.beta_hi, "eta": f.eta,
            "n_fail": f.n_fail, "n_units": f.n_units}


def _r3(m: Metrics) -> list[Alert]:
    out = []
    infant = [k for k in ("S2", "S1") if m.weibull.get(k) is not None
              and m.weibull[k].beta_hi < 1]
    if infant:
        k = infant[-1]                                  # most recent such segment
        f = m.weibull[k]
        w = m.windows.get(k)
        out.append(Alert("R3", "높음", "전체", _span(w) if w else k, f.beta, 1.0,
                         {"segment": k, **_fit_detail(f),
                          "segments": {s: _fit_detail(m.weibull[s]) for s in infant}},
                         "weibull"))
    models = {k: f for k, f in m.weibull_by_model.items() if f is not None and f.beta_hi < 1}
    if models:
        names = sorted(models, key=lambda k: models[k].beta)
        w = m.windows.get("RECENT6")
        out.append(Alert("R3", "높음", ", ".join(names), _span(w) if w else "RECENT6",
                         models[names[0]].beta, 1.0,
                         {"segment": "RECENT6", "models": {k: _fit_detail(models[k]) for k in names}},
                         "weibull"))
    return out


def _r4(m: Metrics, rules: RuleSet) -> list[Alert]:
    out = []
    for model, (p0, p1, ratio) in m.level.items():
        f = m.weibull_by_model.get(model)
        if f is None or not (isinstance(ratio, float) and math.isfinite(ratio)):
            continue
        if ratio >= rules.level_rise_ratio and f.beta_lo <= 1 <= f.beta_hi:
            w = m.windows.get("RECENT12")
            out.append(Alert("R4", "높음", model, _span(w) if w else "", ratio,
                             float(rules.level_rise_ratio),
                             {"prior_6mis": p0, "recent_6mis": p1, **_fit_detail(f)},
                             "mis_curve"))
    return out


def _r5(m: Metrics, rules: RuleSet) -> list[Alert]:
    s = m.surge
    if s is None or s.empty or m.base is None:
        return []
    rows = []
    for _, r in s[s["month"] == m.base].iterrows():
        thr = max(r["avg6"] * rules.surge_ratio, float(rules.surge_min))
        rows.append({"type": str(r["defect_type"]), "count": int(r["count"]),
                     "avg6": float(r["avg6"]), "ratio": float(r["ratio"]), "threshold": float(thr)})
    if not rows:
        return []
    rows.sort(key=lambda r: r["count"] / r["threshold"], reverse=True)
    top = rows[0]
    return [Alert("R5", "중간", ", ".join(r["type"] for r in rows), _ym(m.base),
                  float(top["count"]), top["threshold"], {"types": rows}, "pareto")]


def _r6(m: Metrics) -> list[Alert]:
    """Up to 3 alerts: overall, worse models (joined), worse types (joined)."""
    out = []
    w = m.windows.get("RECENT12")
    for kind, df in (("total", m.yoy_total), ("model", m.yoy_model), ("type", m.yoy_type)):
        if df is None or df.empty:
            continue
        rows = []
        for _, r in df[df["worse"].astype(bool)].iterrows():
            if not (math.isfinite(r["ratio"]) and r["p0"] > 0):
                continue
            rows.append({"group": str(r["group"]), "x1": int(r["x1"]), "n1": int(r["n1"]),
                         "p1": float(r["p1"]), "x0": int(r["x0"]), "n0": int(r["n0"]),
                         "p0": float(r["p0"]), "ratio": float(r["ratio"]), "z": float(r["z"]),
                         "pvalue": float(r["pvalue"])})
        if not rows:
            continue
        rows.sort(key=lambda r: r["ratio"], reverse=True)
        top = rows[0]
        out.append(Alert("R6", "중간", ", ".join(r["group"] for r in rows), _span(w) if w else "",
                         top["p1"], top["p0"], {"by": kind, "rows": rows}, "yoy"))
    return out


def _r7(m: Metrics) -> list[Alert]:
    hm = m.heatmap
    if hm is None or not hm.highlight.to_numpy().any():
        return []
    cells = []
    for pm in hm.highlight.index:
        for k in hm.highlight.columns:
            if hm.highlight.loc[pm, k]:
                cells.append({"month": _ym(pm), "mis": int(k), "rate": float(hm.rate.loc[pm, k]),
                              "baseline": float(hm.baseline[k])})
    top = max(cells, key=lambda c: c["rate"] / c["baseline"] if c["baseline"] > 0 else math.inf)
    months = sorted({c["month"] for c in cells})
    return [Alert("R7", "낮음", "전체", f"{months[0]}~{months[-1]}", top["rate"], top["baseline"],
                  {"cells": cells, "count": len(cells)}, "heatmap")]


def _r8(m: Metrics, targets: dict[str, float] | None) -> list[Alert]:
    if not targets:
        return []
    out = []
    w = m.windows.get("RECENT12")
    for model, tgt in targets.items():
        rate = m.market_recent.get(model)
        if tgt is None or rate is None or not math.isfinite(rate):
            continue
        if rate > tgt:
            out.append(Alert("R8", "낮음", model, _span(w) if w else "", float(rate), float(tgt),
                             {"target": float(tgt)}, "trend"))
    return out


def evaluate(m: Metrics, rules: RuleSet, targets: dict[str, float] | None = None) -> list[Alert]:
    alerts = (_r1(m) + _r2(m, rules) + _r3(m) + _r4(m, rules) + _r5(m, rules) + _r6(m)
              + _r7(m) + _r8(m, targets))
    return sorted(alerts, key=lambda a: (_SEV_ORDER[a.severity], int(a.rule[1:]),
                                         a.target != "전체", -_ratio(a)))


def verdict(alerts: list[Alert]) -> Literal["개선 필요", "주의", "정상"]:
    sev = {a.severity for a in alerts}
    if "높음" in sev:
        return "개선 필요"
    if "중간" in sev:
        return "주의"
    return "정상"
