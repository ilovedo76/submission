"""Assemble web/publish/: the artifact's files, with a SELF-HOSTED Pyodide runtime.

    qreport\\.venv\\Scripts\\python web\\build.py [out_dir]       (default web/publish)

Layout (all URLs relative to the publish root):
  index.html, mvp.html                     from web/pages, self-contained: CSS inlined, page JS bundled
                                           into one inline module (inline_page / bundle)
  js/engine.worker.js                      the Web Worker (own file, its imports bundled in)
  *.b64.<k>.txt                            .whl/.zip/.docx/.xlsx are not served by the host: each is
                                           published as base64 text parts, listed in manifest.binaries
                                           and decoded by js/binfetch.js (fetch shim in the worker)
  files.json, files-<n>.json               {path: contentType} lists for publishing (in batches)
  pyodide/                                 core runtime (from web/node_modules/pyodide, exact version),
                                           trimmed pyodide-lock.json and every wheel of the
                                           PY_PACKAGES dependency closure (cached in web/vendor/)
  wheels/*.whl                             MICROPIP_PACKAGES wheels from PyPI (cached in web/vendor/)
  py/qreport/**, py/web_entry.py, py/web_fonts.py
  fonts/*.ttf + fonts/OFL.txt              OFL Korean font for Matplotlib/ReportLab
  sample/가상_샘플_데이터.xlsx              virtual sample (make_sample)
  manifest.json                            read by the page's engine

The Pyodide version and package lists are parsed from web/tools/pyodide_env.mjs, so the browser loads
exactly the set the Node harness tests. The company workbook and seed never enter the publish folder.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path

WEB = Path(__file__).resolve().parent
REPO = WEB.parent
ENV_MJS = WEB / "tools" / "pyodide_env.mjs"
NODE_PYODIDE = WEB / "node_modules" / "pyodide"
VENDOR = WEB / "vendor"
PAGES = WEB / "pages"
FONTS = WEB / "fonts"
QREPORT_SRC = REPO / "qreport" / "src" / "qreport"
DEFAULT_OUT = WEB / "publish"

CORE_FILES = ("pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip")
CDN = "https://cdn.jsdelivr.net/pyodide/v{version}/full/"
SAMPLE_REL = "sample/가상_샘플_데이터.xlsx"
EXCLUDE_TOP = {"app", "store"}          # Streamlit UI and sqlite store never ship (= pyodide_env.mjs)
KEEP_EXT = {".py", ".docx"}
MB = 1024 * 1024
FILE_LIMIT = 15 * MB

# The artifact host serves no archives/documents: these go out as base64 text parts (manifest.binaries)
ENCODE_EXT = {".whl", ".zip", ".docx", ".xlsx"}
PART_RAW = 3 * 3_499_999                 # raw bytes per part (multiple of 3) -> 13,999,996 base64 chars
CONTENT_TYPES = {
    ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".mjs": "text/javascript",
    ".wasm": "application/wasm", ".json": "application/json", ".txt": "text/plain", ".py": "text/plain",
    ".md": "text/markdown", ".csv": "text/csv", ".xml": "application/xml", ".ttf": "font/ttf",
    ".woff": "font/woff", ".woff2": "font/woff2", ".png": "image/png", ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
}
BATCH_BYTES = 60_000_000
BATCH_FILES = 250
FILE_LIST_RE = re.compile(r"files(-\d+)?\.json")


# ------------------------------------------------------------------ inputs
def _js_list(src: str, name: str) -> list[str]:
    m = re.search(rf"export const {name}\s*=\s*\[(.*?)\];", src, re.S)
    if not m:
        raise ValueError(f"{name} not found in {ENV_MJS}")
    body = re.sub(r"//[^\n]*", "", m.group(1))
    return re.findall(r'"([^"]+)"', body)


def pyodide_env() -> dict:
    src = ENV_MJS.read_text(encoding="utf-8")
    version = re.search(r'export const PYODIDE_VERSION\s*=\s*"([^"]+)"', src).group(1)
    return {"version": version, "packages": _js_list(src, "PY_PACKAGES"),
            "micropip": _js_list(src, "MICROPIP_PACKAGES")}


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _download(url: str, dest: Path, sha256: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"download {url}")
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError(f"sha256 mismatch for {url}")
    dest.write_bytes(data)


def _closure(lock: dict, roots: list[str]) -> list[str]:
    pk, seen, stack = lock["packages"], set(), list(roots)
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        if n not in pk:
            raise KeyError(f"package {n!r} not in pyodide-lock.json")
        seen.add(n)
        stack += pk[n]["depends"]
    return sorted(seen)


def _pyodide_wheel(version: str, pkg: dict) -> Path:
    """Wheel from node_modules/pyodide (Node's package cache) or web/vendor, else the CDN once."""
    for d in (NODE_PYODIDE, VENDOR / f"pyodide-{version}"):
        p = d / pkg["file_name"]
        if p.is_file() and _sha256(p) == pkg["sha256"]:
            return p
    p = VENDOR / f"pyodide-{version}" / pkg["file_name"]
    _download(CDN.format(version=version) + pkg["file_name"], p, pkg["sha256"])
    return p


def _pypi_wheel(spec: str) -> Path:
    name, version = spec.split("==")
    d = VENDOR / "wheels"
    hits = sorted(d.glob(f"{name.replace('-', '_')}-{version}-*.whl")) if d.is_dir() else []
    if hits:
        return hits[0]
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=60) as r:
        meta = json.load(r)
    wheels = [u for u in meta["urls"] if u["filename"].endswith("-none-any.whl")]
    if not wheels:
        raise ValueError(f"{spec}: no pure-Python wheel on PyPI")
    u = wheels[0]
    p = d / u["filename"]
    _download(u["url"], p, u["digests"]["sha256"])
    return p


