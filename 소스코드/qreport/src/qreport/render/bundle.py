"""Chart PNG rendering shared by the Word/PDF/ZIP outputs, and the PNG ZIP."""
from __future__ import annotations

import io
import zipfile

import matplotlib.pyplot as plt

from qreport.render.charts import build_charts, to_png


def render_chart_pngs(result, dpi: int = 300) -> dict[str, bytes]:
    """Render every ChartSpec once; key -> PNG bytes (insertion order = build_charts order)."""
    out = {}
    for key, spec in build_charts(result).items():
        out[key] = to_png(spec, dpi=dpi)
        plt.close(spec.fig)
    return out


def render_png_zip(result, pngs: dict[str, bytes] | None = None, data_label: str | None = None) -> bytes:
    """data_label (e.g. "가상 데이터 — 체험용 샘플") adds README.txt with that label; None = PNGs only."""
    pngs = render_chart_pngs(result) if pngs is None else pngs
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for i, (key, data) in enumerate(pngs.items(), 1):
            z.writestr(f"{i:02d}_{key}.png", data)
        if data_label:
            z.writestr("README.txt", f"{data_label}\n이 묶음의 차트는 실제 기업 데이터가 아닌 가상 데이터로 "
                                     "만들었습니다.\n".encode("utf-8"))
    return buf.getvalue()
