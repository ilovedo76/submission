import re
import time
import tomllib
from pathlib import Path

from qreport.ingest.sheets import DEFAULT_DICTIONARY
from qreport.notes import Notes
from qreport.pipeline import analyze
from qreport.render.bundle import render_png_zip
from qreport.render.docx_report import render_docx
from qreport.render.pdf_report import render_pdf
from qreport.ruleset import DEFAULT_RULES

ROOT = Path(__file__).resolve().parents[1]
NETWORK_MODULES = ("requests", "urllib.request", "http.client", "httpx", "urllib3", "socket")


def test_end_to_end_under_60s(company_bytes):
    t = time.perf_counter()
    r = analyze([("c.xlsx", company_bytes)], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    render_docx(r, Notes(), "X")
    render_pdf(r, Notes(), "X")
    render_png_zip(r)
    assert time.perf_counter() - t < 60


def test_no_network_calls():
    mods = "|".join(re.escape(m) for m in NETWORK_MODULES)
    pat = re.compile(rf"^\s*(?:import\s+(?:{mods})\b|from\s+(?:{mods})\b)", re.M)
    files = list((ROOT / "src").rglob("*.py"))
    assert files
    hits = [f"{f.relative_to(ROOT)}: {m.group().strip()}"
            for f in files for m in pat.finditer(f.read_text(encoding="utf-8"))]
    assert hits == []


def test_usage_stats_disabled():
    cfg = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert cfg["browser"]["gatherUsageStats"] is False
    assert cfg["server"]["showEmailPrompt"] is False


def test_run_bat_starts_from_project_folder():
    # streamlit reads .streamlit/config.toml from the working directory
    raw = (ROOT / "실행.bat").read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    lines = raw.decode("cp949").splitlines()
    cd = next(i for i, ln in enumerate(lines) if ln.lower().startswith("cd /d"))
    run = next(i for i, ln in enumerate(lines) if "streamlit run" in ln)
    assert lines[cd] == 'cd /d "%~dp0"' and cd < run


def test_install_uses_lock_constraints():
    lock = (ROOT / "requirements-lock.txt").read_text(encoding="utf-8").splitlines()
    pins = {ln.split("==")[0].lower() for ln in lock if "==" in ln}
    assert {"pandas", "numpy", "scipy", "openpyxl", "matplotlib", "docxtpl", "reportlab",
            "streamlit", "pytest", "pillow", "pypdf"} <= pins
    assert not any(ln.startswith("-e") or "qreport" in ln for ln in lock)
    raw = (ROOT / "설치.bat").read_bytes()
    assert b"\n" not in raw.replace(b"\r\n", b"")      # CRLF only
    bat = raw.decode("cp949")                            # CP949 (ANSI)
    assert "pip install -c requirements-lock.txt -e .[dev]" in bat