# ------------------------------------------------------------------ build
def _copy(src: Path, out: Path, rel: str) -> str:
    dest = out / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return rel


def _qreport_files() -> list[tuple[Path, str]]:
    out = []
    for p in sorted(QREPORT_SRC.rglob("*")):
        rel = p.relative_to(QREPORT_SRC)
        if not p.is_file() or "__pycache__" in rel.parts or p.suffix not in KEEP_EXT:
            continue
        if len(rel.parts) > 1 and rel.parts[0] in EXCLUDE_TOP:
            continue
        out.append((p, rel.as_posix()))
    return out


# ------------------------------------------------------------------ page inlining + tiny ES-module bundler
# The artifact CSP may block same-origin stylesheets and module imports from the page, so each page
# gets its CSS as <style> and its module graph as one inline <script type="module">. Each module
# becomes a block `{ ... }` (own scope, top-level await still allowed) that fills a frozen namespace
# object; static imports become destructuring of that object, dynamic import("./x.js") becomes
# Promise.resolve(namespace), new URL("./f", import.meta.url) is re-rooted at the page.
PAGE_FILES = ("index.html", "mvp.html")
SEPARATE_FILES = ("js/engine.worker.js",)     # Web Worker scripts load from own files

_RE_IMPORT_NAMED = re.compile(r"""\bimport\s*\{([^}]*)\}\s*from\s*["']([^"']+)["']\s*;?""")
_RE_IMPORT_STAR = re.compile(r"""\bimport\s*\*\s*as\s+(\w+)\s+from\s*["']([^"']+)["']\s*;?""")
_RE_IMPORT_OTHER = re.compile(r"""^\s*import\s+(?:[\w$]+\s*(?:,\s*[{*][^;]*?)?from\s*["']|["'])""", re.M)
_RE_DYN = re.compile(r"""\bimport\(\s*["'](\.{1,2}/[^"']+)["']\s*\)""")
_RE_META_URL = re.compile(r"""new URL\(\s*["'](\.{1,2}/[^"']+)["']\s*,\s*import\.meta\.url\s*\)""")
_RE_EXPORT_DECL = re.compile(r"^export\s+((?:async\s+)?function\*?|const|let|var|class)\s+([\w$]+)", re.M)
_RE_EXPORT_LIST = re.compile(r"^export\s*\{([^}]*)\}\s*;?", re.M)


def _resolve(spec: str, from_dir: str) -> str:
    """Posix path relative to the pages root."""
    if not spec.startswith("."):
        raise ValueError(f"bare module specifier {spec!r} cannot be bundled")
    parts = [p for p in from_dir.split("/") if p]
    for seg in spec.split("/"):
        if seg == "..":
            parts.pop()
        elif seg not in (".", ""):
            parts.append(seg)
    return "/".join(parts)


