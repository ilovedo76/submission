"""Company seed for the shared db: analyze the confidential company workbook on the desktop and
write split_for_db(kind="company") documents to web/seed/company.json (gitignored, never published).

    qreport\.venv\Scripts\python web\build_seed.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent
REPO = WEB.parent
COMPANY = REPO / "qreport" / "tests" / "data" / "company.xlsx"
SEED_OUT = WEB / "seed" / "company.json"

for _p in (WEB / "py", REPO / "qreport" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def build_seed(company: Path = COMPANY, out: Path = SEED_OUT) -> list[tuple[str, dict]]:
    """[(db path, doc)] for the company run; [] (and a message) when the workbook is absent."""
    company, out = Path(company), Path(out)
    if not company.exists():
        print(f"seed skipped: {company} not found (company data is not in git)")
        return []
    import web_entry as we
    res = we.analyze_files([(company.name, company.read_bytes())], None, None, "company")
    if not res.get("ok"):
        raise RuntimeError(f"company analysis failed: {res.get('error') or res}")
    docs = we.split_for_db(res)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([[p, d] for p, d in docs], ensure_ascii=False, separators=(",", ":"),
                              allow_nan=False), encoding="utf-8")
    return docs


if __name__ == "__main__":
    docs = build_seed()
    if docs:
        print(f"{SEED_OUT}: {len(docs)} documents")
