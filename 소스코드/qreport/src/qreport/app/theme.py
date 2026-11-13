"""강렬한레드 look for the Streamlit screens: CSS on top of .streamlit/config.toml, plus HTML blocks.

Tokens (from the design system): paper #e9e7e0, paper-deep #dddcd7, red #cf2e2a (planes and 24px+ type),
red-ink #b3241f (small red text), on-red #f6f4ee, ink #363434, muted #6b645c, line #c9c6bd.
"""
from __future__ import annotations

import html

import streamlit as st

CSS = """
<style>
:root {
  --paper: #e9e7e0; --paper-deep: #dddcd7; --red: #cf2e2a; --red-ink: #b3241f; --red-deep: #8f1b17;
  --on-red: #f6f4ee; --ink: #363434; --muted: #6b645c; --line: #c9c6bd;
  --display: 'Bithumb Gothic', 'Noto Sans KR', 'Malgun Gothic', system-ui, sans-serif;
}
/* the display face (bundled, bold only): titles, labels, the verdict plane, chips, buttons */
[data-testid="stMainBlockContainer"] :is(h1, h2, h3), [data-testid="stSidebar"] h4, .qr-label, .qr-verdict,
.qr-chip, .stButton button, .stDownloadButton button, .stFormSubmitButton button,
[data-testid="stWidgetLabel"] p, [data-testid="stSidebarNav"] a span { font-family: var(--display); }
/* layout: everything sits on the 48px margin line, left aligned */
[data-testid="stMainBlockContainer"] { padding: 48px 48px 96px; max-width: 1280px; }
@media (max-width: 800px) { [data-testid="stMainBlockContainer"] { padding: 24px 24px 48px; } }
[data-testid="stHeader"] { background: transparent; }

/* type: big red display against tiny uppercase labels */
[data-testid="stMainBlockContainer"] h1 {
  font-size: clamp(40px, 6vw, 56px); line-height: 0.95; font-weight: 800; letter-spacing: -0.02em;
  color: var(--red); text-transform: uppercase; padding: 0 0 8px;
}
[data-testid="stMainBlockContainer"] h2, [data-testid="stMainBlockContainer"] h3 {
  font-size: 24px; line-height: 1.1; font-weight: 800; letter-spacing: -0.01em; color: var(--red);
  text-transform: uppercase; border-top: 1px solid var(--line); padding: 16px 0 8px; margin-top: 32px;
}
[data-testid="stSidebar"] h4, .qr-label {
  font-size: 11px; line-height: 14px; font-weight: 700; letter-spacing: 0.04em; color: var(--red-ink);
  text-transform: uppercase;
}
[data-testid="stSidebar"] h4 { border-top: 1px solid var(--line); padding: 12px 0 4px; }
[data-testid="stCaptionContainer"] { color: var(--muted); }
[data-testid="stWidgetLabel"] p { font-size: 12px; font-weight: 700; color: var(--ink); }

/* square, flat: no radius on planes, no shadows anywhere */
[data-testid="stVerticalBlockBorderWrapper"], [data-testid="stExpander"] details, [data-testid="stAlert"],
[data-testid="stAlertContainer"], [data-testid="stImage"] img, [data-testid="stFileUploaderDropzone"],
[data-testid="stForm"], [data-testid="stDataFrame"] { border-radius: 0 !important; box-shadow: none !important; }
[data-testid="stFileUploaderDropzone"] { background: var(--paper); border: 1px dashed var(--line); }
hr { border-color: var(--line) !important; }

/* the evidence chart fits one screen: cap its height, keep the aspect ratio */
.st-key-chart_box [data-testid="stImage"] img {
  max-height: 62vh; width: auto !important; max-width: 100%; object-fit: contain;
}

/* buttons: primary is a red plane, secondary is paper with a hairline */
.stButton button, .stDownloadButton button, .stFormSubmitButton button {
  font-weight: 700; letter-spacing: 0.02em; box-shadow: none !important;
}
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] {
  background: var(--red); border-color: var(--red); color: var(--on-red);
}
[data-testid="stBaseButton-primary"]:hover, [data-testid="stBaseButton-primaryFormSubmit"]:hover {
  background: var(--red-deep); border-color: var(--red-deep); color: var(--on-red);
}
[data-testid="stBaseButton-secondary"] { background: var(--paper); border-color: var(--line); color: var(--ink); }
[data-testid="stBaseButton-secondary"]:hover { border-color: var(--red-ink); color: var(--red-ink); }
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"],
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] div { justify-content: flex-start; text-align: left; }
button:focus-visible, input:focus-visible, textarea:focus-visible { outline: 2px solid var(--red-deep); outline-offset: 2px; }

/* verdict: one full color plane, label small on top, the word huge at the bottom-left */
.qr-verdict { padding: 24px 24px 20px; margin: 8px 0 12px; }
.qr-verdict .qr-label { color: inherit; }
.qr-verdict-word {
  font-size: clamp(56px, 9vw, 96px); line-height: 0.9; font-weight: 800; letter-spacing: -0.02em;
  margin: 48px 0 20px;
}
.qr-stats { display: flex; flex-wrap: wrap; gap: 8px 48px; border-top: 1px solid currentColor; padding-top: 12px; }
.qr-stats div { font-size: 24px; line-height: 1.1; font-weight: 800; letter-spacing: -0.01em; }
.qr-stats b {
  display: block; font-size: 11px; line-height: 14px; font-weight: 700; letter-spacing: 0.04em; margin-bottom: 4px;
}
.qr-chip { font-size: 12px; font-weight: 700; padding: 2px 8px; margin-right: 6px; white-space: nowrap; }
</style>
"""


def apply_theme() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def md_safe(text: str) -> str:
    """Escape for HTML inside st.markdown; a pair of '~' would otherwise strike through the text between."""
    return html.escape(text).replace("~", "&#126;")


def verdict_html(verdict: str, n_alerts: int, forecast: str, cost: str, style: tuple[str, str, str]) -> str:
    """The verdict plane. style = (mark, text color, plane color)."""
    mark, fg, bg = style
    e = md_safe
    return (
        f'<div class="qr-verdict" style="background:{bg};color:{fg}">'
        f'<div class="qr-label">{mark} 종합 판정 · 경고 {n_alerts}건</div>'
        f'<div class="qr-verdict-word">{e(verdict)}</div>'
        f'<div class="qr-stats"><div><b>예상 추가 접수 범위</b> {e(forecast)}</div>'
        f'<div><b>예상 비용</b> {e(cost)}</div></div></div>')


def chip_html(mark: str, label: str, fg: str, bg: str) -> str:
    return f'<span class="qr-chip" style="background:{bg};color:{fg}">{mark} {html.escape(label)}</span>'