def _ns(rel: str) -> str:
    return "__mod_" + re.sub(r"\W", "_", rel)


def _bindings(spec_list: str) -> str:
    out = []
    for item in (s.strip() for s in spec_list.split(",")):
        if item:
            a, _, b = item.partition(" as ")
            out.append(f"{a.strip()}: {b.strip()}" if b else a.strip())
    return ", ".join(out)


def _transform(code: str, from_dir: str, deps: list[str],
               wanted: list[tuple[str, str]] | None = None) -> tuple[str, list[str]]:
    def dep(spec):
        rel = _resolve(spec, from_dir)
        deps.append(rel)
        return _ns(rel)

    def named(m):
        ns = dep(m.group(2))
        if wanted is not None:
            wanted.extend((deps[-1], b.split(":")[0].strip()) for b in _bindings(m.group(1)).split(", ") if b)
        return f"const {{ {_bindings(m.group(1))} }} = {ns};"

    code = _RE_IMPORT_NAMED.sub(named, code)
    code = _RE_IMPORT_STAR.sub(lambda m: f"const {m.group(1)} = {dep(m.group(2))};", code)
    if _RE_IMPORT_OTHER.search(code):
        raise ValueError(f"unsupported import form in {from_dir or '.'}: {_RE_IMPORT_OTHER.search(code).group(0)!r}")
    code = _RE_DYN.sub(lambda m: f"Promise.resolve({dep(m.group(1))})", code)
    code = _RE_META_URL.sub(lambda m: f'new URL("./{_resolve(m.group(1), from_dir)}", import.meta.url)', code)
    names = [m.group(2) for m in _RE_EXPORT_DECL.finditer(code)]
    code = _RE_EXPORT_DECL.sub(lambda m: f"{m.group(1)} {m.group(2)}", code)
    for m in _RE_EXPORT_LIST.finditer(code):
        for a, _, b in (s.strip().partition(" as ") for s in m.group(1).split(",") if s.strip()):
            names.append(f"{b.strip()}: {a.strip()}" if b else a.strip())
    code = _RE_EXPORT_LIST.sub("", code)
    if re.search(r"^export\b", code, re.M) or "import.meta" in _RE_META_URL.sub("", code):
        raise ValueError(f"unsupported export / import.meta use in {from_dir or '.'}")
    return code, names


def bundle(entry_code: str, entry_dir: str = "") -> str:
    """Inline module code for an entry (page script or module file) and every module it imports."""
    done: dict[str, str] = {}
    order: list[str] = []
    exported: dict[str, set[str]] = {}
    wanted: list[tuple[str, str]] = []

    def visit(rel: str, stack: tuple = ()):
        if rel in done:
            return
        if rel in stack:
            raise ValueError(f"import cycle: {' -> '.join(stack + (rel,))}")
        deps: list[str] = []
        code, names = _transform((PAGES / rel).read_text(encoding="utf-8"), rel.rpartition("/")[0], deps, wanted)
        exported[rel] = {n.split(":")[0].strip() for n in names}
        for d in deps:
            visit(d, stack + (rel,))
        exports = ", ".join(names)
        done[rel] = (f"// ---- {rel}\nconst {_ns(rel)} = {{}};\n{{\n{code}\n"
                     f"Object.assign({_ns(rel)}, {{ {exports} }}); Object.freeze({_ns(rel)});\n}}\n")
        order.append(rel)

    deps: list[str] = []
    entry, _ = _transform(entry_code, entry_dir, deps, wanted)
    for d in deps:
        visit(d)
    missing = [f"{rel}: {name}" for rel, name in wanted if name not in exported[rel]]
    if missing:
        raise ValueError(f"imported names not exported: {missing}")
    out = "".join(done[r] for r in order) + f"// ---- entry\n{{\n{entry}\n}}\n"
    if re.search(r"</script", out, re.I):
        raise ValueError("bundled code contains '</script'")
    return out


