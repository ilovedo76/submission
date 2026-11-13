"""Thin entry point from the web pages (Pyodide) into the qreport engine.

analyze_files() -> {"ok", "run", "series"}: the run summary and chart series, built only from
qreport results (analyze, comment_lines, metrics functions) - no new statistics here.
split_for_db() cuts that into shared-db documents (<= 256 KiB each); render_report() and
chart_pngs() reuse the last analysis kept in this module.

Series rows (month key "m" = "YYYY-MM", NaN/inf -> null), each {"전체": rows, "<model>": rows}:
  market          {m, defects, sales, rate, pbar, ucl}          market_control_limits
  pchart          {m, x, n, p, pbar, ucl, out, run}              metrics p-charts (out = test 1, run = run rule)
  heatmap         {m, k, rate, observed, highlight}              recent-12 cells of the report heatmap
  heatmapBaseline {k, rate}                                      prior-year baseline row ("전년 같은 MIS");
                                                                 JS reads the baseline only from here
  pareto          {type, count, cum}                             qreport.render.chart_data.pareto_table
  modelType       {model, type, recent, prior, diff}             qreport.render.chart_data.model_type_counts

Runs unchanged in desktop Python (tests) and in Pyodide. Only qreport's engine, naming and render
modules are imported (qreport.app / qreport.store are not shipped to the browser).
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import traceback
from typing import Literal

import numpy as np
import pandas as pd

from qreport.ingest.sheets import DEFAULT_DICTIONARY, RawInput
from qreport.metrics.cohort import KS, observable_mis
from qreport.metrics.heatmap import heatmap
from qreport.metrics.market import market_control_limits
from qreport.naming import base_options, doc_no_from_files
from qreport.notes import PLAN_KEYS, Notes
from qreport.periods import recent_12
from qreport.pipeline import AnalysisResult, InputError, analyze
from qreport.render.chart_data import model_type_counts, pareto_table
from qreport.render.content import cost_text, forecast_text, validation_text
from qreport.rules.comments import comment_lines, verdict_line
from qreport.ruleset import DEFAULT_RULES

RULE_VERSION = 1
RULE_HASH = hashlib.sha256(DEFAULT_RULES.to_json().encode("utf-8")).hexdigest()
KINDS = ("company", "sample", "user")
TOTAL = "전체"
SERIES_ORDER = ("market", "pchart", "heatmap", "heatmapBaseline", "pareto", "modelType")
DB_LIMIT = 256 * 1024
SCREEN_DPI = 110
FORMATS = ("docx", "pdf", "zip")
SAMPLE_LABEL = "가상 데이터 — 체험용 샘플"

# last successful analysis: {"result", "files", "kind", "pngs" (300 dpi, lazy), "screen" {model: pngs}}
_LAST: dict | None = None


# ------------------------------------------------------------------ JSON-safe values
def _ym(p) -> str:
    return pd.Period(p, "M").strftime("%Y-%m")


def _num(v):
    """numpy/pandas scalar -> int | float | bool | None (NaN/inf -> None)."""
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    f = float(v)
    return f if math.isfinite(f) else None


def _count(v) -> int | None:
    """Counts stored as floats in qreport tables (e.g. 1MIS x) -> int."""
    f = _num(v)
    return None if f is None else int(round(f))


def _pair(t) -> list | None:
    return None if t is None else [_num(t[0]), _num(t[1])]


# ------------------------------------------------------------------ run summary
def _yoy_change(row) -> tuple[float | None, float | None]:
    """(change_pct, pvalue) from one qreport yoy row - the same expressions as
    qreport.pipeline.headline applies to yoy_total.iloc[0]."""
    change = pv = None
    if row["p0"] > 0 and math.isfinite(row["p1"]):
        change = float((row["p1"] / row["p0"] - 1) * 100)
    if math.isfinite(row["pvalue"]):
        pv = float(row["pvalue"])
    return change, pv


def _model_headline(r: AnalysisResult) -> dict[str, dict]:
    """Per-model counterpart of headline.level / change_pct / pvalue: level = metrics.market_recent[model]
    (what headline.level reads for "전체"), change/p = the model's row of metrics.yoy_model (yoy by="model",
    the per-model run of the yoy_total the headline uses). No prior year -> change/p None."""
    m = r.metrics
    rows = {} if m.yoy_model.empty else {str(x["group"]): x for _, x in m.yoy_model.iterrows()}
    out = {}
    for model in r.dataset.models:
        change, pv = _yoy_change(rows[str(model)]) if str(model) in rows else (None, None)
        out[str(model)] = {"level": _num(m.market_recent.get(model)), "changePct": _num(change), "pvalue": _num(pv)}
    return out


def _base_options(first: pd.Period, last: pd.Period) -> list[str]:
    return [_ym(p) for p in base_options(first, last)]


def _run(r: AnalysisResult, files: list[tuple[str, bytes]], kind: str) -> dict:
    ds, h = r.dataset, r.headline
    return {
        "kind": kind,
        "fileNames": [name for name, _ in files],
        "inputHash": r.input_hash,
        "baseMonth": _ym(r.base),
        "baseOptions": _base_options(ds.first_month, ds.last_occur_month),
        "period": f"{_ym(ds.first_month)} ~ {_ym(r.base)}",
        "docNo": doc_no_from_files(files, r.base),
        "ruleVersion": RULE_VERSION,
        "ruleHash": RULE_HASH,
        "verdict": r.verdict,
        "verdictLine": verdict_line(r),
        "alertCount": len(r.alerts),
        "headline": {
            "level": _num(h.level), "changePct": _num(h.change_pct), "pvalue": _num(h.pvalue),
            "unstableMonths": int(h.unstable_months), "topType": h.top_type,
            "topTypeShare": _num(h.top_type_share), "forecast": _pair(h.forecast),
            "cost": _pair(h.cost),
        },
        "modelHeadline": _model_headline(r),
        "forecastText": forecast_text(r),
        "costText": cost_text(r),
        "alerts": [{"rule": a.rule, "severity": a.severity, "target": a.target, "period": a.period,
                    "value": _num(a.value), "threshold": _num(a.threshold), "chartKey": a.chart_key}
                   for a in r.alerts],
        "comments": comment_lines(r),
        "validation": validation_text(ds.summary, r.reports),
        "models": [str(m) for m in ds.models],
    }


# ------------------------------------------------------------------ series
def _market_rows(r: AnalysisResult, model: str | None) -> list[dict]:
    mr = market_control_limits(r.dataset, r.base, r.rules, model)
    return [{"m": _ym(x.month), "defects": _count(x.defects), "sales": _num(x.sales),
             "rate": _num(x.rate), "pbar": _num(x.pbar), "ucl": _num(x.ucl)}
            for x in mr.itertuples(index=False)]


def _pchart_rows(r: AnalysisResult, model: str | None) -> list[dict]:
    pc = r.metrics.pchart_total if model is None else r.metrics.pchart_by_model[model]
    t1, r9 = set(pc.test1), set(pc.run9)
    return [{"m": _ym(x.prod_month), "x": _count(x.x), "n": _count(x.n), "p": _num(x.p),
             "pbar": _num(pc.pbar), "ucl": _num(x.ucl), "out": x.prod_month in t1,
             "run": x.prod_month in r9}
            for x in pc.df.itertuples(index=False)]


def _heatmap(r: AnalysisResult, model: str | None):
    return r.metrics.heatmap if model is None else heatmap(r.dataset, r.base, r.rules, model)


def _heatmap_rows(r: AnalysisResult, hm) -> list[dict]:
    """Recent-12 production months x MIS 1..6, the cells of the report heatmap (charts._heatmap)."""
    rows = [m for m in recent_12(r.base).months() if m in hm.rate.index]
    out = []
    for pm in rows:
        obs = observable_mis(pm, r.base)
        for k in KS:
            seen = k <= obs
            out.append({"m": _ym(pm), "k": int(k), "rate": _num(hm.rate.loc[pm, k]) if seen else None,
                        "observed": seen, "highlight": bool(hm.highlight.loc[pm, k])})
    return out


def _baseline_rows(hm) -> list[dict]:
    if hm.baseline.empty:
        return []
    return [{"k": int(k), "rate": _num(hm.baseline.get(k))} for k in KS]


def _pareto_rows(r: AnalysisResult, model: str | None) -> list[dict]:
    t = pareto_table(r.dataset, r.base, model)
    return [{"type": str(i), "count": _count(c), "cum": _num(s)}
            for i, c, s in zip(t.index, t["count"], t["cum_share"])]


def _model_type_rows(r: AnalysisResult, model: str | None) -> list[dict]:
    recent, prior = model_type_counts(r.dataset, r.base, model)
    out = []
    for m in recent.index:
        for t in recent.columns:
            a, b = _count(recent.loc[m, t]), _count(prior.loc[m, t])
            out.append({"model": str(m), "type": str(t), "recent": a, "prior": b, "diff": a - b})
    return out


def _series(r: AnalysisResult) -> dict[str, dict[str, list]]:
    scopes = [(TOTAL, None)] + [(str(m), m) for m in r.dataset.models]
    out = {name: {} for name in SERIES_ORDER}
    for scope, model in scopes:
        hm = _heatmap(r, model)
        out["market"][scope] = _market_rows(r, model)
        out["pchart"][scope] = _pchart_rows(r, model)
        out["heatmap"][scope] = _heatmap_rows(r, hm)
        out["heatmapBaseline"][scope] = _baseline_rows(hm)
        out["pareto"][scope] = _pareto_rows(r, model)
        out["modelType"][scope] = _model_type_rows(r, model)
    return out


# ------------------------------------------------------------------ analyze
def _overrides(ov: dict | None) -> dict | None:
    """JSON form {"roles": [{file, sheet, role}], "columns": [{file, sheet, map: {col: field}}]}
    (or qreport's own {(file, sheet): ...} dicts) -> qreport overrides."""
    if not ov:
        return None
    roles, columns = ov.get("roles") or {}, ov.get("columns") or {}
    if not isinstance(roles, dict):
        roles = {(x["file"], x["sheet"]): x["role"] for x in roles}
    if not isinstance(columns, dict):
        cols: dict = {}
        for x in columns:
            cols.setdefault((x["file"], x["sheet"]), {}).update(dict(x["map"]))
        columns = cols
    return {"roles": roles, "columns": columns}


def _mapping(ri: RawInput) -> list[dict]:
    return [{"file": q.file, "sheet": q.sheet, "columns": list(q.columns), "missing": list(q.missing_fields)}
            for q in ri.needs_mapping]


def analyze_files(files: list[tuple[str, bytes]], base: str | None, as_cost: float | None, kind: str,
                  overrides: dict | None = None) -> dict:
    """files = [(name, xlsx bytes)], base = "YYYY-MM" or None (last occurrence month),
    as_cost = AS cost per case (None / <= 0: not given)."""
    global _LAST
    _LAST = None                      # a failed analysis never leaves an older result to render
    if kind not in KINDS:
        return {"ok": False, "error": f"알 수 없는 데이터 종류입니다: {kind!r} (company, sample, user 중 하나)"}
    try:
        cost = None if as_cost in (None, "") else float(as_cost)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"AS 처리비는 숫자로 입력해 주세요: {as_cost!r}"}
    cost = cost if cost is not None and math.isfinite(cost) and cost > 0 else None
    files = [(str(name), bytes(data)) for name, data in files]
    try:
        r = analyze(files, None if base is None else pd.Period(base, "M"), DEFAULT_RULES,
                    DEFAULT_DICTIONARY, cost, _overrides(overrides))
        if isinstance(r, RawInput):
            return {"ok": False, "needs_mapping": _mapping(r)}
        out = {"ok": True, "run": _run(r, files, kind), "series": _series(r)}
    except InputError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:   # readable message for the page; details for the console
        return {"ok": False, "error": f"분석 중 오류가 발생했습니다: {e}", "detail": traceback.format_exc()}
    _LAST = {"result": r, "files": files, "kind": kind, "pngs": None, "screen": {}}
    return out


