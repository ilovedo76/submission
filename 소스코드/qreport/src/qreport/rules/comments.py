"""Rule-based Korean bullet comments (spec 3.4). Numbers come only from alerts/metrics."""
from __future__ import annotations

import math

import pandas as pd

from qreport.pipeline import AnalysisResult
from qreport.rules.engine import Alert

NO_ALERTS = "관리 기준을 벗어난 항목이 없습니다."
HYP = " (가설)"


def _ym(s: str) -> str:
    """'2025-12' -> '25.12'."""
    return f"{s[2:4]}.{s[5:7]}"


def _period(p: str) -> str:
    """'2025-11~2025-12' -> '25.11~12'; single month -> '25.12'."""
    if "~" not in p:
        return _ym(p)
    a, b = p.split("~")
    return f"{_ym(a)}~{b[5:7]}" if a[:4] == b[:4] else f"{_ym(a)}~{_ym(b)}"


def _pct(v: float) -> str:
    return f"{v * 100:.3f}%"


def ratio_text(v: float) -> str:
    """Ratios ('배') everywhere: one decimal."""
    return f"{v:.1f}배"


def change_text(pct: float) -> str:
    """Percent change everywhere (screen and report): signed, no decimals, e.g. '+57%'."""
    return f"{pct:+.0f}%"


def _prior_label(result: AnalysisResult) -> str:
    w = result.metrics.windows.get("PRIOR12")
    if w is not None and w.start.month == 1 and w.end.month == 12:
        return f"'{str(w.start.year)[2:]}"
    return "전년"


def verdict_line(result: AnalysisResult) -> str:
    return f"종합 판정: {result.verdict} (경고 {len(result.alerts)}건)"


def _point_from_metrics(result: AnalysisResult, month: str) -> dict:
    df = result.metrics.pchart_total.df
    row = df.loc[df["prod_month"] == pd.Period(month, "M")].iloc[0]
    return {"month": month, "p": float(row["p"]), "x": int(row["x"])}


def _r1_total(a: Alert, result: AnalysisResult) -> str:
    pts = a.detail.get("points") or []
    last = pts[-1] if pts else _point_from_metrics(result, a.detail["months"][-1])
    s = (f"{_ym(last['month'])} 생산분 1MIS {_pct(last['p'])}({last['x']}건)로 "
         f"관리상한 {_pct(a.threshold)} 초과")
    n = a.detail.get("consecutive", 1)
    if n >= 2:
        pm = a.detail["months"][-2]
        prev = next((p for p in pts if p["month"] == pm), None) or _point_from_metrics(result, pm)
        s += f". {_ym(pm)}({_pct(prev['p'])}, {prev['x']}건)에 이어 {n}개월 연속 이탈."
    else:
        s += "."
    return s


def _r1_models(a: Alert) -> str:
    rows = a.detail["rows"]
    parts = []
    for month in sorted({r["month"] for r in rows}, reverse=True):
        items = []
        for r in (r for r in rows if r["month"] == month):
            ratio = ratio_text(r["p"] / r["ucl"]) if r["ucl"] > 0 else "상한 0"
            lead = "상한의 " if not items else ""
            items.append(f"{r['model']} {_pct(r['p'])}({lead}{ratio})")
        parts.append(f"{_ym(month)} 모델별 1MIS: {', '.join(items)} 초과.")
    return " ".join(parts)


def _ci(d: dict) -> str:
    return f"{d['beta']:.2f}(95% CI {d['beta_lo']:.2f}~{d['beta_hi']:.2f})"


def _r2(a: Alert) -> str:
    d = a.detail
    return (f"{a.target} 생산분 {_period(a.period)} 중심선 위 {d['length']}개월 연속 "
            f"(런 규칙 {a.threshold:.0f}개월 이상).")


def _r3(a: Alert) -> str:
    d = a.detail
    if a.target == "전체":
        return (f"{_period(a.period)} 생산분 β {_ci(d)}로 초기 고장형 확정. "
                f"공정 변경·부품 로트·협력사 변경 이력 점검 권고{HYP}")
    items = ", ".join(f"{k} β {_ci(v)}" for k, v in d["models"].items())
    return (f"{_period(a.period)} 생산분 모델별 {items}로 초기 고장형. "
            f"해당 모델의 공정 변경·부품 로트 이력 점검 권고{HYP}")


def _r4(a: Alert, prior: str) -> str:
    d = a.detail
    return (f"{a.target} 6MIS {_pct(d['recent_6mis'])}({prior} {_pct(d['prior_6mis'])}의 "
            f"{ratio_text(a.value)}), β ≈ 1로 패턴 유지·수준 상승. 설계·부품 신뢰성 점검 권고{HYP}")


def _r5(a: Alert) -> str:
    items = ", ".join(f"{r['type']} {r['count']}건(직전 6개월 평균 {r['avg6']:.1f}건, "
                      f"기준 {r['threshold']:.1f}건)" for r in a.detail["types"][:3])
    return f"{_ym(a.period)} 유형 급증: {items}."


def _r6(a: Alert, prior: str) -> str:
    d = a.detail
    rows = d["rows"][:3]
    if d["by"] == "total":
        r = rows[0]
        return (f"최근 12개월 시장 불량률 {_pct(r['p1'])}({prior} {_pct(r['p0'])}의 "
                f"{ratio_text(r['ratio'])})로 악화.")
    label = "모델" if d["by"] == "model" else "유형"
    items = ", ".join(f"{r['group']} {r['x1']}건({prior} {r['x0']}건의 {ratio_text(r['ratio'])})"
                      for r in rows)
    return f"전년 대비 악화 {label}: {items}."


def _r7(a: Alert) -> str:
    cells = a.detail["cells"]
    top = max(cells, key=lambda c: c["rate"] / c["baseline"] if c["baseline"] > 0 else math.inf)
    return (f"히트맵 {_period(a.period)}: 평소보다 높은 칸 {a.detail['count']}개 "
            f"(최대 {_ym(top['month'])} 생산분 {top['mis']}MIS {_pct(top['rate'])}, "
            f"기준 {_pct(top['baseline'])}).")


def _r8(a: Alert) -> str:
    return (f"{a.target} 최근 12개월 시장 불량률 {_pct(a.value)}가 목표 "
            f"{_pct(a.threshold)} 초과.")


def _line(a: Alert, result: AnalysisResult) -> str:
    prior = _prior_label(result)
    if a.rule == "R1":
        return _r1_total(a, result) if a.target == "전체" else _r1_models(a)
    return {"R2": lambda: _r2(a), "R3": lambda: _r3(a), "R4": lambda: _r4(a, prior),
            "R5": lambda: _r5(a), "R6": lambda: _r6(a, prior), "R7": lambda: _r7(a),
            "R8": lambda: _r8(a)}[a.rule]()


def comment_lines(result: AnalysisResult) -> list[str]:
    if not result.alerts:
        return [NO_ALERTS]
    return [_line(a, result) for a in result.alerts]
