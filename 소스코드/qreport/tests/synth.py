"""Synthetic company-like workbook generator shared by many tests.

``make_workbook(months, start, seed, anomalies)`` returns xlsx bytes with the
same four sheets as the company file: Graph (title + chart, ignorable),
RawData (one row per defect), 생산량 / 판매량 (wide, model x YYMM, Total row).

Model: per model and month, production is ~5,000-10,000.  Each production
cohort yields defects Poisson-distributed per usage month u (1..MAXU) with
mean n * pmf(u) of a population Weibull F(t) = 1 - exp(-H6 * (t/6)^beta) with
H6 = -log(1 - 0.15%), so the cumulative 6MIS rate is 0.15% for any beta and
beta=1 is a truly constant per-unit hazard (no "susceptible fraction" mixture,
which would make the fitted beta drift below 1).  occurrence month = prod_month + u - 1, kept
only up to the last data month.  Clean data: beta=1, constant rate.

Anomaly keys:
  "1mis_spike" : last 2 production months, usage-month-1 defects x4
  "run_up"     : last 10 production months, level ramps x1.8 -> x2.2 (below UCL in most months)
  "infant"     : last 6 production months use beta=0.4 (front-loaded failures)
  "level_rise" : first model, last 12 production months, 6MIS level x2, beta=1
  "type_surge" : one defect type in the last occurrence month at >=3x its recent
                 average and >=15 rows
  "yoy_worse"  : second year (production months index >= 12) overall level x1.6
"""
from __future__ import annotations

import io
import math

import numpy as np
import openpyxl
import pandas as pd
from openpyxl.chart import BarChart, Reference

MODELS = ["4Door", "2Door", "양문형", "김치"]
TYPES = ["소음", "냉각불량", "누수", "도어불량", "전기"]
TYPE_P = [0.30, 0.25, 0.20, 0.15, 0.10]
BASE_6MIS = 0.0015
MAXU = 24
SURGE_TYPE = "누수"


def _pmf(beta: float) -> np.ndarray:
    u = np.arange(0, MAXU + 1, dtype=float)
    cdf = 1 - np.exp(-(-np.log1p(-BASE_6MIS)) * (u / 6) ** beta)
    return np.diff(cdf)  # index 0 -> u=1


def make_workbook(months: int = 24, start: str = "2024-01", seed: int = 0,
                  anomalies: set[str] = frozenset()) -> bytes:
    an = set(anomalies)
    rng = np.random.default_rng(seed)
    periods = pd.period_range(start, periods=months, freq="M")
    n_models = len(MODELS)

    # production / sales
    scale = np.array([1.0, 0.9, 0.8, 0.7])[:, None]
    season = 1 + 0.15 * np.sin(np.arange(months) / 3.0)[None, :]
    prod = (7500 * scale * season * rng.uniform(0.9, 1.1, (n_models, months))).round().astype(int)
    sales = (prod * rng.uniform(0.88, 0.99, prod.shape)).round().astype(int)

    # expected defects per (model, prod_month, u)
    mean = np.zeros((n_models, months, MAXU))
    for mi in range(n_models):
        for p in range(months):
            beta = 0.4 if ("infant" in an and p >= months - 6) else 1.0
            lvl = 1.0
            if "run_up" in an and p >= months - 10:
                lvl *= 1.8 + 0.4 * (p - (months - 10)) / 9
            if "level_rise" in an and mi == 0 and p >= months - 12:
                lvl *= 2.0
            if "yoy_worse" in an and p >= 12:
                lvl *= 1.6
            m = prod[mi, p] * _pmf(beta)[:MAXU] * lvl
            if "1mis_spike" in an and p >= months - 2:
                m[0] *= 4
            mean[mi, p] = m
    counts = rng.poisson(mean)

    rows = []
    for mi, p, u in zip(*np.nonzero(counts)):
        occ = p + u  # u index 0 == usage month 1
        if occ > months - 1:
            continue
        for _ in range(counts[mi, p, u]):
            rows.append((mi, p, occ, u + 1))
    arr = np.array(rows, dtype=int).reshape(-1, 4)
    types = list(rng.choice(TYPES, size=len(arr), p=TYPE_P))

    if "type_surge" in an:
        last = months - 1
        in_type = [i for i, t in enumerate(types) if t == SURGE_TYPE]
        prev = [np.sum(arr[in_type, 2] == last - k) for k in (1, 2, 3)] if in_type else [0, 0, 0]
        cur = int(np.sum(arr[in_type, 2] == last)) if in_type else 0
        target = max(15, math.ceil(3.5 * (np.mean(prev) if prev else 0)))
        extra = max(target - cur, 0)
        add = []
        for _ in range(extra):
            u = int(rng.integers(1, 7))
            add.append((int(rng.integers(0, n_models)), last - u + 1, last, u))
        if add:
            arr = np.vstack([arr, np.array(add, dtype=int)])
            types += [SURGE_TYPE] * len(add)

    order = rng.permutation(len(arr))
    arr = arr[order]
    types = [types[i] for i in order]

    wb = openpyxl.Workbook()
    g = wb.active
    g.title = "Graph"
    g["B3"] = "불량 : 요약"
    g["B4"] = "행 레이블"
    for j, p in enumerate(periods[: min(months, 12)]):
        g.cell(row=4, column=3 + j, value=_yymm(p))
        g.cell(row=5, column=3 + j, value=int(prod[:, j].sum()))
    ch = BarChart()
    ch.add_data(Reference(g, min_col=3, max_col=2 + min(months, 12), min_row=5), from_rows=True)
    g.add_chart(ch, "B8")

    raw = wb.create_sheet("RawData")
    raw.append(["냉장고", "생산월", "발생월", "불량 종류", "사용개월"])
    for (mi, p, o, u), t in zip(arr.tolist(), types):
        raw.append([MODELS[mi], _yymm(periods[p]), _yymm(periods[o]), t, u])

    for name, mat in (("생산량", prod), ("판매량", sales)):
        ws = wb.create_sheet(name)
        ws.append(["모델"] + [_yymm(p) for p in periods])
        for mi in range(n_models):
            ws.append([MODELS[mi]] + mat[mi].tolist())
        ws.append(["Total"] + mat.sum(axis=0).tolist())

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _yymm(p: pd.Period) -> int:
    return (p.year % 100) * 100 + p.month
