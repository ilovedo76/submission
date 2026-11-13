from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from qreport.months import normalize_month


@pytest.mark.parametrize("v", [2401, "2401", "'24.01", "24.01", "2024-01", "2024.1", "2024년 1월", datetime(2024, 1, 15), np.int64(2401), np.float64(2401.0)])
def test_normalize_month_variants(v):
    assert normalize_month(v) == pd.Period("2024-01", "M")


def test_normalize_month_rejects_garbage():
    with pytest.raises(ValueError):
        normalize_month("abc")
    with pytest.raises(ValueError):
        normalize_month(2413)


@pytest.mark.parametrize("v", [float("nan"), float("inf"), pd.NaT, None, True, np.int64(2413)])
def test_normalize_month_rejects_invalid_scalars(v):
    with pytest.raises(ValueError):
        normalize_month(v)
