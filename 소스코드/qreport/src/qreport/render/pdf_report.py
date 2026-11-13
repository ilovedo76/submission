"""PDF report (ReportLab platypus), same content as the Word report."""
from __future__ import annotations

import io
from xml.sax.saxutils import escape

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

from qreport.notes import Notes
from qreport.render.bundle import render_chart_pngs
from qreport.render.content import build_content

REG, BOLD = "Malgun", "Malgun-Bold"
IMG_W = 16 * cm
MAX_IMG_H = 21 * cm
NAVY = colors.HexColor("#1F3864")
GRID = colors.HexColor("#9AA3B2")
HEAD_BG = colors.HexColor("#E6EAF2")


def _register_fonts() -> None:
    if REG in pdfmetrics.getRegisteredFontNames():
        return
    pdfmetrics.registerFont(TTFont(REG, "C:/Windows/Fonts/malgun.ttf"))
    pdfmetrics.registerFont(TTFont(BOLD, "C:/Windows/Fonts/malgunbd.ttf"))
    pdfmetrics.registerFontFamily(REG, normal=REG, bold=BOLD, italic=REG, boldItalic=BOLD)


def _styles() -> dict[str, ParagraphStyle]:
    base = dict(fontName=REG, fontSize=9.5, leading=14, wordWrap="CJK")
    return {
        "body": ParagraphStyle("body", **base),
        "bullet": ParagraphStyle("bullet", leftIndent=12, bulletIndent=2, spaceAfter=2, **base),
        "cell": ParagraphStyle("cell", **{**base, "fontSize": 8.5, "leading": 11}),
        "head": ParagraphStyle("head", **{**base, "fontName": BOLD, "fontSize": 8.5, "leading": 11}),
        "h1": ParagraphStyle("h1", **{**base, "fontName": BOLD, "fontSize": 15, "leading": 20,
                                      "textColor": NAVY, "spaceBefore": 6, "spaceAfter": 8}),
        "h2": ParagraphStyle("h2", **{**base, "fontName": BOLD, "fontSize": 11, "leading": 15,
                                      "textColor": NAVY, "spaceBefore": 8, "spaceAfter": 4, "keepWithNext": 1}),
        "verdict": ParagraphStyle("verdict", **{**base, "fontName": BOLD, "fontSize": 13, "leading": 18,
                                                "spaceAfter": 6}),
        "title": ParagraphStyle("title", **{**base, "fontName": BOLD, "fontSize": 26, "leading": 34,
                                            "alignment": TA_CENTER, "textColor": NAVY}),
        "sub": ParagraphStyle("sub", **{**base, "fontSize": 14, "leading": 20, "alignment": TA_CENTER}),
    }


# glyphs missing from Malgun Gothic -> plain equivalents
_GLYPHS = {"p̄": "p-bar", "≈": "~", "−": "-", "≤": "<=", "≥": ">=", "̄": ""}


def _safe(text) -> str:
    text = str(text)
    for a, b in _GLYPHS.items():
        text = text.replace(a, b)
    return escape(text)


def _p(text, style) -> Paragraph:
    return Paragraph(_safe(text), style)


def _table(headers, rows, widths, st, header_rows: int = 1) -> Table:
    data = [[_p(h, st["head"]) for h in headers]]
    data += [[_p(c, st["cell"]) for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=header_rows)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, GRID), ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _fit(total: float, fractions: list[float]) -> list[float]:
    return [total * f for f in fractions]


def _image(data: bytes, width: float = IMG_W) -> Image:
    w, h = PILImage.open(io.BytesIO(data)).size
    height = width * h / w
    if height > MAX_IMG_H:
        width, height = width * MAX_IMG_H / height, MAX_IMG_H
    return Image(io.BytesIO(data), width=width, height=height)


def _footer(canvas, doc, data_label: str | None = None) -> None:
    canvas.saveState()
    canvas.setFont(REG, 8)
    canvas.setFillColor(colors.grey)
    canvas.drawCentredString(A4[0] / 2, 1.0 * cm, f"- {doc.page} -")
    if data_label:
        canvas.setFont(BOLD, 9)
        canvas.setFillColor(colors.black)
        canvas.drawCentredString(A4[0] / 2, A4[1] - 1.2 * cm, data_label)
    canvas.restoreState()