# ------------------------------------------------------------------ db documents
def _dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _size(o) -> int:
    return len(_dumps(o).encode("utf-8"))


def run_id(run: dict) -> str:
    return f"{run['inputHash'][:16]}_{run['baseMonth']}"


def _chunk_series(data: dict[str, list], limit: int, envelope: int, name: str) -> list[dict]:
    """Greedy split of {scope: rows}: whole scopes first, then the rows of a scope that does not fit.
    `envelope` = size of the document with "data":{} (part numbers at their widest)."""
    chunks: list[dict] = []
    cur: dict[str, list] = {}
    size = envelope

    def flush():
        nonlocal cur, size
        chunks.append(cur)
        cur, size = {}, envelope

    def entry(scope: str, rows_size: int) -> int:   # [,]"scope":<rows> inside "data":{...}
        return (1 if cur else 0) + _size(scope) + 1 + rows_size

    for scope, rows in data.items():
        whole = _size(rows)
        if cur and size + entry(scope, whole) > limit and envelope + _size(scope) + 1 + whole <= limit:
            flush()                                   # the whole scope fits in a fresh document
        if size + entry(scope, whole) <= limit:
            size += entry(scope, whole)
            cur[scope] = rows
            continue
        if cur:                                       # too big even alone: split its rows
            flush()
        part: list = []
        size += entry(scope, 2)                       # "scope":[]
        cur[scope] = part
        for row in rows:
            cost = _size(row) + (1 if part else 0)
            if size + cost > limit and part:
                flush()
                part = []
                size += entry(scope, 2)
                cur[scope] = part
                cost = _size(row)
            if size + cost > limit:
                raise ValueError(f"series {name}/{scope}: one row exceeds the {limit}-byte limit")
            part.append(row)
            size += cost
    if cur or not chunks:
        chunks.append(cur)
    return chunks


