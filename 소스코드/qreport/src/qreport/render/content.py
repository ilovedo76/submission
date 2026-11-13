"""Report content shared by the Word and PDF renderers (text and table rows; numbers from metrics only)."""
from __future__ import annotations

import math

import pandas as pd

from qreport.notes import PLAN_KEYS, Notes
from qreport.periods import recent_12
from qreport.pipeline import AnalysisResult
from qreport.rules.comments import change_text, comment_lines, ratio_text, verdict_line

TEMPLATE_VERSION = "2"
EMPTY = "(작성 전)"
N_TYPES = 5

SEGMENT_LABELS = {"S1": "직전 2개월 생산분", "S2": "그 앞 4개월 생산분", "S3": "그 앞 6개월 생산분",
                  "PRIOR": "전년 12개월 생산분", "RECENT6": "최근 6개월 생산분 (전체)"}

APPENDIX_FORMULAS = [
    "P 관리도 중심선: p̄ = Σx / Σn  (x = 생산월별 1MIS 불량 건수, n = 해당 생산월 생산량)",
    "관리상한: UCL = p̄ + 3√(p̄(1−p̄)/n)  (월별 n 적용, 3 = 룰 세트의 ucl_sigma)",
    "k MIS 정의: 생산월 코호트의 누적 불량 건수(사용 개월 ≤ k) ÷ 해당 생산월 생산량. "
    "관찰 가능 MIS = 기준월 − 생산월 + 1 (최대 6). 관찰 전 칸은 공란이며 0으로 채우지 않는다.",
    "시장 불량률 정의: 발생월 기준 불량 건수 ÷ 같은 월 판매량 (최근 12개월은 합산 후 나눔).",
    "β 판정 기준: 와이블 적합 β의 95% 신뢰구간 상한 < 1이면 초기 고장형, 하한 > 1이면 마모형, "
    "1을 포함하면 우발형(β ≈ 1).",
    "전년 대비: 최근 12개월 vs 그 직전 12개월의 시장 불량률을 2-비율 z 검정으로 비교 (유의수준 = 룰 세트 alpha).",
]
APPENDIX_DENOMS = [
    "분모 정의: 1MIS 등 코호트 지표는 모델·생산월 단위 생산량, 시장 불량률은 모델·발생월 단위 판매량.",
    "모델을 선택하면 분자·분모가 같은 (모델, 월) 키로 함께 필터되며, 전체는 모델 합계다. Total 행은 대조용으로만 쓴다.",
]
HEADLINE_DEFS = [
    "수준: 최근 12개월 시장 불량률 (기준월로 끝나는 12개월 불량 건수 ÷ 판매량).",
    "변화: 직전 12개월 대비 시장 불량률 증감률과 2-비율 검정 p값.",
    "안정성: 최근 6개 생산월 중 1MIS가 관리상한을 이탈한 개월 수 (검정 1).",
    "집중도: 최근 12개월 불량 중 상위 1개 유형이 차지하는 비중.",
    "전망: 6MIS 미도달 생산월의 추가 접수 예측 범위(현재 추세 vs 전년 적합)와 건당 처리비를 곱한 예상 비용.",
]


def total_diffs(reports) -> list:
    """Wide-sheet reports whose Total row does not match the model sum."""
    return [r for r in reports
            if r.reason.startswith("Total 대조") and not r.reason.split(";")[0].endswith("차이 0")]


def validation_text(summary: dict, reports) -> str:
    """One validation line, identical on screen and in the report appendix."""
    return (f"검증: 유효 {summary.get('유효', 0)} · 검토 {summary.get('검토', 0)} · "
            f"제외 {summary.get('제외', 0)} · 중복 의심 {summary.get('duplicates', 0)}건 · "
            f"Total 차이 {len(total_diffs(reports))}")


def pct(v) -> str:
    return "-" if v is None or not math.isfinite(v) else f"{v * 100:.3f}%"


def _ym(p) -> str:
    return pd.Period(p, "M").strftime("%Y-%m")


def _fit_row(name: str, f) -> dict:
    if f is None:
        return {"name": name, "beta": "-", "ci": "-", "kind": "적합 불가 (고장 건수 부족)"}
    return {"name": name, "beta": f"{f.beta:.2f}", "ci": f"{f.beta_lo:.2f}~{f.beta_hi:.2f}",
            "kind": f.kind()}


def forecast_text(result: AnalysisResult) -> str:
    fc = result.headline.forecast
    if fc is None:
        return "비교 불가"
    return f"약 {round(fc[0])}~{round(fc[1])}건"


def cost_text(result: AnalysisResult) -> str:
    c = result.headline.cost
    if c is None:
        return "건당 처리비 입력 시 산출"
    return f"약 {round(c[0]):,}~{round(c[1]):,}원"