def render_pdf(result, notes: Notes, doc_no: str, pngs: dict[str, bytes] | None = None,
               data_label: str | None = None) -> bytes:
    """data_label (e.g. "가상 데이터 — 체험용 샘플") marks the cover and every page header; None = no mark."""
    pngs = render_chart_pngs(result) if pngs is None else pngs
    _register_fonts()
    st = _styles()
    c = build_content(result, notes, doc_no)
    W = A4[0] - 4 * cm
    f: list = []

    def h1(text, new_page=True):
        if new_page:
            f.append(PageBreak())
        f.append(_p(text, st["h1"]))

    def h2(text):
        f.append(_p(text, st["h2"]))

    def img(key, width=IMG_W):
        f.append(_image(pngs[key], width))
        f.append(Spacer(1, 6))

    # cover
    if data_label:
        f += [Spacer(1, 2 * cm), _p(data_label, st["verdict"])]
    f += [Spacer(1, 2 * cm if data_label else 4 * cm), _p("시장 품질 리포트", st["title"]), Spacer(1, 6),
          _p("냉장고 현장 품질 분석 보고서", st["sub"]), Spacer(1, 1.5 * cm)]
    info = Table([[_p(k, st["head"]), _p(v, st["body"])] for k, v in
                  (("문서번호", c["doc_no"]), ("분석 기간", c["period"]), ("기준월", c["base"]))],
                 colWidths=_fit(W, [0.3, 0.7]))
    info.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, GRID), ("BACKGROUND", (0, 0), (0, -1), HEAD_BG),
                              ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    sign = Table([[_p(h, st["head"]) for h in ("작성", "검토", "승인")], ["", "", ""],
                  [_p("서명/일자", st["cell"])] * 3],
                 colWidths=_fit(W, [1 / 3] * 3), rowHeights=[None, 1.6 * cm, None])
    sign.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, GRID), ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG)]))
    f += [info, Spacer(1, 1.2 * cm), sign]

    # 1
    h1("1. 한눈에 보기")
    f.append(_p(c["verdict"], st["verdict"]))
    for line in c["comments"]:
        f.append(Paragraph(_safe(line), st["bullet"], bulletText="•"))
    h2("핵심 지표 (정의는 부록 참조)")
    f.append(_table(["지표", "값", "설명"], [[r["name"], r["value"], r["note"]] for r in c["headline"]],
                    _fit(W, [0.15, 0.30, 0.55]), st))
    f.append(Spacer(1, 4))
    f.append(_p(f"예상 추가 접수: {c['forecast']}    예상 품질 비용: {c['cost']}", st["body"]))
    f.append(Spacer(1, 6))
    img("trend", 12 * cm)
    h2("모델별 1MIS (최근 생산월)")
    f.append(_table(["모델", "생산월", "불량(건)", "생산량", "1MIS", "관리상한", "판정"],
                    [[r[k] for k in ("model", "month", "x", "n", "p", "ucl", "judge")] for r in c["model_rows"]],
                    _fit(W, [0.17, 0.13, 0.11, 0.15, 0.14, 0.14, 0.16]), st))
    h2("모델 × 유형 불량 건수 (최근 12개월)")
    f.append(_table(c["type_headers"], [[r[f"c{i}"] for i in range(7)] for r in c["type_rows"]],
                    _fit(W, [0.16] + [0.13] * 5 + [0.19]), st))

    h1("2. 기술통계")
    img("histogram")
    img("boxplot")
    h1("3. 파레토 (유형 순위)")
    img("pareto")
    h1("4. 모델 × 유형 매트릭스", new_page=False)
    img("model_type")

    h1("5. P 관리도")
    img("pchart")
    for m in c["models"]:
        img(f"pchart_{m}")
    h2("검정 결과 (전체)")
    f.append(_table(["생산월", "불량(건)", "생산량", "1MIS", "관리상한", "판정"],
                    [[r[k] for k in ("month", "x", "n", "p", "ucl", "judge")] for r in c["pchart_rows"]],
                    _fit(W, [0.13, 0.11, 0.15, 0.13, 0.13, 0.35]), st))

    h1("6. MIS·와이블")
    img("mis_curve")
    img("heatmap")
    img("weibull")
    f.append(_table(["구간 / 모델", "β", "95% 신뢰구간", "유형"],
                    [[r[k] for k in ("name", "beta", "ci", "kind")] for r in c["beta_rows"]],
                    _fit(W, [0.38, 0.12, 0.2, 0.3]), st))
    f.append(Spacer(1, 4))
    f.append(_p(f"추가 접수 예측: {c['forecast']}  (예상 비용: {c['cost']})", st["body"]))

    h1("7. 전년 대비")
    img("yoy")
    f.append(_table(["구분", "대상", "전년 건수", "전년 불량률", "최근 건수", "최근 불량률", "배수", "p값", "판정"],
                    [[r[k] for k in ("kind", "group", "x0", "p0", "x1", "p1", "ratio", "pvalue", "judge")]
                     for r in c["yoy_rows"]],
                    _fit(W, [0.08, 0.13, 0.09, 0.12, 0.09, 0.12, 0.09, 0.09, 0.19]), st))

    h1("8. 중점 관리 이슈")
    for line in c["issues"].split("\n"):
        f.append(_p(line, st["body"]))
    h2("9. 개선 계획")
    f.append(_table(["담당", "일정", "대책"], [[r["담당"], r["일정"], r["대책"]] for r in c["plan_rows"]],
                    _fit(W, [0.2, 0.2, 0.6]), st))

    h1("부록. 분석 기준")
    for title, key in (("수식", "formulas"), ("분모 정의", "denoms"), ("핵심 지표 정의", "defs")):
        h2(title)
        for line in c[key]:
            f.append(Paragraph(_safe(line), st["bullet"], bulletText="•"))
    h2("재현 정보")
    for line in (f"기준월: {c['base']}", f"데이터 {c['validation']}", f"입력 파일 SHA-256: {c['sha']}",
                 f"템플릿 버전: {c['template_version']}", f"룰 세트(JSON): {c['rules_json']}"):
        f.append(_p(line, st["body"]))

    def page(canvas, doc):
        _footer(canvas, doc, data_label)

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm,
                      bottomMargin=2 * cm, title=f"시장 품질 리포트 {doc_no}").build(
        f, onFirstPage=page, onLaterPages=page)
    return buf.getvalue()
