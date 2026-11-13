"""Month normalization: many spellings -> pd.Period(freq="M")."""
from __future__ import annotations

import math
import numbers
import re
from datetime import date, datetime

import numpy as np
import pandas as pd


def _make(year: int, month: int, raw: object) -> pd.Period:
    if not (1 <= month <= 12) or not (1900 <= year <= 2100):
        raise ValueError(f"cannot interpret month: {raw!r}")
    return pd.Period(year=year, month=month, freq="M")


def normalize_month(value: object) -> pd.Period:
    if isinstance(value, pd.Period):
        return value.asfreq("M")
    if isinstance(value, (datetime, date, pd.Timestamp)):
        if pd.isna(value):
            raise ValueError(f"cannot interpret month: {value!r}")
        return pd.Period(year=value.year, month=value.month, freq="M")
    if value is None or isinstance(value, (bool, np.bool_)):
        raise ValueError(f"cannot interpret month: {value!r}")
    if isinstance(value, numbers.Real) and not isinstance(value, numbers.Integral):
        f = float(value)
        if not math.isfinite(f):
            raise ValueError(f"cannot interpret month: {value!r}")
        value = int(f) if f == int(f) else f"{f:.2f}"
    if isinstance(value, numbers.Integral):
        value = str(int(value))
    if not isinstance(value, str):
        raise ValueError(f"cannot interpret month: {value!r}")

    s = value.strip().lstrip("'").strip()
    if re.fullmatch(r"\d{4}", s):  # YYMM
        return _make(2000 + int(s[:2]), int(s[2:]), value)
    m = re.fullmatch(r"(\d{4})\s*[-./]\s*(\d{1,2})(?:\s*[-./]\s*\d{1,2})?", s)
    if m:
        return _make(int(m[1]), int(m[2]), value)
    m = re.fullmatch(r"(\d{2})\s*[-./]\s*(\d{1,2})", s)
    if m:
        return _make(2000 + int(m[1]), int(m[2]), value)
    m = re.fullmatch(r"(\d{4}|\d{2})\s*년\s*(\d{1,2})\s*월?", s)
    if m:
        y = int(m[1])
        return _make(y if len(m[1]) == 4 else 2000 + y, int(m[2]), value)
    raise ValueError(f"cannot interpret month: {value!r}")