def headline_rows(result: AnalysisResult) -> list[dict]:
    h = result.headline
    if h.change_pct is None:
        change = "비교 불가 (전년 12개월 데이터 없음)"
    else:
        p = "" if h.pvalue is None else (", p<0.0001" if h.pvalue < 1e-4 else f", p={h.pvalue:.4f}")
        change = f"{change_text(h.change_pct)}{p}"
    conc = f"{h.top_type} {h.top_type_share * 100:.1f}%" if h.top_type else "-"
    return [
        {"name": "수준", "value": pct(h.level), "note": "최근 12개월 시장 불량률"},
        {"name": "변화", "value": change, "note": "전년 대비 증감률"},
        {"name": "안정성", "value": f"{h.unstable_months}개월", "note": "최근 6개 생산월 중 관리상한 이탈"},
        {"name": "집중도", "value": conc, "note": "상위 1개 유형 비중"},
        {"name": "전망", "value": forecast_text(result),
         "note": f"추가 접수 예측, 예상 비용 {cost_text(result)}"},
    ]


def model_type_table(result: AnalysisResult) -> tuple[list[str], list[dict]]:
    """Recent-12-month defect counts by model x top-5 types, as fixed columns c0..c6."""
    d = result.dataset.analyzable()
    d = d[recent_12(result.base).mask(d["occur_month"])]
    types = [str(t) for t in d.groupby("defect_type").size()
             .sort_values(ascending=False, kind="stable").index[:N_TYPES]]
    pad = [""] * (N_TYPES - len(types))
    headers = ["모델"] + types + pad + ["합계"]
    rows = []
    for m in result.dataset.models:
        dm = d[d["model"] == m]
        vals = [str(int((dm["defect_type"] == t).sum())) for t in types]
        rows.append({f"c{i}": v for i, v in enumerate([m] + vals + pad + [str(len(dm))])})
    return headers, rows


def model_1mis_rows(result: AnalysisResult) -> list[dict]:
    rows = []
    for m, pc in result.metrics.pchart_by_model.items():
        if pc.df.empty:
            continue
        r = pc.df.iloc[-1]
        over = r["prod_month"] in set(pc.test1)
        rows.append({"model": str(m), "month": _ym(r["prod_month"]), "x": f"{int(r['x'])}",
                     "n": f"{int(r['n']):,}", "p": pct(r["p"]), "ucl": pct(r["ucl"]),
                     "judge": "상한 초과" if over else "상한 이내"})
    return rows


def pchart_rows(result: AnalysisResult) -> list[dict]:
    pc = result.metrics.pchart_total
    t1, r9 = set(pc.test1), set(pc.run9)
    out = []
    for _, r in pc.df.iterrows():
        j = [s for s, hit in (("검정1 상한 초과", r["prod_month"] in t1),
                              ("검정2 중심선 위 연속", r["prod_month"] in r9)) if hit]
        out.append({"month": _ym(r["prod_month"]), "x": f"{int(r['x'])}", "n": f"{int(r['n']):,}",
                    "p": pct(r["p"]), "ucl": pct(r["ucl"]), "judge": ", ".join(j) or "정상"})
    return out


def beta_rows(result: AnalysisResult) -> list[dict]:
    m = result.metrics
    rows = [_fit_row(label, m.weibull.get(k)) for k, label in SEGMENT_LABELS.items()]
    rows += [_fit_row(f"{mdl} (최근 6개월)", f) for mdl, f in m.weibull_by_model.items()]
    return rows


def yoy_rows(result: AnalysisResult) -> list[dict]:
    m = result.metrics
    out = []
    for kind, df in (("전체", m.yoy_total), ("모델", m.yoy_model), ("유형", m.yoy_type)):
        for _, r in df.iterrows():
            pv, ratio = r["pvalue"], r["ratio"]
            out.append({"kind": kind, "group": str(r["group"]), "x0": f"{int(r['x0'])}",
                        "p0": pct(r["p0"]), "x1": f"{int(r['x1'])}", "p1": pct(r["p1"]),
                        "ratio": "-" if not math.isfinite(ratio) else ratio_text(ratio),
                        "pvalue": "-" if not math.isfinite(pv) else ("<0.0001" if pv < 1e-4 else f"{pv:.4f}"),
                        "judge": "악화" if r["worse"] else "유의한 악화 아님"})
    return out


def plan_rows(notes: Notes) -> list[dict]:
    rows = [{k: str(p.get(k, "")) for k in PLAN_KEYS} for p in notes.plan]
    return rows or [{"담당": EMPTY, "일정": "", "대책": ""}]


def build_content(result: AnalysisResult, notes: Notes, doc_no: str) -> dict:
    ds = result.dataset
    th, trows = model_type_table(result)
    return {
        "doc_no": doc_no,
        "period": f"{_ym(ds.first_month)} ~ {_ym(result.base)}",
        "base": _ym(result.base),
        "validation": validation_text(ds.summary, result.reports),
        "verdict": verdict_line(result),
        "comments": comment_lines(result),
        "headline": headline_rows(result),
        "forecast": forecast_text(result),
        "cost": cost_text(result),
        "type_headers": th, "type_rows": trows,
        "model_rows": model_1mis_rows(result),
        "pchart_rows": pchart_rows(result),
        "beta_rows": beta_rows(result),
        "yoy_rows": yoy_rows(result),
        "issues": notes.issues.strip() or EMPTY,
        "plan_rows": plan_rows(notes),
        "models": [str(m) for m in ds.models],
        "formulas": APPENDIX_FORMULAS, "denoms": APPENDIX_DENOMS, "defs": HEADLINE_DEFS,
        "rules_json": result.rules.to_json(), "sha": result.input_hash,
        "template_version": TEMPLATE_VERSION,
    }
