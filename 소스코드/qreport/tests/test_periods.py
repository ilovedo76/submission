import pandas as pd

from qreport.periods import Window, has_prior, prior_12, recent_12, weibull_segments

P = lambda s: pd.Period(s, "M")  # noqa: E731


def test_segments_base_2512():
    s = weibull_segments(P("2025-12"))
    assert (s["S1"].start, s["S1"].end) == (P("2025-11"), P("2025-12"))
    assert (s["S2"].start, s["S2"].end) == (P("2025-07"), P("2025-10"))
    assert (s["S3"].start, s["S3"].end) == (P("2025-01"), P("2025-06"))
    assert (s["PRIOR"].start, s["PRIOR"].end) == (P("2024-01"), P("2024-12"))


def test_rolling_windows_base_2605():
    assert recent_12(P("2026-05")) == Window(P("2025-06"), P("2026-05"))
    assert prior_12(P("2026-05")) == Window(P("2024-06"), P("2025-05"))


def test_has_prior():
    assert has_prior(P("2024-01"), P("2025-12")) and not has_prior(P("2024-01"), P("2025-02"))


def test_recent6_is_s1_union_s2():
    s = weibull_segments(P("2025-12"))
    assert (s["RECENT6"].start, s["RECENT6"].end) == (P("2025-07"), P("2025-12"))


def test_window_months_inclusive_and_contains():
    w = Window(P("2025-11"), P("2026-01"))
    assert w.months() == [P("2025-11"), P("2025-12"), P("2026-01")]
    assert w.contains(P("2025-11")) and w.contains(P("2026-01"))
    assert not w.contains(P("2025-10")) and not w.contains(P("2026-02"))
