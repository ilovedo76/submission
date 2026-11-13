"""Base-month relative windows (all months are pd.Period freq="M")."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Window:
    start: pd.Period
    end: pd.Period

    def months(self) -> list[pd.Period]:
        n = self.end.ordinal - self.start.ordinal + 1
        return [self.start + i for i in range(n)]

    def contains(self, p: pd.Period) -> bool:
        return self.start <= p <= self.end

    def mask(self, s: pd.Series) -> pd.Series:
        """Boolean Series: which months of s fall in the window (NaT -> False; empty-safe)."""
        return s.between(self.start, self.end).astype(bool)


def recent_12(base: pd.Period) -> Window:
    return Window(base - 11, base)


def prior_12(base: pd.Period) -> Window:
    return Window(base - 23, base - 12)


def weibull_segments(base: pd.Period, seg_months: tuple[int, int, int] = (2, 4, 6)) -> dict[str, Window]:
    s1 = Window(base - (seg_months[0] - 1), base)
    s2_end = s1.start - 1
    s2 = Window(s2_end - (seg_months[1] - 1), s2_end)
    s3_end = s2.start - 1
    s3 = Window(s3_end - (seg_months[2] - 1), s3_end)
    prior = prior_12(base)
    return {
        "S1": s1,
        "S2": s2,
        "S3": s3,
        "PRIOR": prior,
        "RECENT6": Window(s2.start, s1.end),
    }


def has_prior(ds_first_month: pd.Period, base: pd.Period) -> bool:
    return prior_12(base).start >= ds_first_month
