"""UI-independent helpers for the Streamlit screens (no streamlit import)."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from qreport.naming import base_options, doc_no_from_files  # noqa: F401  (re-exported)
from qreport.render.content import total_diffs, validation_text  # noqa: F401  (re-exported)

PLAN_COLS = ("담당", "일정", "대책")

# 강렬한레드 status colors. Red is the brand color, so it is reserved for the worst state only;
# mustard and deep teal sit beside it and differ in lightness (light / mid / dark), not hue alone.
# A shape mark and the label always accompany the color.
_RED, _ON_RED, _MUSTARD, _TEAL, _INK, _PAPER_DEEP = "#cf2e2a", "#f6f4ee", "#e0a526", "#1f5f5b", "#363434", "#dddcd7"

# severity -> (mark, label, text color, background)
SEVERITY_STYLE = {
    "높음": ("■", "높음", _ON_RED, _RED),
    "중간": ("▲", "중간", _INK, _MUSTARD),
    "낮음": ("○", "낮음", "#57514a", _PAPER_DEEP),
}
# verdict -> (mark, text color, plane color)
VERDICT_STYLE = {
    "개선 필요": ("■", _ON_RED, _RED),
    "주의": ("▲", _INK, _MUSTARD),
    "정상": ("●", _ON_RED, _TEAL),
}


@dataclass(frozen=True)
class AlertCard:
    icon: str
    label: str
    color: str
    background: str
    rule: str
    target: str
    text: str
    chart_key: str


def alert_cards(alerts, lines: list[str]) -> list[AlertCard]:
    """One card per alert; lines[i] is the comment text of alerts[i]."""
    cards = []
    for a, text in zip(alerts, lines):
        icon, label, color, bg = SEVERITY_STYLE.get(a.severity, SEVERITY_STYLE["낮음"])
        cards.append(AlertCard(icon, label, color, bg, a.rule, a.target, text, a.chart_key))
    return cards


def as_cost_value(raw: str | None) -> float | None:
    try:
        v = float(raw) if raw not in (None, "") else 0.0
    except ValueError:
        return None
    return v if v > 0 else None


def plan_to_df(plan: list[dict]) -> pd.DataFrame:
    rows = [{k: str(p.get(k, "")) for k in PLAN_COLS} for p in plan]
    return pd.DataFrame(rows, columns=list(PLAN_COLS))


def df_to_plan(df: pd.DataFrame) -> list[dict]:
    out = []
    for r in df.to_dict("records"):
        item = {k: ("" if pd.isna(r.get(k)) else str(r.get(k))).strip() for k in PLAN_COLS}
        if any(item.values()):
            out.append(item)
    return out


validation_line = validation_text   # same line as the report appendix


def review_rows(ds) -> pd.DataFrame:
    d = ds.defects
    d = d[d["status"] != "유효"]
    return pd.DataFrame({
        "상태": d["status"], "모델": d["model"],
        "생산월": d["prod_month"].astype(str), "발생월": d["occur_month"].astype(str),
        "불량유형": d["defect_type"], "사유": d["reason"],
        "원본 위치": d["src_file"].astype(str) + " / " + d["src_sheet"].astype(str)
                     + " / " + d["src_row"].astype(str) + "행",
    }).reset_index(drop=True)


def analysis_key(input_hash: str, rule_version: int, base, targets: dict | None,
                 as_cost: float | None, overrides_repr: str = "") -> tuple:
    return (input_hash, rule_version, str(base), tuple(sorted((targets or {}).items())),
            as_cost, overrides_repr)
