"""Korean font registration for matplotlib (Malgun Gothic), idempotent."""
from __future__ import annotations

from pathlib import Path

import matplotlib
from matplotlib import font_manager

matplotlib.use("Agg")

FONT_FILES = ("C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/malgunbd.ttf")
_done = False


def setup_fonts() -> None:
    global _done
    matplotlib.rcParams["axes.unicode_minus"] = False
    if _done:
        return
    found = False
    for f in FONT_FILES:
        if Path(f).exists():
            font_manager.fontManager.addfont(f)
            found = True
    if found:
        matplotlib.rcParams["font.family"] = "Malgun Gothic"
    _done = True