def split_for_db(result: dict, limit: int = DB_LIMIT) -> list[tuple[str, dict]]:
    """[("runs/{id}", run), ("runs/{id}/series/{name}" | ".../{name}__{k}", doc), ...], each document
    <= limit bytes as compact UTF-8 JSON. A series doc is {runId, name, part, parts, data: {scope: rows}};
    the run doc gets runId and seriesParts {name: parts} so a reader knows which docs to fetch."""
    run, series = result["run"], result["series"]
    rid = run_id(run)
    names = [n for n in SERIES_ORDER if n in series] + [n for n in series if n not in SERIES_ORDER]
    docs: list[tuple[str, dict]] = []
    parts: dict[str, int] = {}
    for name in names:
        widest = {"runId": rid, "name": name, "part": 10 ** 6, "parts": 10 ** 6, "data": {}}
        chunks = _chunk_series(series[name], limit, _size(widest), name)
        parts[name] = len(chunks)
        for k, data in enumerate(chunks):
            path = f"runs/{rid}/series/{name}" if len(chunks) == 1 else f"runs/{rid}/series/{name}__{k}"
            docs.append((path, {"runId": rid, "name": name, "part": k, "parts": len(chunks), "data": data}))
    run_doc = {**run, "runId": rid, "seriesParts": parts}
    out = [(f"runs/{rid}", run_doc)] + docs
    for path, doc in out:
        if _size(doc) > limit:
            raise ValueError(f"{path}: {_size(doc)} bytes exceeds the {limit}-byte limit")
    return out


