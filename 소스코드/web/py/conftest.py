"""Desktop test setup for web/py: run with qreport's venv (`qreport\\.venv\\Scripts\\python -m pytest web/py`).

Puts web/py (web_entry), qreport/src (`import qreport`) and qreport (`from tests.synth import ...`)
on sys.path, and provides the confidential company workbook as a fixture (skipped when absent).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for p in (HERE, REPO / "qreport" / "src", REPO / "qreport"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

COMPANY_PATH = REPO / "qreport" / "tests" / "data" / "company.xlsx"


@pytest.fixture(scope="session")
def company_bytes() -> bytes:
    if not COMPANY_PATH.exists():
        pytest.skip("qreport/tests/data/company.xlsx not present (company data is not committed)")
    return COMPANY_PATH.read_bytes()
