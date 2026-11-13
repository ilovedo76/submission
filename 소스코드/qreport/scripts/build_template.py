"""Generate src/qreport/templates/report.docx (python-docx + docxtpl Jinja tags).

Run from qreport/: python scripts/build_template.py
Replace the generated file with a company layout later; keep the tag names (see render/content.py).
"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

FONT = "Malgun Gothic"
OUT = Path(__file__).resolve().parent.parent / "src" / "qreport" / "templates" / "report.docx"


def set_font(style, name: str = FONT) -> None:
    style.font.name = name
    rpr = style.element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        style.font.name = name
        rf = rpr.find(qn("w:rFonts"))
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        if rf.get(qn(attr)) is not None:
            del rf.attrib[qn(attr)]
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rf.set(qn(attr), name)


def build() -> Document:
    doc = Document()
    s = doc.sections[0]
    s.page_width, s.page_height = Cm(21.0), Cm(29.7)
    s.left_margin = s.right_margin = Cm(2.0)
    s.top_margin = s.bottom_margin = Cm(2.0)

    for name, size, color in (("Normal", 10, None), ("Title", 26, "1F3864"),
                              ("Heading 1", 16, "1F3864"), ("Heading 2", 12, "2F5496")):
        st = doc.styles[name]
        set_font(st)
        st.font.size = Pt(size)
        if color:
            st.font.color.rgb = RGBColor.from_string(color)
            st.font.bold = True
    doc.styles["Normal"].paragraph_format.space_after = Pt(4)

    def para(text, style=None, align=None, page_break=False, bold=False, size=None):
        p = doc.add_paragraph(text, style=style)
        if align:
            p.alignment = align
        if page_break:
            p.paragraph_format.page_break_before = True
        if bold or size:
            for r in p.runs:
                r.bold = bold or None
                if size:
                    r.font.size = Pt(size)
        return p

    def table(headers, fields, rows_var, loop="r"):
        t = doc.add_table(rows=3, cols=len(headers))
        t.style = "Table Grid"
        for c, h in zip(t.rows[0].cells, headers):
            c.text = h
            for r in c.paragraphs[0].runs:
                r.bold = True
        t.rows[1].cells[0].text = "{%tr for " + loop + " in " + rows_var + " %}"
        for c, f in zip(t.rows[2].cells, fields):
            c.text = "{{ " + loop + "." + f + " }}"
        t.add_row().cells[0].text = "{%tr endfor %}"   # rows: header, loop-open, data, loop-close
        doc.add_paragraph("")
        return t

    def dyn_table(header_vars, fields, rows_var):
        t = table([""] * len(fields), fields, rows_var)
        for c, hv in zip(t.rows[0].cells, header_vars):
            c.text = "{{ " + hv + " }}"
            for r in c.paragraphs[0].runs:
                r.bold = True

    def image(var):
        para("{{ " + var + " }}", align=WD_ALIGN_PARAGRAPH.CENTER)

    # --- cover
    for _ in range(4):
        para("")
    para("시장 품질 리포트", "Title", WD_ALIGN_PARAGRAPH.CENTER)
    para("냉장고 현장 품질 분석 보고서", align=WD_ALIGN_PARAGRAPH.CENTER, size=14)
    para("")
    t = doc.add_table(rows=3, cols=2)
    t.style = "Table Grid"
    for row, (k, v) in zip(t.rows, (("문서번호", "{{ doc_no }}"), ("분석 기간", "{{ period }}"),
                                    ("기준월", "{{ base }}"))):
        row.cells[0].text, row.cells[1].text = k, v
    para("")
    t = doc.add_table(rows=3, cols=3)
    t.style = "Table Grid"
    for c, h in zip(t.rows[0].cells, ("작성", "검토", "승인")):
        c.text = h
        c.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    t.rows[1].height = Cm(1.6)
    for c in t.rows[2].cells:
        c.text = "서명/일자"

    # --- 1
    para("1. 한눈에 보기", "Heading 1", page_break=True)
    para("{{ verdict }}", bold=True, size=13)
    para("{%p for c in comments %}")
    para("{{ c }}", style="List Bullet")
    para("{%p endfor %}")
    para("핵심 지표 (정의는 부록 참조)", "Heading 2")
    table(["지표", "값", "설명"], ["name", "value", "note"], "headline")
    para("예상 추가 접수: {{ forecast }}    예상 품질 비용: {{ cost }}")
    image("img_trend")
    para("모델별 1MIS (최근 생산월)", "Heading 2")
    table(["모델", "생산월", "불량(건)", "생산량", "1MIS", "관리상한", "판정"],
          ["model", "month", "x", "n", "p", "ucl", "judge"], "model_rows")
    para("모델 × 유형 불량 건수 (최근 12개월)", "Heading 2")
    dyn_table([f"type_headers[{i}]" for i in range(7)], [f"c{i}" for i in range(7)], "type_rows")

    # --- 2..4
    para("2. 기술통계", "Heading 1", page_break=True)
    image("img_histogram")
    image("img_boxplot")
    para("3. 파레토 (유형 순위)", "Heading 1", page_break=True)
    image("img_pareto")
    para("4. 모델 × 유형 매트릭스", "Heading 1")
    image("img_model_type")

    # --- 5
    para("5. P 관리도", "Heading 1", page_break=True)
    image("img_pchart")
    para("{%p for it in model_pcharts %}")
    para("{{ it.img }}", align=WD_ALIGN_PARAGRAPH.CENTER)
    para("{%p endfor %}")
    para("검정 결과 (전체)", "Heading 2")
    table(["생산월", "불량(건)", "생산량", "1MIS", "관리상한", "판정"],
          ["month", "x", "n", "p", "ucl", "judge"], "pchart_rows")

    # --- 6
    para("6. MIS·와이블", "Heading 1", page_break=True)
    image("img_mis_curve")
    image("img_heatmap")
    image("img_weibull")
    table(["구간 / 모델", "β", "95% 신뢰구간", "유형"], ["name", "beta", "ci", "kind"], "beta_rows")
    para("추가 접수 예측: {{ forecast }}  (예상 비용: {{ cost }})")

    # --- 7
    para("7. 전년 대비", "Heading 1", page_break=True)
    image("img_yoy")
    table(["구분", "대상", "전년 건수", "전년 불량률", "최근 건수", "최근 불량률", "배수", "p값", "판정"],
          ["kind", "group", "x0", "p0", "x1", "p1", "ratio", "pvalue", "judge"], "yoy_rows")

    # --- 8, 9
    para("8. 중점 관리 이슈", "Heading 1", page_break=True)
    para("{{ issues }}")
    para("9. 개선 계획", "Heading 1")
    table(["담당", "일정", "대책"], ["담당", "일정", "대책"], "plan_rows")

    # --- appendix
    para("부록. 분석 기준", "Heading 1", page_break=True)
    para("수식", "Heading 2")
    para("{%p for l in formulas %}")
    para("{{ l }}", style="List Bullet")
    para("{%p endfor %}")
    para("분모 정의", "Heading 2")
    para("{%p for l in denoms %}")
    para("{{ l }}", style="List Bullet")
    para("{%p endfor %}")
    para("핵심 지표 정의", "Heading 2")
    para("{%p for l in defs %}")
    para("{{ l }}", style="List Bullet")
    para("{%p endfor %}")
    para("재현 정보", "Heading 2")
    para("기준월: {{ base }}")
    para("데이터 {{ validation }}")
    para("입력 파일 SHA-256: {{ sha }}")
    para("템플릿 버전: {{ template_version }}")
    para("룰 세트(JSON): {{ rules_json }}")
    return doc


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    build().save(OUT)
    print(f"wrote {OUT}")
