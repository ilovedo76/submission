"""Defect-type surge detection (occurrence-month counts vs trailing 6-month mean)."""
from __future__ import annotations

import pandas as pd

from qreport.ingest.validate import Dataset
from qreport.periods import recent_12
from qreport.ruleset import RuleSet

COLUMNS = ["defect_type", "month", "count", "avg6", "ratio"]


def type_surge(ds: Dataset, base: pd.Period, rules: RuleSet) -> pd.DataFrame:
    d = ds.analyzable()
    rows = []
    for t, g in d.groupby("defect_type"):
        cnt = g.groupby("occur_month").size()
        for m in recent_12(base).months():
            if m > base or m - 6 < ds.first_month:
                continue
            c = int(cnt.get(m, 0))
            avg6 = sum(int(cnt.get(m - j, 0)) for j in range(1, 7)) / 6
            if c >= rules.surge_min and c >= avg6 * rules.surge_ratio:
                rows.append((t, m, c, avg6, c / avg6 if avg6 > 0 else float("inf")))
    return pd.DataFrame(rows, columns=COLUMNS)