# ------------------------------------------------------------------ reports and pictures
def _last(state: dict | None = None) -> dict:
    if _LAST is None:
        raise ValueError("분석 결과가 없습니다. 먼저 분석해 주세요.")
    if state is not None:
        r = _LAST["result"]
        want = (state.get("inputHash"), state.get("baseMonth", _ym(r.base)))
        if want != (r.input_hash, _ym(r.base)):
            raise ValueError("보고서를 만들 분석 결과가 현재 화면의 데이터와 다릅니다. 다시 분석해 주세요. "
                             f"(state {want[0]!s:.16}/{want[1]}, last {r.input_hash[:16]}/{_ym(r.base)})")
    return _LAST


def _notes(notes: dict | None) -> Notes:
    notes = notes or {}
    plan = []
    for p in notes.get("plan") or []:
        item = {k: ("" if p.get(k) is None else str(p.get(k))).strip() for k in PLAN_KEYS}
        if any(item.values()):
            plan.append(item)
    return Notes(issues=str(notes.get("issues") or ""), plan=plan)


def render_report(state: dict, notes: dict, fmt: Literal["docx", "pdf", "zip"]) -> bytes:
    """Word / PDF report or chart-PNG ZIP of the last analysis (300-dpi charts, rendered once)."""
    if fmt not in FORMATS:
        raise ValueError(f"fmt must be one of {FORMATS}: {fmt!r}")
    last = _last(state)
    r = last["result"]
    from qreport.render.bundle import render_chart_pngs, render_png_zip
    if last["pngs"] is None:
        last["pngs"] = render_chart_pngs(r)
    pngs = last["pngs"]
    label = SAMPLE_LABEL if last["kind"] == "sample" else None
    if fmt == "zip":
        return render_png_zip(r, pngs, data_label=label)
    doc_no = doc_no_from_files(last["files"], r.base)
    if fmt == "docx":
        from qreport.render.docx_report import render_docx
        return render_docx(r, _notes(notes), doc_no, pngs, data_label=label)
    from qreport.render.pdf_report import render_pdf
    return render_pdf(r, _notes(notes), doc_no, pngs, data_label=label)


def chart_pngs(model: str | None = None, state: dict | None = None) -> dict[str, str]:
    """Screen charts of the last analysis as base64 PNG (same figures as the report, SCREEN_DPI).
    model=None: overall set plus pchart_<model> per model; model="X": every chart filtered to X.
    state (the run shown on screen) is checked against the last analysis like render_report."""
    last = _last(state)
    key = model or TOTAL
    if key not in last["screen"]:
        from qreport.render.charts import build_charts, to_png
        out = {}
        for k, spec in build_charts(last["result"], model).items():
            out[k] = base64.b64encode(to_png(spec, dpi=SCREEN_DPI)).decode("ascii")
            spec.fig.clear()
        last["screen"][key] = out
    return last["screen"][key]
