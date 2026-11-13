"""Korean font setup for Pyodide (no Malgun Gothic there): register the bundled OFL font
(NanumGothic Regular/Bold) with Matplotlib and with ReportLab under qreport's PDF font names
"Malgun"/"Malgun-Bold", so qreport renders Korean text unchanged. Call setup(font_dir) once before
analysis/rendering. Idempotent; qreport itself is not modified.
"""
from __future__ import annotations

from pathlib import Path

DEFAULT_DIR = "/home/pyodide/fonts"
_family: str | None = None
PDF_GLYPHS = {"p̄": "p-bar", "β": "beta", "Σ": "Sum ", "−": "-", "̄": ""}


def _pick(font_dir: Path, style: str) -> Path:
    found = sorted(font_dir.glob(f"*-{style}.ttf"))
    if not found:
        raise FileNotFoundError(f"no *-{style}.ttf in {font_dir}")
    return found[0]


def setup(font_dir: str = DEFAULT_DIR) -> str:
    """Returns the Matplotlib font family now in use."""
    global _family
    if _family is not None:
        return _family
    d = Path(font_dir)
    reg, bold = _pick(d, "Regular"), _pick(d, "Bold")

    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import font_manager
    from qreport.render import fonts as qfonts
    for f in (reg, bold):
        font_manager.fontManager.addfont(str(f))
    family = font_manager.FontProperties(fname=str(reg)).get_name()
    # DejaVu Sans (bundled with Matplotlib) as per-glyph fallback: NanumGothic has no Greek (β)
    matplotlib.rcParams["font.family"] = [family, "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    qfonts._done = True                    # qreport's setup_fonts() must not reset the family

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from qreport.render import pdf_report
    pdfmetrics.registerFont(TTFont(pdf_report.REG, str(reg)))
    pdfmetrics.registerFont(TTFont(pdf_report.BOLD, str(bold)))
    pdfmetrics.registerFontFamily(pdf_report.REG, normal=pdf_report.REG, bold=pdf_report.BOLD,
                                  italic=pdf_report.REG, boldItalic=pdf_report.BOLD)
    # NanumGothic has no β, Σ, − or combining macron and ReportLab has no glyph fallback: extend
    # qreport's PDF text substitution (keys already there keep their replacement, e.g. p̄ -> p-bar)
    for a, b in PDF_GLYPHS.items():
        pdf_report._GLYPHS.setdefault(a, b)
    _family = family
    return family