_RE_CSS_LINK = re.compile(r"""<link\b[^>]*rel=["']?stylesheet["']?[^>]*>""", re.I)
_RE_SCRIPT = re.compile(r"""<script\b([^>]*)>(.*?)</script>""", re.I | re.S)


def inline_page(page: Path) -> str:
    html = page.read_text(encoding="utf-8")

    def css(m):
        href = re.search(r"""href=["']([^"']+)["']""", m.group(0)).group(1)
        if re.match(r"https?:", href):
            return m.group(0)
        return f"<style>\n{(PAGES / _resolve('./' + href.removeprefix('./'), '')).read_text(encoding='utf-8')}\n</style>"

    def script(m):
        attrs, body = m.group(1), m.group(2)
        if not re.search(r"""type=["']?module""", attrs):
            return m.group(0)
        src = re.search(r"""src=["']([^"']+)["']""", attrs)
        if src:
            if re.match(r"https?:", src.group(1)):
                return m.group(0)
            body = f'import * as __entry from "./{_resolve("./" + src.group(1).removeprefix("./"), "")}";'
        return f'<script type="module">\n{bundle(body)}</script>'

    return _RE_SCRIPT.sub(script, _RE_CSS_LINK.sub(css, html))


def inline_modules(html: str) -> list[str]:
    """Code of every inline <script type="module"> in a page (for checks)."""
    return [m.group(2) for m in _RE_SCRIPT.finditer(html) if re.search(r"""type=["']?module""", m.group(1))
            and "src=" not in m.group(1)]


