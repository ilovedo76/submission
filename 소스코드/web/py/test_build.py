"""build.py: the publish folder is self-contained (self-hosted Pyodide), within the artifact limits,
and carries no company data and no tests."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
import build  # noqa: E402

from conftest import COMPANY_PATH  # noqa: E402

MB = 1024 * 1024


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("b") / "publish"
    sizes = build.build(out)
    return out, sizes


def _files(out: Path) -> list[Path]:
    return [p for p in out.rglob("*") if p.is_file()]


def _manifest(out: Path) -> dict:
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))


def _read(out: Path, rel: str) -> bytes:
    """Bytes of a published file by its original path (decoded from base64 parts when encoded)."""
    parts = _manifest(out)["binaries"].get(rel)
    if parts is None:
        return (out / rel).read_bytes()
    return b"".join(base64.b64decode((out / p).read_bytes()) for p in parts)


def _exists(out: Path, rel: str) -> bool:
    return (out / rel).is_file() or rel in _manifest(out)["binaries"]


def test_returns_sizes_of_every_file(built):
    out, sizes = built
    assert sizes == {p.relative_to(out).as_posix(): p.stat().st_size for p in _files(out)}


def test_no_company_bytes(built):
    out, _ = built
    if not COMPANY_PATH.exists():
        pytest.skip("company.xlsx not present")
    company = hashlib.sha256(COMPANY_PATH.read_bytes()).hexdigest()
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() != company for p in _files(out))
    assert all(hashlib.sha256(_read(out, r)).hexdigest() != company for r in _manifest(out)["binaries"])
    import openpyxl
    wb = openpyxl.load_workbook(COMPANY_PATH, read_only=True)
    names = {str(r[0]) for r in wb["RawData"].iter_rows(min_row=2, max_col=1, values_only=True) if r[0]}
    sample = openpyxl.load_workbook(io.BytesIO(_read(out, build.SAMPLE_REL)), read_only=True)
    strings = {v for ws in sample.worksheets for row in ws.iter_rows(values_only=True) for v in row
               if isinstance(v, str)}
    assert "모델A" in strings
    assert not names & strings
    assert not (out / "seed").exists() and not list(out.rglob("company*"))


def test_only_sample_xlsx_and_no_tests(built):
    out, sizes = built
    originals = list(sizes) + list(_manifest(out)["binaries"])
    assert [p for p in originals if p.endswith((".xlsx", ".xlsm", ".csv"))] == [build.SAMPLE_REL]
    assert not [p for p in originals if "tests/" in p or "/test_" in p or p.startswith("test_")
                or ".test." in p or "__pycache__" in p or p.startswith("seed/")]


def test_archives_are_base64_parts_that_decode_to_the_originals(built):
    out, sizes = built
    assert not [p for p in sizes if Path(p).suffix.lower() in build.ENCODE_EXT]
    for p in sizes:
        build.content_type(p)                                # every published file is a served type
    m = _manifest(out)
    lock = json.loads((out / "pyodide" / "pyodide-lock.json").read_text(encoding="utf-8"))
    by_file = {pkg["file_name"]: pkg["sha256"] for pkg in lock["packages"].values()}
    sources = {
        "pyodide/python_stdlib.zip": build.NODE_PYODIDE / "python_stdlib.zip",
        "py/qreport/templates/report.docx": build.QREPORT_SRC / "templates" / "report.docx",
        **{w: build.VENDOR / "wheels" / Path(w).name for w in m["wheels"]},
    }
    sys.path.insert(0, str(WEB / "sample"))
    from make_sample import make_sample
    expected = {k: hashlib.sha256(v.read_bytes()).hexdigest() for k, v in sources.items()}
    expected[build.SAMPLE_REL] = hashlib.sha256(make_sample()).hexdigest()
    expected.update({f"pyodide/{f}": h for f, h in by_file.items()})
    assert set(m["binaries"]) == set(expected)
    for rel, parts in m["binaries"].items():
        assert parts == [f"{rel}.b64.{k}.txt" for k in range(len(parts))]
        assert all(sizes[p] <= 14_000_000 for p in parts)
        assert hashlib.sha256(_read(out, rel)).hexdigest() == expected[rel], rel


def test_manifest_paths_exist(built):
    out, _ = built
    m = _manifest(out)
    assert m["pyodideVersion"] == build.pyodide_env()["version"]
    assert m["indexURL"] == "pyodide/"
    for f in ("pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"):
        assert _exists(out, f"pyodide/{f}"), f
    lock = json.loads((out / "pyodide" / "pyodide-lock.json").read_text(encoding="utf-8"))
    for name, pkg in lock["packages"].items():
        assert _exists(out, f"pyodide/{pkg['file_name']}"), name
    assert set(m["packages"]) <= set(lock["packages"])
    assert len(m["wheels"]) == len(build.pyodide_env()["micropip"])
    for p in m["wheels"] + m["fonts"] + [m["entry"], m["setup"], m["sample"], m["fontLicense"]]:
        assert _exists(out, p), p
    py = {x["path"]: x["size"] for x in m["python"]}
    assert "py/qreport/__init__.py" in py and "py/qreport/templates/report.docx" in py
    assert m["entry"] in py and m["setup"] in py
    assert not [p for p in py if p.startswith(("py/qreport/app/", "py/qreport/store/"))]
    for p, size in py.items():
        assert len(_read(out, p)) == size, p


def test_within_artifact_limits_and_batches(built):
    out, sizes = built
    big = {p: s for p, s in sizes.items() if s > 15 * MB}
    assert not big, big
    listed = json.loads((out / "files.json").read_text(encoding="utf-8"))
    assert set(listed) == set(sizes) - {"index.html"} - {p for p in sizes if build.FILE_LIST_RE.fullmatch(p)}
    assert all(t == build.content_type(p) for p, t in listed.items())
    assert listed["py/web_entry.py"] == "text/plain" and listed["fonts/OFL.txt"] == "text/plain"
    batches = sorted(out.glob("files-*.json"))
    if sum(sizes[p] for p in listed) > build.BATCH_BYTES:
        assert batches
    union: dict = {}
    for f in batches:
        b = json.loads(f.read_text(encoding="utf-8"))
        assert sum(sizes[p] for p in b) <= 60_000_000 and len(b) <= 250, f.name
        assert not set(b) & set(union)
        union.update(b)
    if batches:
        assert union == listed


# ------------------------------------------------------------------ self-contained pages
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

NODE = shutil.which("node")


@pytest.mark.parametrize("page", build.PAGE_FILES)
def test_pages_are_self_contained(built, page, tmp_path):
    out, sizes = built
    if not (build.PAGES / page).is_file():
        pytest.skip(f"web/pages/{page} not present")
    html = (out / page).read_text(encoding="utf-8")
    links = re.findall(r"<link\b[^>]*rel=[\"']?stylesheet[^>]*>", html, re.I)
    assert all("https://fonts.googleapis.com/" in l for l in links), links
    assert "<style>" in html
    assert not re.search(r"<script\b[^>]*type=[\"']?module[^>]*\bsrc=", html, re.I)
    srcs = re.findall(r"<script\b[^>]*\bsrc=[\"']([^\"']+)", html, re.I)
    assert all(s.startswith("https://cdn.jsdelivr.net/npm/") for s in srcs), srcs
    mods = build.inline_modules(html)
    assert len(mods) == 1
    code = mods[0]
    assert not re.search(r"""\bimport\b[^;()]*\bfrom\s*["']\./""", code)
    assert not re.search(r"""\bimport\(\s*["']\./""", code)
    if page == "index.html":
        assert not re.search(r"<!doctype|<html\b|<head\b|<body\b", html, re.I)
        assert html.lstrip().startswith("<title>")
        assert '<meta name="claude:viewport-fit-sides" content="cover">' in html
    else:
        assert html.lstrip().lower().startswith("<!doctype html>")
    if NODE:
        f = tmp_path / "page.mjs"
        f.write_text(code, encoding="utf-8")
        r = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        # module bodies evaluate without a DOM (the entry, which needs the page, is left out)
        f.write_text(code.split("// ---- entry")[0], encoding="utf-8")
        r = subprocess.run([NODE, str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


def test_only_worker_js_published(built):
    _, sizes = built
    assert [p for p in sizes if p.endswith((".js", ".css")) and not p.startswith("pyodide/")] == \
        [p for p in build.SEPARATE_FILES if (build.PAGES / p).is_file()]
    assert "js/engine.worker.js" in sizes


def test_worker_is_bundled(built, tmp_path):
    out, _ = built
    code = (out / "js" / "engine.worker.js").read_text(encoding="utf-8")
    assert not re.search(r"""\bimport\b[^;()`]*\bfrom\s*["']\./""", code)
    assert "installFetchShim(m, base)" in code and "function installFetchShim" in code
    if NODE:
        f = tmp_path / "worker.mjs"
        f.write_text(code, encoding="utf-8")
        r = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
