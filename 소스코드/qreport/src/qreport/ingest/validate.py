"""Standard tables with per-row validation status and source tracking."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from qreport.ingest.sheets import RawInput

def _blank(v: object) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (
        not isinstance(v, str) and pd.isna(v))


_DUP_KEY = ["model", "prod_month", "occur_month", "defect_type", "use_months"]


@dataclass
class Dataset:
    defects: pd.DataFrame
    production: pd.DataFrame
    sales: pd.DataFrame
    models: list[str]
    first_month: pd.Period
    last_occur_month: pd.Period
    summary: dict

    def analyzable(self) -> pd.DataFrame:
        return self.defects[self.defects["status"] != "제외"]


def build_dataset(ri: RawInput) -> Dataset:
    d = ri.defects.copy()
    n = len(d)
    reasons: list[list[str]] = [[] for _ in range(n)]
    status = ["유효"] * n

    prod_keys = set(zip(ri.production["model"], ri.production["month"]))
    use = d["use_months"].tolist()
    new_use = list(use)
    for k, (model, dtype_, pm, om, u) in enumerate(
            zip(d["model"], d["defect_type"], d["prod_month"], d["occur_month"], use)):
        unusable = False
        if _blank(model) or _blank(dtype_):
            reasons[k].append("모델/불량 유형 없음")
            unusable = True
        if pd.isna(pm) or pd.isna(om):
            reasons[k].append("생산월/발생월 인식 불가")
            unusable = True
        if unusable:
            status[k] = "제외"
            continue
        if om < pm:
            status[k] = "제외"
            reasons[k].append("발생월이 생산월보다 앞섬")
        else:
            calc = (om - pm).n + 1
            if pd.isna(u) or int(u) != calc:
                orig = "없음" if pd.isna(u) else int(u)
                new_use[k] = calc
                status[k] = "검토"
                reasons[k].append(f"사용개월 불일치: 원본 {orig} → 재계산 {calc}")
        if (model, pm) not in prod_keys:
            status[k] = "제외"
            reasons[k].append("분모(생산량) 없음")

    dup = d.duplicated(subset=_DUP_KEY, keep="first")
    for k in dup[dup].index.map(d.index.get_loc):
        reasons[k].append("중복 의심")

    d["use_months"] = pd.array(new_use, dtype="int64") if all(
        not pd.isna(x) for x in new_use) else new_use
    d["status"] = status
    d["reason"] = ["; ".join(r) for r in reasons]

    ok = d[d["status"] != "제외"]
    last = ok["occur_month"].max() if len(ok) else d["occur_month"].max()
    summary = {s: int((d["status"] == s).sum()) for s in ("유효", "검토", "제외")}
    summary["duplicates"] = int(dup.sum())
    return Dataset(
        defects=d,
        production=ri.production,
        sales=ri.sales,
        models=sorted({m for m in ri.production["model"] if isinstance(m, str) and m.strip()}),
        first_month=ri.production["month"].min(),
        last_occur_month=last,
        summary=summary,
    )
