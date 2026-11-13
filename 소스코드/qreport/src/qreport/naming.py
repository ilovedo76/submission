"""Base-month options and report document numbers (shared by the Streamlit app and the web page)."""
from __future__ import annotations

import re

import pandas as pd


def base_options(first: pd.Period, last: pd.Period) -> list[pd.Period]:
    """Selectable base months, newest first (index 0 = default = last occurrence month)."""
    return [last - i for i in range((last - first).n + 1)]


def doc_no_from_files(files: list[tuple[str, bytes]], base: pd.Period) -> str:
    tag = base.strftime("%y%m")
    if not files:
        return f"QR-{tag}"
    stem = re.sub(r"\.[^.]+$", "", sorted(f[0] for f in files)[0].replace("\\", "/").split("/")[-1])
    stem = re.sub(r"[^0-9A-Za-z가-힣 _.-]", "", stem).strip()[:30] or "QR"
    return f"{stem}-{tag}"
