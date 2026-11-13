from pathlib import Path

import pytest

COMPANY_PATH = Path(__file__).parent / "data" / "company.xlsx"


@pytest.fixture(scope="session")
def company_bytes() -> bytes:
    if not COMPANY_PATH.exists():
        pytest.skip("tests/data/company.xlsx not present (company data is not committed)")
    return COMPANY_PATH.read_bytes()


@pytest.fixture(scope="session")
def company_ds(company_bytes):
    from qreport.ingest.sheets import DEFAULT_DICTIONARY, read_workbooks
    from qreport.ingest.validate import build_dataset

    return build_dataset(read_workbooks([("company.xlsx", company_bytes)], DEFAULT_DICTIONARY))


@pytest.fixture(scope="session")
def company_result(company_bytes):
    from qreport.ingest.sheets import DEFAULT_DICTIONARY
    from qreport.pipeline import analyze
    from qreport.ruleset import DEFAULT_RULES

    return analyze([("company.xlsx", company_bytes)], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
