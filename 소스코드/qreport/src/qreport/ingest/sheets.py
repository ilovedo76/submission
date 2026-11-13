"""Content-based sheet role detection and reading.

Layout is detected from cell contents; sheet names are only used to tell
production from sales (and can be overridden by the caller).
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pandas as pd

from qreport.months import normalize_month

DEFAULT_DICTIONARY: dict[str, str] = {
    "냉장고": "model",
    "모델": "model",
    "model": "model",
    "생산월": "prod_month",
    "발생월": "occur_month",
    "불량 종류": "defect_type",
    "불량유형": "defect_type",
    "불량 유형": "defect_type",
    "사용개월": "use_months",
}

ROLE_KO = {"defects": "불량 내역(RawData)", "production": "생산량", "sales": "판매량"}
REQUIRED = ["model", "prod_month", "occur_month", "defect_type"]
FIELDS = REQUIRED + ["use_months"]
DEFECT_COLS = FIELDS + ["src_file", "src_sheet", "src_row"]
LONG_COLS = ["model", "month", "qty"]
TOTAL_LABELS = {"total", "합계"}
TOTAL_COL_LABELS = {"total", "합계", "계"}  # a trailing total column in a wide header is ignored


@dataclass
class SheetReport:
    file: str
    sheet: str
    role: str  # "defects"|"production"|"sales"|"ignored"|"unknown"
    reason: str


@dataclass
class MappingRequest:
    file: str
    sheet: str
    columns: list[str]
    missing_fields: list[str]


@dataclass
class RawInput:
    defects: pd.DataFrame
    production: pd.DataFrame
    sales: pd.DataFrame
    reports: list[SheetReport] = field(default_factory=list)
    needs_mapping: list[MappingRequest] = field(default_factory=list)
    overlaps: list[str] = field(default_factory=list)  # wide sheets of one role sharing (model, month)


def _blank(v: object) -> bool:
    return v is None or (not isinstance(v, str) and pd.isna(v))


def _key(s: object) -> str:
    return re.sub(r"\s+", "", str(s)).lower()


def _to_period(series: pd.Series) -> pd.Series:
    cache: dict = {}

    def conv(v):
        if _blank(v):
            return pd.NaT
        try:
            h = (type(v), v)
            if h not in cache:
                cache[h] = normalize_month(v)
            return cache[h]
        except (ValueError, TypeError):
            return pd.NaT

    return pd.Series(pd.array([conv(v) for v in series], dtype="period[M]"), index=series.index)


def _header_row(df: pd.DataFrame) -> int | None:
    for i in range(len(df)):
        if df.iloc[i].notna().any():
            return i
    return None


def _horizontal_months(header: list) -> list[tuple[int, pd.Period]] | None:
    cells = list(header[1:])
    while cells and _blank(cells[-1]):
        cells.pop()
    if cells and isinstance(cells[-1], str) and _key(cells[-1]) in TOTAL_COL_LABELS:
        cells.pop()
    if len(cells) < 2:
        return None
    out = []
    for j, v in enumerate(cells, start=1):
        if _blank(v):
            return None
        try:
            out.append((j, normalize_month(v)))
        except (ValueError, TypeError):
            return None
    return out


def _read_defects(df: pd.DataFrame, h: int, colmap: dict[int, str], file: str, sheet: str) -> pd.DataFrame:
    body = df.iloc[h + 1:]
    out = pd.DataFrame({f: body[c] for c, f in colmap.items()}).dropna(how="all")
    for f in FIELDS:
        if f not in out:
            out[f] = pd.NA
    for f in ("model", "defect_type"):
        out[f] = out[f].map(lambda v: v if _blank(v) else str(v).strip())
    out["prod_month"] = _to_period(out["prod_month"])
    out["occur_month"] = _to_period(out["occur_month"])
    um = pd.to_numeric(out["use_months"], errors="coerce")
    if um.notna().all() and (um % 1 == 0).all():
        um = um.astype("int64")
    out["use_months"] = um
    out["src_file"] = file
    out["src_sheet"] = sheet
    out["src_row"] = out.index + 1  # Excel row number (sheet row 1 == header)
    return out[DEFECT_COLS].reset_index(drop=True)


def _read_wide(df: pd.DataFrame, h: int, months: list[tuple[int, pd.Period]]):
    body = df.iloc[h + 1:]
    frames, total, unlabeled = [], None, 0
    for _, row in body.iterrows():
        if row.isna().all():
            continue
        label = str(row.iloc[0]).strip() if not _blank(row.iloc[0]) else ""
        vals = pd.to_numeric(pd.Series([row.iloc[j] for j, _ in months]), errors="coerce")
        if label.lower() in TOTAL_LABELS:
            total = vals.tolist()
            continue
        if not label:
            unlabeled += 1
            continue
        part = pd.DataFrame({"model": label, "month": [p for _, p in months], "qty": vals})
        frames.append(part.dropna(subset=["qty"]))
    long = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=LONG_COLS)
    long["qty"] = long["qty"].astype("int64")
    long["month"] = pd.array(list(long["month"]), dtype="period[M]")
    if total is None:
        reason = "Total 없음: 자동 합산"
    else:
        sums = long.groupby("month")["qty"].sum()
        diff = 0.0
        for (_, p), t in zip(months, total):
            diff += abs((0 if pd.isna(t) else t) - float(sums.get(p, 0)))
        diff = int(diff) if diff == int(diff) else diff
        reason = "Total 대조: 차이 0" if diff == 0 else f"Total 대조: 차이 {diff}"
    if unlabeled:
        reason += f"; 모델명 없는 행 {unlabeled}개 제외"
    return long[LONG_COLS], reason


def _name_role(sheet: str) -> str | None:
    p, s = "생산" in sheet, "판매" in sheet
    if p and not s:
        return "production"
    if s and not p:
        return "sales"
    return None


def read_workbooks(
    files: list[tuple[str, bytes]],
    dictionary: dict[str, str],
    role_overrides: dict[tuple[str, str], str] | None = None,
    column_overrides: dict[tuple[str, str], dict[str, str]] | None = None,
) -> RawInput:
    role_overrides = role_overrides or {}
    column_overrides = column_overrides or {}
    dic = {_key(k): v for k, v in dictionary.items()}
    defects, prod, sales = [], [], []
    wide: list[tuple[str, str, str, set]] = []  # (role, file, sheet, {(model, month)})
    reports: list[SheetReport] = []
    needs: list[MappingRequest] = []

    for fname, data in files:
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, engine="openpyxl")
        for sname, df in sheets.items():
            ov = role_overrides.get((fname, sname))
            if ov == "ignored":
                reports.append(SheetReport(fname, sname, "ignored", "사용자 지정: 무시"))
                continue
            h = _header_row(df)
            if h is None:
                reports.append(SheetReport(fname, sname, "ignored", "빈 시트"))
                continue
            header = df.iloc[h].tolist()
            cols = [str(x) for x in header if not _blank(x)]
            cov = {_key(k): v for k, v in column_overrides.get((fname, sname), {}).items()}
            colmap: dict[int, str] = {}
            for c, name in enumerate(header):
                if _blank(name):
                    continue
                f = cov.get(_key(name)) or dic.get(_key(name))
                if f in FIELDS and f not in colmap.values():
                    colmap[c] = f
            found = set(colmap.values())
            missing = [f for f in REQUIRED if f not in found]
            months = None if (not missing or ov == "defects") else _horizontal_months(header)

            if not missing and ov in (None, "defects"):
                defects.append(_read_defects(df, h, colmap, fname, sname))
                reports.append(SheetReport(fname, sname, "defects", "세로형: 필수 열 모두 확인"))
            elif months is not None:
                role = ov if ov in ("production", "sales") else _name_role(sname)
                if role is None:
                    needs.append(MappingRequest(fname, sname, cols, ["role"]))
                    reports.append(SheetReport(fname, sname, "unknown", "가로형이나 생산/판매 구분 불가"))
                    continue
                long, reason = _read_wide(df, h, months)
                (prod if role == "production" else sales).append(long)
                wide.append((role, fname, sname, set(zip(long["model"], long["month"]))))
                reports.append(SheetReport(fname, sname, role, reason))
            elif len(found) >= 2 or ov == "defects":
                needs.append(MappingRequest(fname, sname, cols, missing))
                reports.append(SheetReport(fname, sname, "unknown", f"필수 열 누락: {', '.join(missing)}"))
            else:
                reports.append(SheetReport(fname, sname, "ignored", "표 형식 아님(피벗/차트)"))

    overlaps = []
    for i, (role, f1, s1, k1) in enumerate(wide):
        for role2, f2, s2, k2 in wide[i + 1:]:
            common = k1 & k2
            if role == role2 and common:
                overlaps.append(f"{f1} / {s1} 와 {f2} / {s2} 의 {ROLE_KO[role]} 데이터가 "
                                f"(모델, 월) {len(common)}개에서 겹칩니다")

    def cat(frames, cols):
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)

    return RawInput(cat(defects, DEFECT_COLS), cat(prod, LONG_COLS), cat(sales, LONG_COLS), reports, needs,
                    overlaps)