def _is_test(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return ".test." in name or name.startswith("test_") or "tests/" in rel


def _clear(d: Path) -> None:
    """Delete every file under d, then the empty folders where Windows allows it (OneDrive or an
    editor can hold a folder handle; a leftover empty folder does not matter for the publish)."""
    for p in sorted(d.rglob("*"), key=lambda p: -len(p.parts)):
        if p.is_file() or p.is_symlink():
            p.unlink()
    for p in sorted((p for p in d.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        try:
            p.rmdir()
        except OSError:
            pass


def build(out: Path = DEFAULT_OUT) -> dict[str, int]:
    out = Path(out)
    if out.exists():
        if any(out.iterdir()) and not ((out / "manifest.json").exists() or (out / "pyodide").is_dir()):
            raise ValueError(f"{out} is not empty and is not a previous publish folder; refusing to delete it")
        _clear(out)
    out.mkdir(parents=True, exist_ok=True)
    env = pyodide_env()
    version = env["version"]

    # Pyodide core + trimmed lock + package wheels
    node_ver = json.loads((NODE_PYODIDE / "package.json").read_text(encoding="utf-8"))["version"]
    if node_ver != version:
        raise ValueError(f"web/node_modules/pyodide is {node_ver}, pinned {version}; run npm ci in web/")
    for f in CORE_FILES:
        _copy(NODE_PYODIDE / f, out, f"pyodide/{f}")
    lock = json.loads((NODE_PYODIDE / "pyodide-lock.json").read_text(encoding="utf-8"))
    names = _closure(lock, env["packages"])
    for n in names:
        pkg = lock["packages"][n]
        _copy(_pyodide_wheel(version, pkg), out, f"pyodide/{pkg['file_name']}")
    trimmed = {**lock, "packages": {n: lock["packages"][n] for n in names}}
    (out / "pyodide" / "pyodide-lock.json").write_text(json.dumps(trimmed, indent=1), encoding="utf-8")

    wheels = [_copy(w, out, f"wheels/{w.name}") for w in map(_pypi_wheel, env["micropip"])]

    # Python sources
    python = [_copy(src, out, f"py/qreport/{rel}") for src, rel in _qreport_files()]
    python += [_copy(WEB / "py" / f, out, f"py/{f}") for f in ("web_entry.py", "web_fonts.py")]

    # fonts + license
    fonts = [_copy(f, out, f"fonts/{f.name}") for f in sorted(FONTS.glob("*.ttf"))]
    font_license = _copy(FONTS / "OFL.txt", out, "fonts/OFL.txt")

    # sample
    sys.path.insert(0, str(WEB / "sample"))
    from make_sample import make_sample
    (out / SAMPLE_REL).parent.mkdir(parents=True, exist_ok=True)
    (out / SAMPLE_REL).write_bytes(make_sample())

    # pages: self-contained HTML (CSS inlined, page JS bundled inline); the worker stays a file
    for name in PAGE_FILES:
        if (PAGES / name).is_file():
            (out / name).write_text(inline_page(PAGES / name), encoding="utf-8")
    for rel in SEPARATE_FILES:          # worker: bundled too (its imports inlined), still its own file
        if (PAGES / rel).is_file():
            code = bundle((PAGES / rel).read_text(encoding="utf-8"), rel.rpartition("/")[0])
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            (out / rel).write_text(code, encoding="utf-8")

    python_files = [{"path": p, "size": (out / p).stat().st_size} for p in python]

    # archives the host will not serve -> base64 text parts
    binaries = {}
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.suffix.lower() in ENCODE_EXT:
            rel = p.relative_to(out).as_posix()
            binaries[rel] = encode_binary(out, rel)

    manifest = {
        "pyodideVersion": version,
        "indexURL": "pyodide/",
        "packages": env["packages"],
        "wheels": wheels,
        "python": python_files,
        "entry": "py/web_entry.py",
        "setup": "py/web_fonts.py",
        "fonts": fonts,
        "fontLicense": font_license,
        "sample": SAMPLE_REL,
        "binaries": binaries,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    sizes = {p.relative_to(out).as_posix(): p.stat().st_size for p in sorted(out.rglob("*")) if p.is_file()}
    write_file_lists(out, sizes)
    return {p.relative_to(out).as_posix(): p.stat().st_size for p in sorted(out.rglob("*")) if p.is_file()}


def encode_binary(out: Path, rel: str) -> list[str]:
    """out/rel -> out/<rel>.b64.<k>.txt parts (each <= PART_CHARS), original removed."""
    src = out / rel
    data = src.read_bytes()
    parts = []
    for k, i in enumerate(range(0, max(len(data), 1), PART_RAW)):
        part = f"{rel}.b64.{k}.txt"
        (out / part).write_bytes(base64.b64encode(data[i:i + PART_RAW]))
        parts.append(part)
    src.unlink()
    return parts


def content_type(rel: str) -> str:
    ext = Path(rel).suffix.lower()
    if ext not in CONTENT_TYPES:
        raise ValueError(f"{rel}: extension {ext!r} is not served by the artifact host")
    return CONTENT_TYPES[ext]


def write_file_lists(out: Path, sizes: dict[str, int]) -> None:
    """files.json = {path: contentType} for every published file except index.html (the page itself)
    and these lists; if the total exceeds BATCH_BYTES, also files-1.json / files-2.json (... -N), each
    <= BATCH_BYTES and <= BATCH_FILES, for publishing in batches to the same artifact."""
    rels = [r for r in sizes if r != "index.html" and not FILE_LIST_RE.fullmatch(r)]
    (out / "files.json").write_text(json.dumps({r: content_type(r) for r in rels}, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    if sum(sizes[r] for r in rels) <= BATCH_BYTES and len(rels) <= BATCH_FILES:
        return
    batches: list[list[str]] = [[]]
    used = [0]
    for r in sorted(rels, key=lambda r: -sizes[r]):          # first-fit decreasing
        for i in range(len(batches)):
            if used[i] + sizes[r] <= BATCH_BYTES and len(batches[i]) < BATCH_FILES:
                batches[i].append(r)
                used[i] += sizes[r]
                break
        else:
            batches.append([r])
            used.append(sizes[r])
    for i, b in enumerate(batches, 1):
        (out / f"files-{i}.json").write_text(
            json.dumps({r: content_type(r) for r in sorted(b)}, ensure_ascii=False, indent=1), encoding="utf-8")


def report(sizes: dict[str, int]) -> str:
    total = sum(sizes.values())
    big = [f"  OVER 15 MB: {p} ({s / MB:.2f} MB)" for p, s in sizes.items() if s > FILE_LIMIT]
    top = sorted(sizes.items(), key=lambda kv: -kv[1])[:8]
    lines = [f"{len(sizes)} files, total {total / MB:.2f} MB"] + big + ["largest:"]
    lines += [f"  {s / MB:7.2f} MB  {p}" for p, s in top]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    print(report(build(target)))
