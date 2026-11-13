"""web_entry: summary JSON (run + series) is the qreport engine result, db splitting, report bytes."""
from __future__ import annotations

import base64
import io
import json
import math
import sys
import time
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

import web_entry as we
from qreport.ingest.sheets import DEFAULT_DICTIONARY
from qreport.metrics.heatmap import heatmap
from qreport.metrics.market import market_control_limits
from qreport.pipeline import analyze
from qreport.render.charts import build_charts
from qreport.rules.comments import comment_lines, verdict_line
from qreport.render.content import cost_text, forecast_text, validation_text
from qreport.ruleset import DEFAULT_RULES
from tests.synth import make_workbook

SERIES_NAMES = {"market", "pchart", "heatmap", "heatmapBaseline", "pareto", "modelType"}


def _dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


@pytest.fixture(scope="module")
def company(company_bytes):
    out = we.analyze_files([("c.xlsx", company_bytes)], None, None, "company")
    r = analyze([("c.xlsx", company_bytes)], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    return out, r


def _rows(series, name, scope):
    return series[name][scope]


# ------------------------------------------------------------------ run summary
def test_company_summary_matches_analyze(company):
    out, r = company
    assert out["ok"] and out["run"]["verdict"] == r.verdict == "개선 필요"
    assert out["run"]["alertCount"] == len(r.alerts) == 10
    assert out["run"]["comments"] == comment_lines(r)


def test_run_fields_are_the_engine_values(company):
    out, r = company
    run = out["run"]
    assert run["kind"] == "company" and run["fileNames"] == ["c.xlsx"]
    assert run["inputHash"] == r.input_hash
    assert run["baseMonth"] == r.base.strftime("%Y-%m")
    assert run["baseOptions"][0] == run["baseMonth"]
    assert run["baseOptions"][-1] == r.dataset.first_month.strftime("%Y-%m")
    assert len(run["baseOptions"]) == (r.dataset.last_occur_month - r.dataset.first_month).n + 1
    assert run["ruleVersion"] == 1 and len(run["ruleHash"]) == 64
    assert run["verdictLine"] == verdict_line(r)
    assert run["validation"] == validation_text(r.dataset.summary, r.reports)
    assert run["forecastText"] == forecast_text(r) and run["costText"] == cost_text(r)
    assert run["models"] == list(r.dataset.models)
    h, rh = run["headline"], r.headline
    assert h["level"] == rh.level and h["changePct"] == rh.change_pct and h["pvalue"] == rh.pvalue
    assert h["unstableMonths"] == rh.unstable_months
    assert h["topType"] == rh.top_type and h["topTypeShare"] == rh.top_type_share
    assert h["forecast"] == (None if rh.forecast is None else [float(v) for v in rh.forecast])
    assert h["cost"] is None
    assert [a["rule"] for a in run["alerts"]] == [a.rule for a in r.alerts]
    for a, ra in zip(run["alerts"], r.alerts):
        assert (a["severity"], a["target"], a["period"], a["chartKey"]) == (
            ra.severity, ra.target, ra.period, ra.chart_key)
        assert a["value"] == (ra.value if math.isfinite(ra.value) else None)
        assert a["threshold"] == (ra.threshold if math.isfinite(ra.threshold) else None)


def _check_model_headline(out, r):
    from qreport.metrics.yoy import yoy
    run, h = out["run"], r.headline
    # the helper applied to the total path reproduces headline (same expressions as pipeline.headline)
    change, pv = we._yoy_change(r.metrics.yoy_total.iloc[0])
    assert change == h.change_pct and pv == h.pvalue
    assert r.metrics.market_recent["전체"] == h.level == run["headline"]["level"]
    mh = run["modelHeadline"]
    assert list(mh) == [str(m) for m in r.dataset.models]
    by_model = yoy(r.dataset, r.base, DEFAULT_RULES, "model").set_index("group")
    for model, v in mh.items():
        row = by_model.loc[model]
        assert v["level"] == r.metrics.market_recent[model]
        assert v["changePct"] == (row["p1"] / row["p0"] - 1) * 100
        assert v["pvalue"] == row["pvalue"]
    return mh


def test_model_headline_is_engine_yoy_and_market(company):
    out, r = company
    mh = _check_model_headline(out, r)
    assert mh["양문형"]["level"] > 0 and mh["양문형"]["changePct"] is not None


def test_model_headline_synth_and_no_prior_year():
    data = make_workbook(months=24, seed=0, anomalies={"1mis_spike", "type_surge"})
    out = we.analyze_files([("s.xlsx", data)], None, None, "user")
    r = analyze([("s.xlsx", data)], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    _check_model_headline(out, r)
    short = we.analyze_files([("s.xlsx", data)], "2025-11", None, "user")   # 23 months: no prior 12
    assert short["run"]["headline"]["changePct"] is None
    for v in short["run"]["modelHeadline"].values():
        assert v["level"] is not None and v["changePct"] is None and v["pvalue"] is None


def test_cost_uses_as_cost(company_bytes):
    out = we.analyze_files([("c.xlsx", company_bytes)], None, 50000, "company")
    r = analyze([("c.xlsx", company_bytes)], None, DEFAULT_RULES, DEFAULT_DICTIONARY, 50000)
    assert out["run"]["headline"]["cost"] == [float(v) for v in r.headline.cost]
    assert out["run"]["costText"] == cost_text(r)


def test_bad_kind_and_cost_are_error_results():
    data = make_workbook(24, "2024-01", 0)
    out = we.analyze_files([("s.xlsx", data)], None, None, "demo")
    assert out["ok"] is False and "demo" in out["error"]
    out = we.analyze_files([("s.xlsx", data)], None, "만원", "user")
    assert out["ok"] is False and "AS 처리비" in out["error"]
    out = we.analyze_files([("s.xlsx", data)], None, "30000", "user")
    assert out["ok"] and out["run"]["headline"]["cost"] is not None


def test_doc_no_and_base_options_come_from_qreport():
    from qreport.naming import base_options, doc_no_from_files
    data = make_workbook(24, "2024-01", 0)
    files = [("b 파일.xlsx", data)]
    out = we.analyze_files(files, None, None, "user")
    r = analyze(files, None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    assert out["run"]["docNo"] == doc_no_from_files(files, r.base) == "b 파일-2512"
    assert out["run"]["baseOptions"] == [p.strftime("%Y-%m") for p in
                                         base_options(r.dataset.first_month, r.dataset.last_occur_month)]


# ------------------------------------------------------------------ series
def test_series_model_denominator(company):
    out, _ = company
    rows = {x["m"]: x for x in _rows(out["series"], "pchart", "양문형")}
    row = rows["2025-12"]
    assert round(row["p"], 5) == 0.00241 and row["n"] == 4155
    total = {x["m"]: x for x in _rows(out["series"], "pchart", "전체")}["2025-12"]
    assert round(total["p"], 5) == 0.00136 and total["n"] != 4155


def test_series_equal_engine_metrics(company):
    out, r = company
    s = out["series"]
    assert set(s) == SERIES_NAMES
    scopes = ["전체"] + list(r.dataset.models)
    for name in SERIES_NAMES:
        assert list(s[name]) == scopes, name
    for scope in scopes:
        model = None if scope == "전체" else scope
        pc = r.metrics.pchart_total if model is None else r.metrics.pchart_by_model[model]
        got = s["pchart"][scope]
        assert [x["m"] for x in got] == [p.strftime("%Y-%m") for p in pc.df["prod_month"]]
        assert [x["p"] for x in got] == list(pc.df["p"])
        assert [x["ucl"] for x in got] == list(pc.df["ucl"])
        assert all(x["pbar"] == pc.pbar for x in got)
        assert [x["m"] for x in got if x["out"]] == [p.strftime("%Y-%m") for p in pc.test1]
        mr = market_control_limits(r.dataset, r.base, r.rules, model)
        got = s["market"][scope]
        assert [x["rate"] for x in got] == [None if pd.isna(v) else v for v in mr["rate"]]
        assert [x["ucl"] for x in got] == list(mr["ucl"])
        assert [x["sales"] for x in got] == list(mr["sales"])
        hm = heatmap(r.dataset, r.base, r.rules, model)
        base = {x["k"]: x["rate"] for x in s["heatmapBaseline"][scope]}
        assert base == {int(k): (None if pd.isna(v) else float(v)) for k, v in hm.baseline.items()}


def test_heatmap_unobserved_is_null(company):
    out, r = company
    rows = [x for x in _rows(out["series"], "heatmap", "전체") if x["m"] == "2025-12"]
    by_k = {x["k"]: x for x in rows}
    assert by_k[1]["rate"] is not None and by_k[1]["observed"]
    for k in range(2, 7):
        assert by_k[k]["rate"] is None and by_k[k]["observed"] is False and by_k[k]["highlight"] is False
    hl = [(x["m"], x["k"]) for x in _rows(out["series"], "heatmap", "전체") if x["highlight"]]
    hm = r.metrics.heatmap
    want = [(pm.strftime("%Y-%m"), int(k)) for pm in hm.highlight.index for k in hm.highlight.columns
            if hm.highlight.loc[pm, k]]
    assert sorted(hl) == sorted(want)


def _check_pareto_model_type(out, r):
    s = out["series"]
    for model in (None, r.dataset.models[0]):
        scope = "전체" if model is None else model
        specs = build_charts(r, model)
        pd_ = specs["pareto"].data
        got = s["pareto"][scope]
        assert [x["type"] for x in got] == [str(i) for i in pd_.index]
        assert [x["count"] for x in got] == [int(v) for v in pd_["count"]]
        assert [x["cum"] for x in got] == [float(v) for v in pd_["cum_share"]]
        mat = specs["model_type"].data
        got = {(x["model"], x["type"]): x for x in s["modelType"][scope]}
        assert set(got) == {(m, t) for m in mat.index for t in mat.columns}
        for (m, t), x in got.items():
            assert x["recent"] - x["prior"] == x["diff"] == int(mat.loc[m, t])
        for spec in specs.values():
            spec.fig.clear()


def test_pareto_and_model_type_match_charts(company):
    _check_pareto_model_type(*company)


def test_pareto_and_model_type_match_charts_synth():
    files = [("s.xlsx", make_workbook(24, "2024-01", 2, {"type_surge", "yoy_worse"}))]
    out = we.analyze_files(files, None, None, "user")
    _check_pareto_model_type(out, analyze(files, None, DEFAULT_RULES, DEFAULT_DICTIONARY))


def test_json_serializable_and_split_under_limit(company):
    out, _ = company
    _dumps(out)
    docs = we.split_for_db(out)
    run_id = f"{out['run']['inputHash'][:16]}_{out['run']['baseMonth']}"
    assert docs[0][0] == f"runs/{run_id}"
    assert all(p.startswith(f"runs/{run_id}/series/") for p, _ in docs[1:])
    for path, doc in docs:
        assert len(_dumps(doc).encode("utf-8")) <= 256 * 1024, path
        assert all(len(seg.encode("utf-8")) <= 200 for seg in path.split("/"))
    assert _join(docs) == out["series"]


# ------------------------------------------------------------------ large input
def _join(docs) -> dict:
    run = docs[0][1]
    series: dict = {}
    for path, doc in docs[1:]:
        name = doc["name"]
        tail = path.rsplit("/", 1)[-1]
        assert tail == (name if run["seriesParts"][name] == 1 else f"{name}__{doc['part']}")
        dst = series.setdefault(name, {})
        for scope, rows in doc["data"].items():
            dst.setdefault(scope, []).extend(rows)
    assert {k: len([d for _, d in docs[1:] if d["name"] == k]) for k in series} == run["seriesParts"]
    return series


def _eight_model_book() -> bytes:
    """Two 4-model synth workbooks (36 months) merged into one 8-model workbook."""
    a = openpyxl.load_workbook(io.BytesIO(make_workbook(36, "2023-01", 0, {"1mis_spike", "infant"})))
    b = openpyxl.load_workbook(io.BytesIO(make_workbook(36, "2023-01", 1, {"run_up"})))
    rename = lambda v: f"{v}-B"   # noqa: E731
    for row in list(b["RawData"].iter_rows(min_row=2, values_only=True)):
        a["RawData"].append([rename(row[0]), *row[1:]])
    for name in ("생산량", "판매량"):
        wa, wb_ = a[name], b[name]
        wa.delete_rows(wa.max_row)                     # drop Total: auto-summed
        for row in list(wb_.iter_rows(min_row=2, values_only=True)):
            if str(row[0]).lower() != "total":
                wa.append([rename(row[0]), *row[1:]])
    buf = io.BytesIO()
    a.save(buf)
    return buf.getvalue()


@pytest.fixture(scope="module")
def large():
    data = _eight_model_book()
    out = we.analyze_files([("big.xlsx", data)], None, None, "user")
    assert out["ok"], out
    return out


def test_large_input_splits(large):
    assert len(large["run"]["models"]) == 8 and len(large["series"]["market"]["전체"]) == 36
    for limit in (256 * 1024, 16 * 1024, _tight(large)):
        docs = we.split_for_db(large, limit=limit)
        for path, doc in docs:
            assert len(_dumps(doc).encode("utf-8")) <= limit, (limit, path)
        assert _join(docs) == large["series"]
        if limit < 256 * 1024:
            assert any("__" in p for p, _ in docs)


def _tight(out) -> int:
    """Smallest limit the run doc still fits in (well below one model's heatmap rows)."""
    return len(_dumps(we.split_for_db(out)[0][1]).encode("utf-8")) + 16


def test_split_rows_of_one_model_when_needed(large):
    # without the per-model headline the run doc is small enough for a limit below one model's rows
    lean = {**large, "run": {k: v for k, v in large["run"].items() if k != "modelHeadline"}}
    limit = _tight(lean)
    assert len(_dumps(large["series"]["heatmap"]["4Door"]).encode("utf-8")) > limit
    docs = we.split_for_db(lean, limit=limit)
    parts = [d for p, d in docs if d.get("name") == "heatmap"]
    scopes = [s for d in parts for s in d["data"]]
    assert len(scopes) > len(set(scopes))           # one model's rows spread over several docs
    assert [d["part"] for d in parts] == list(range(len(parts)))


def test_split_refuses_impossible_limit(large):
    with pytest.raises(ValueError):
        we.split_for_db(large, limit=200)


# ------------------------------------------------------------------ input errors / mapping
def _book(edit) -> bytes:
    wb = openpyxl.load_workbook(io.BytesIO(make_workbook(24, "2024-01", 0)))
    edit(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_input_error_and_mapping():
    no_sales = _book(lambda wb: wb.remove(wb["판매량"]))
    out = we.analyze_files([("x.xlsx", no_sales)], None, None, "user")
    assert out == {"ok": False, "error": out["error"]} and "판매량" in out["error"]

    def rename(wb):
        wb["RawData"]["D1"] = "결함 내용"
    renamed = _book(rename)
    out = we.analyze_files([("y.xlsx", renamed)], None, None, "user")
    assert out["ok"] is False
    assert out["needs_mapping"] == [{"file": "y.xlsx", "sheet": "RawData",
                                     "columns": ["냉장고", "생산월", "발생월", "결함 내용", "사용개월"],
                                     "missing": ["defect_type"]}]
    ov = {"roles": [{"file": "y.xlsx", "sheet": "RawData", "role": "defects"}],
          "columns": [{"file": "y.xlsx", "sheet": "RawData", "map": {"결함 내용": "defect_type"}}]}
    out = we.analyze_files([("y.xlsx", renamed)], None, None, "user", ov)
    assert out["ok"], out
    _dumps(out)


def test_duplicate_files_error():
    data = make_workbook(24, "2024-01", 0)
    out = we.analyze_files([("a.xlsx", data), ("b.xlsx", data)], None, None, "user")
    assert out["ok"] is False and "같은 파일" in out["error"]


def test_base_month_option(company_bytes):
    out = we.analyze_files([("c.xlsx", company_bytes)], "2025-06", None, "company")
    r = analyze([("c.xlsx", company_bytes)], pd.Period("2025-06", "M"), DEFAULT_RULES, DEFAULT_DICTIONARY)
    assert out["run"]["baseMonth"] == "2025-06" and out["run"]["verdict"] == r.verdict
    assert out["run"]["comments"] == comment_lines(r)


# ------------------------------------------------------------------ report bytes / pngs
def test_render_report_bytes(company_bytes):
    out = we.analyze_files([("c.xlsx", company_bytes)], None, None, "company")
    notes = {"issues": "테스트 이슈", "plan": [{"담당": "품질팀", "일정": "1월", "대책": "점검"},
                                              {"담당": "", "일정": "", "대책": ""}]}
    assert we.render_report(out["run"], notes, "docx")[:2] == b"PK"
    assert we.render_report(out["run"], notes, "pdf")[:4] == b"%PDF"
    assert we.render_report(out["run"], notes, "zip")[:2] == b"PK"
    with pytest.raises(ValueError):
        we.render_report({**out["run"], "inputHash": "0" * 64}, notes, "pdf")
    with pytest.raises(ValueError):
        we.render_report(out["run"], notes, "pptx")


def test_chart_pngs(company_bytes):
    out = we.analyze_files([("c.xlsx", company_bytes)], None, None, "company")
    pngs = we.chart_pngs(None)
    assert "trend" in pngs and f"pchart_{out['run']['models'][0]}" in pngs
    raw = base64.b64decode(pngs["trend"])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    one = we.chart_pngs(out["run"]["models"][0])
    assert not any(k.startswith("pchart_") for k in one)


def _text(paragraphs) -> str:
    return " | ".join(p.text for p in paragraphs)


def test_sample_reports_carry_data_label():
    import docx
    import zipfile
    files = [("sample.xlsx", make_workbook(24, "2024-01", 0, {"1mis_spike"}))]
    out = we.analyze_files(files, None, None, "sample")
    d = docx.Document(io.BytesIO(we.render_report(out["run"], {}, "docx")))
    assert we.SAMPLE_LABEL in _text(d.paragraphs)
    assert we.SAMPLE_LABEL in _text(d.sections[0].header.paragraphs)
    z = zipfile.ZipFile(io.BytesIO(we.render_report(out["run"], {}, "zip")))
    assert we.SAMPLE_LABEL in z.read("README.txt").decode("utf-8")
    out = we.analyze_files(files, None, None, "user")
    d = docx.Document(io.BytesIO(we.render_report(out["run"], {}, "docx")))
    assert we.SAMPLE_LABEL not in _text(d.paragraphs)
    assert "README.txt" not in zipfile.ZipFile(io.BytesIO(we.render_report(out["run"], {}, "zip"))).namelist()


def test_failed_analyze_clears_last_result():
    data = make_workbook(24, "2024-01", 0)
    ok = we.analyze_files([("s.xlsx", data)], None, None, "user")
    assert we.chart_pngs("양문형", ok["run"])
    bad = we.analyze_files([("s.xlsx", data), ("t.xlsx", data)], None, None, "user")
    assert bad["ok"] is False
    with pytest.raises(ValueError):
        we.render_report(ok["run"], {}, "zip")
    with pytest.raises(ValueError):
        we.chart_pngs(None)


def test_chart_pngs_checks_state():
    data = make_workbook(24, "2024-01", 0)
    ok = we.analyze_files([("s.xlsx", data)], None, None, "user")
    with pytest.raises(ValueError):
        we.chart_pngs(None, {**ok["run"], "inputHash": "f" * 64})


# ------------------------------------------------------------------ Task 4: virtual sample + company seed
def _sample():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sample"))
    import make_sample
    return make_sample


def test_sample_is_deterministic_virtual_and_needs_improvement():
    ms = _sample()
    a = ms.make_sample()
    time.sleep(1.1)                       # different save second: timestamps must not leak in
    assert ms.make_sample() == a
    assert "가상" in ms.FILE_NAME and ms.FILE_NAME.endswith(".xlsx")
    wb = openpyxl.load_workbook(io.BytesIO(a))
    for name in ("Graph", "RawData", "생산량", "판매량"):
        cells = [c.value for row in wb[name].iter_rows() for c in row]
        assert any(isinstance(v, str) and "가상 데이터" in v for v in cells), name
    assert "가상 데이터" in wb.properties.title
    out = we.analyze_files([(ms.FILE_NAME, a)], None, None, "sample")
    assert out["ok"], out.get("error")
    run = out["run"]
    assert run["verdict"] == "개선 필요"
    assert run["alertCount"] >= 4
    assert run["models"] == ["모델A", "모델B", "모델C", "모델D"]
    rawdata_models = {r[0].value for r in wb["RawData"].iter_rows(min_row=2, max_col=1)}
    assert rawdata_models == {"모델A", "모델B", "모델C", "모델D"}


def test_build_seed_writes_company_docs(company_bytes, tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import build_seed
    docs = build_seed.build_seed(out=tmp_path / "company.json")
    assert docs[0][0].startswith("runs/") and docs[0][1]["kind"] == "company"
    saved = json.loads((tmp_path / "company.json").read_text(encoding="utf-8"))
    assert [p for p, _ in saved] == [p for p, _ in docs]
    assert all(len(json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode()) <= we.DB_LIMIT
               for _, d in saved)


def test_build_seed_skips_without_company(tmp_path, capsys):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import build_seed
    assert build_seed.build_seed(company=tmp_path / "missing.xlsx", out=tmp_path / "c.json") == []
    assert not (tmp_path / "c.json").exists()
    assert "missing.xlsx" in capsys.readouterr().out
