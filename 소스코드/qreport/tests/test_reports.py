import io
import time
import zipfile

import docx
import pypdf
import pytest

from qreport.notes import Notes
from qreport.render.bundle import render_chart_pngs, render_png_zip
from qreport.render.charts import build_charts
from qreport.render.docx_report import TEMPLATE_VERSION, render_docx
from qreport.render.pdf_report import render_pdf


@pytest.fixture(scope="module")
def pngs(company_result):
    return render_chart_pngs(company_result)


def _docx_text(b: bytes) -> tuple[docx.Document, str]:
    doc = docx.Document(io.BytesIO(b))
    parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        parts += [c.text for r in t.rows for c in r.cells]
    return doc, "\n".join(parts)


def test_docx_contents(company_result, pngs):
    b = render_docx(company_result, Notes(issues="현장 확인 중"), "QR-2512-01", pngs=pngs)
    doc = docx.Document(io.BytesIO(b))
    text = "\n".join(p.text for p in doc.paragraphs)
    for s in ["종합 판정: 개선 필요", "8. 중점 관리 이슈", "현장 확인 중", "9. 개선 계획", "부록",
              company_result.input_hash[:12], "(가설)"]:
        assert s in text
    assert len(doc.inline_shapes) >= 10
    assert company_result.input_hash in text and TEMPLATE_VERSION == "2"
    dups = company_result.dataset.summary["duplicates"]
    assert f"중복 의심 {dups}건" in text


def test_docx_tables_and_plan(company_result, pngs):
    notes = Notes(plan=[{"담당": "품질팀", "일정": "26.01", "대책": "로트 추적"}])
    _, text = _docx_text(render_docx(company_result, notes, "QR-1", pngs=pngs))
    for s in ["품질팀", "로트 추적", "수준", "검정1 상한 초과", "초기 고장형", "약 101~114건"]:
        assert s in text
    assert "{{" not in text and "{%" not in text


def test_pdf_korean_and_pages(company_result, pngs):
    b = render_pdf(company_result, Notes(issues="현장 확인 중"), "QR-2512-01", pngs=pngs)
    r = pypdf.PdfReader(io.BytesIO(b))
    text = "\n".join(p.extract_text() for p in r.pages)
    assert len(r.pages) >= 8
    for s in ["종합 판정", "9. 개선 계획", "현장 확인 중"]:
        assert s in text


def test_png_zip(company_result, pngs):
    z = zipfile.ZipFile(io.BytesIO(render_png_zip(company_result, pngs)))
    names = z.namelist()
    assert len(names) == len(build_charts(company_result))
    assert all(n.endswith(".png") for n in names)
    assert all(z.read(n)[:8] == b"\x89PNG\r\n\x1a\n" for n in names)


def test_cost_line_without_as_cost(company_result, pngs):
    _, text = _docx_text(render_docx(company_result, Notes(), "QR-1", pngs=pngs))
    assert "건당 처리비 입력 시 산출" in text
    assert "(작성 전)" in text


def test_render_under_30s(company_result):
    t = time.time()
    p = render_chart_pngs(company_result)
    render_docx(company_result, Notes(), "QR-1", pngs=p)
    render_pdf(company_result, Notes(), "QR-1", pngs=p)
    render_png_zip(company_result, p)
    assert time.time() - t < 30


def test_pdf_validation_line(company_result, pngs):
    r = pypdf.PdfReader(io.BytesIO(render_pdf(company_result, Notes(), "QR-1", pngs=pngs)))
    text = "\n".join(p.extract_text() for p in r.pages)
    assert f"중복 의심 {company_result.dataset.summary['duplicates']}건" in text


def test_pdf_empty_notes(company_result, pngs):
    r = pypdf.PdfReader(io.BytesIO(render_pdf(company_result, Notes(), "QR-1", pngs=pngs)))
    assert "(작성 전)" in "\n".join(p.extract_text() for p in r.pages)


def test_short_history_docx_and_pdf():
    from qreport.ingest.sheets import DEFAULT_DICTIONARY
    from qreport.pipeline import analyze
    from qreport.ruleset import DEFAULT_RULES
    from tests.synth import make_workbook

    r = analyze([("s.xlsx", make_workbook(months=14))], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    p = render_chart_pngs(r, dpi=50)
    assert render_docx(r, Notes(), "QR-S", pngs=p)[:2] == b"PK"
    assert render_pdf(r, Notes(), "QR-S", pngs=p)[:4] == b"%PDF"


def test_model_judgement_matches_test1(company_result):
    from qreport.render.content import model_1mis_rows
    rows = {r["model"]: r["judge"] for r in model_1mis_rows(company_result)}
    for m, pc in company_result.metrics.pchart_by_model.items():
        last = pc.df.iloc[-1]["prod_month"]
        assert (rows[m] == "상한 초과") == (last in pc.test1)


LABEL = "가상 데이터 — 체험용 샘플"


@pytest.fixture(scope="module")
def synth_result():
    from qreport.ingest.sheets import DEFAULT_DICTIONARY
    from qreport.pipeline import analyze
    from qreport.ruleset import DEFAULT_RULES
    from tests.synth import make_workbook

    r = analyze([("s.xlsx", make_workbook(months=24))], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    return r, render_chart_pngs(r, dpi=50)


def test_data_label_docx_cover_and_header(synth_result):
    r, p = synth_result
    doc = docx.Document(io.BytesIO(render_docx(r, Notes(), "QR-S", pngs=p, data_label=LABEL)))
    body = [x.text for x in doc.paragraphs]
    title = next(i for i, x in enumerate(doc.paragraphs) if x.style.name == "Title")
    assert LABEL in body[:title + 3]
    assert all(LABEL in "\n".join(x.text for x in s.header.paragraphs) for s in doc.sections)
    plain = docx.Document(io.BytesIO(render_docx(r, Notes(), "QR-S", pngs=p)))
    assert LABEL not in "\n".join(x.text for x in plain.paragraphs)
    assert all(LABEL not in "\n".join(x.text for x in s.header.paragraphs) for s in plain.sections)


def test_data_label_pdf_cover_and_every_page(synth_result):
    r, p = synth_result
    pages = pypdf.PdfReader(io.BytesIO(render_pdf(r, Notes(), "QR-S", pngs=p, data_label=LABEL))).pages
    assert all("가상 데이터" in pg.extract_text() for pg in pages)
    plain = pypdf.PdfReader(io.BytesIO(render_pdf(r, Notes(), "QR-S", pngs=p))).pages
    assert not any("가상 데이터" in pg.extract_text() for pg in plain)


def test_data_label_zip_readme(synth_result):
    r, p = synth_result
    z = zipfile.ZipFile(io.BytesIO(render_png_zip(r, p, data_label=LABEL)))
    assert LABEL in z.read("README.txt").decode("utf-8")
    assert sum(n.endswith(".png") for n in z.namelist()) == len(p)
    assert "README.txt" not in zipfile.ZipFile(io.BytesIO(render_png_zip(r, p))).namelist()
