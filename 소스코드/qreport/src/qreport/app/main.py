"""한 화면 앱: 업로드 -> 판정 -> 경고 카드 -> 차트 -> 작성란 -> 다운로드. Layout only; logic lives in viewmodel."""
from __future__ import annotations

import html
import traceback
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from qreport.app.common import get_store
from qreport.app.viewmodel import (
    PLAN_COLS, VERDICT_STYLE, alert_cards, analysis_key, as_cost_value, base_options, df_to_plan,
    doc_no_from_files, plan_to_df, review_rows, total_diffs, validation_line,
)
from qreport.ingest.sheets import RawInput
from qreport.notes import Notes
from qreport.pipeline import InputError, analyze, input_hash
from qreport.render.bundle import render_png_zip
from qreport.render.charts import build_charts, to_png
from qreport.render.content import cost_text, forecast_text
from qreport.render.docx_report import render_docx
from qreport.render.pdf_report import render_pdf
from qreport.app.theme import apply_theme, chip_html, md_safe, verdict_html
from qreport.rules.comments import change_text, comment_lines

FIELD_KO = {"model": "모델", "prod_month": "생산월", "occur_month": "발생월",
            "defect_type": "불량 유형", "use_months": "사용개월"}
ROLE_KO = {"production": "생산량", "sales": "판매량", "defects": "불량 내역(RawData)", "ignored": "무시"}
CACHE_LIMIT = 6


def _ym(p: pd.Period) -> str:
    return p.strftime("%y.%m")


def _remember(name: str, key, make):
    """Tiny session-level cache: name -> {key: value}, oldest entries dropped."""
    box = st.session_state.setdefault(name, {})
    if key not in box:
        if len(box) >= CACHE_LIMIT:
            box.pop(next(iter(box)))
        box[key] = make()
    return box[key]


# ------------------------------------------------------------------ callbacks
SCROLL_TO_CHART_JS = """<script>/* __NONCE__ */
(function go(n) {
  const el = window.parent.document.querySelector('.st-key-chart_box');
  if (el) { el.scrollIntoView({behavior: 'smooth', block: 'center'}); }
  else if (n < 20) { setTimeout(function () { go(n + 1); }, 100); }
})(0);
</script>"""


def _set_chart(key: str) -> None:
    st.session_state["chart"] = key
    st.session_state["model_sel"] = "전체"
    st.session_state["chart_scroll"] = st.session_state.get("chart_scroll", 0) + 1


def _load_job(store, job_id: int, base_month: str, rule_version: int) -> None:
    ss = st.session_state
    ss["files"] = store.load_job_files(job_id)
    ss["overrides"] = {}
    ss["pinned_rv"] = rule_version   # reproduce the job with the rules it was made with
    ss["base_sel"] = pd.Period(base_month, "M")
    up = ss.get("uploader")   # keep the uploader's current files from overriding the loaded job
    ss["upload_sig"] = input_hash([(f.name, f.getvalue()) for f in up]) if up else None


def _save_as_cost(store) -> None:
    v = st.session_state.get("as_cost_in") or 0
    store.set_setting("as_cost", str(int(v)))


# ------------------------------------------------------------------ sections
def _sidebar_inputs(store):
    ss = st.session_state
    ss.setdefault("files", [])
    ss.setdefault("overrides", {})
    with st.sidebar:
        st.markdown("#### 엑셀 올리기")
        up = st.file_uploader("RawData·생산량·판매량 엑셀 (여러 파일 가능)", type=["xlsx", "xlsm"],
                              accept_multiple_files=True, key="uploader")
        if up:
            files = [(f.name, f.getvalue()) for f in up]
            sig = input_hash(files)
            if ss.get("upload_sig") != sig:
                ss["files"], ss["upload_sig"], ss["overrides"] = files, sig, {}
                ss.pop("base_sel", None)
                ss.pop("pinned_rv", None)   # a new upload uses the current rules
        base_slot = st.container()
        if "as_cost_in" not in ss:
            ss["as_cost_in"] = int(as_cost_value(store.get_setting("as_cost")) or 0)
        st.number_input("AS 처리비 (원/건, 비우면 0)", min_value=0, step=1000, key="as_cost_in",
                        on_change=_save_as_cost, args=(store,),
                        help="0이면 비용 추정을 표시하지 않습니다.")
        st.markdown("#### 지난 작업")
        jobs = store.list_jobs()[:8]
        if not jobs:
            st.caption("아직 저장된 작업이 없습니다.")
        for j in jobs:
            took = "" if j["elapsed_min"] is None else f" · 작성 {j['elapsed_min']:.0f}분"
            label = f"#{j['id']} · 기준 {j['base_month']} · {j['verdict'] or '-'}{took}"
            st.button(label, key=f"job_{j['id']}", on_click=_load_job,
                      args=(store, j["id"], j["base_month"], j["rule_version"]), width="stretch")
    return ss["files"], base_slot


def _mapping_form(store, ri: RawInput) -> None:
    st.warning("엑셀의 시트 역할이나 열 이름을 자동으로 알 수 없습니다. 아래에서 지정해 주세요.")
    with st.form("mapping_form"):
        choices = []
        for i, req in enumerate(ri.needs_mapping):
            st.markdown(f"**{req.file} / {req.sheet}**")
            st.caption("열: " + ", ".join(req.columns))
            if req.missing_fields == ["role"]:
                role = st.selectbox("시트 역할", ["production", "sales", "ignored"],
                                    format_func=ROLE_KO.get, key=f"role_{i}")
                cols = {}
            else:
                role = st.selectbox("시트 역할", ["defects", "ignored"],
                                    format_func=ROLE_KO.get, key=f"role_{i}")
                cols = {}
                for f in req.missing_fields:
                    c = st.selectbox(f"'{FIELD_KO.get(f, f)}'에 해당하는 열", ["(선택 안 함)"] + req.columns,
                                     key=f"col_{i}_{f}")
                    if c != "(선택 안 함)":
                        cols[c] = f
            choices.append((req, role, cols))
        add = st.checkbox("열 이름 사전에 추가", value=True, key="map_add_alias")
        ok = st.form_submit_button("확정하고 다시 분석", type="primary")
    if ok:
        ov = st.session_state["overrides"]
        roles, columns = ov.setdefault("roles", {}), ov.setdefault("columns", {})
        for req, role, cols in choices:
            roles[(req.file, req.sheet)] = role
            if cols:
                columns.setdefault((req.file, req.sheet), {}).update(cols)
                if add:
                    for col, f in cols.items():
                        store.add_alias(col, f)
        st.rerun()


def _validation(res) -> None:
    ds = res.dataset
    st.markdown(validation_line(ds.summary, res.reports))
    rows, diffs = review_rows(ds), total_diffs(res.reports)
    if len(rows) or diffs:
        with st.expander("검토·제외 행과 Total 차이 보기"):
            for r in diffs:
                st.markdown(f"- {r.file} / {r.sheet}: {r.reason}")
            if len(rows):
                st.dataframe(rows, hide_index=True, width="stretch")


def _verdict_box(res) -> None:
    st.markdown(verdict_html(res.verdict, len(res.alerts), forecast_text(res), cost_text(res),
                             VERDICT_STYLE[res.verdict]), unsafe_allow_html=True)
    h = res.headline
    if h.change_pct is not None:
        st.caption(f"최근 12개월 시장 불량률 {h.level * 100:.3f}% (전년 대비 {change_text(h.change_pct)})")
    else:
        st.caption(f"최근 12개월 시장 불량률 {h.level * 100:.3f}%")


def _alert_cards(res) -> None:
    if not res.alerts:
        st.markdown("관리 기준을 벗어난 항목이 없습니다.")
        return
    for i, c in enumerate(alert_cards(res.alerts, comment_lines(res))):
        with st.container(border=True):
            left, right = st.columns([6, 1], vertical_alignment="center")
            left.markdown(
                f'{chip_html(c.icon, c.label, c.color, c.background)}'
                f'<b>{html.escape(c.rule)}</b> · {html.escape(c.target)}<br>{md_safe(c.text)}',
                unsafe_allow_html=True)
            right.button("근거 차트 보기", key=f"card_{i}", on_click=_set_chart, args=(c.chart_key,))


def _charts_for(res, rv: int, targets: dict, model: str | None) -> dict:
    key = (res.input_hash, rv, str(res.base), model, tuple(sorted(targets.items())))

    def make():
        out = {}
        for k, spec in build_charts(res, model, targets).items():
            out[k] = (spec.title, list(spec.ref_lines), to_png(spec, dpi=120))
            plt.close(spec.fig)
        return out
    return _remember("charts", key, make)


def _chart_section(res, rv: int, targets: dict) -> None:
    ss = st.session_state
    st.subheader("차트")
    model = st.selectbox("모델", ["전체"] + list(res.dataset.models), key="model_sel")
    with st.spinner("차트를 그리는 중..."):
        charts = _charts_for(res, rv, targets, None if model == "전체" else model)
    keys = list(charts)
    if ss.get("chart") not in keys:
        ss["chart"] = keys[0]
    st.radio("차트 선택", keys, key="chart", horizontal=True, format_func=lambda k: charts[k][0])
    _, refs, png = charts[ss["chart"]]
    with st.container(key="chart_box"):   # theme.py caps the image height so it fits one screen
        st.image(png, width="stretch")
        for line in refs:
            st.caption(line)
    nonce = ss.get("chart_scroll", 0)
    if nonce != ss.get("chart_scrolled", 0):   # a "근거 차트 보기" click: bring the chart into view
        ss["chart_scrolled"] = nonce
        components.html(SCROLL_TO_CHART_JS.replace("__NONCE__", str(nonce)), height=0)


def _plan_add(jid: int) -> None:
    rows = st.session_state[f"plan_rows_{jid}"]
    rows.append(max(rows, default=-1) + 1)


def _plan_del(jid: int, rid: int) -> None:
    ss = st.session_state
    ss[f"plan_rows_{jid}"] = [r for r in ss[f"plan_rows_{jid}"] if r != rid] or [rid + 1]
    for c in PLAN_COLS:
        ss.pop(f"plan_{jid}_{rid}_{c}", None)


def _plan_inputs(jid: int) -> list[dict]:
    """개선 계획 rows as plain text inputs. (st.data_editor's floating cell editor is
    positioned once against the window and does not scroll with the page.)"""
    ss = st.session_state
    st.markdown("개선 계획 (9장)")
    widths = [2, 2, 5, 1]
    for i, rid in enumerate(ss[f"plan_rows_{jid}"]):
        cols = st.columns(widths, vertical_alignment="bottom")
        for col, c in zip(cols, PLAN_COLS):
            col.text_input(c, key=f"plan_{jid}_{rid}_{c}",
                           label_visibility="visible" if i == 0 else "collapsed")
        cols[3].button("삭제", key=f"plan_del_{jid}_{rid}", on_click=_plan_del, args=(jid, rid))
    st.button("＋ 행 추가", key=f"plan_add_{jid}", on_click=_plan_add, args=(jid,))
    rows = [{c: str(ss.get(f"plan_{jid}_{rid}_{c}", "")) for c in PLAN_COLS}
            for rid in ss[f"plan_rows_{jid}"]]
    return df_to_plan(plan_to_df(rows))


def _notes_section(store, jid: int) -> Notes:
    ss = st.session_state
    saved_key = f"notes_saved_{jid}"
    rows_key = f"plan_rows_{jid}"
    if saved_key not in ss:
        ss[saved_key] = store.load_notes(jid)
        saved_plan = ss[saved_key].plan
        ss[rows_key] = list(range(max(len(saved_plan), 1)))   # stable row ids
        for rid, item in enumerate(saved_plan):
            for c in PLAN_COLS:
                ss[f"plan_{jid}_{rid}_{c}"] = item.get(c, "")
    st.subheader("작성란")
    issues = st.text_area("이슈 (8장)", value=ss[saved_key].issues, key=f"issues_{jid}", height=140)
    plan = _plan_inputs(jid)
    notes = Notes(issues=issues, plan=plan)
    if notes != ss[saved_key]:
        store.save_notes(jid, notes)
        ss[saved_key] = notes
    return notes


def _downloads(store, res, rv: int, targets: dict, jid: int, notes: Notes, files: list) -> None:
    st.subheader("내려받기")
    doc_no = doc_no_from_files(files, res.base)
    base_key = (res.input_hash, rv, str(res.base), tuple(sorted(targets.items())))

    def make_pngs():
        out = {}
        for k, spec in build_charts(res, None, targets).items():
            out[k] = to_png(spec, dpi=300)
            plt.close(spec.fig)
        return out
    pngs = _remember("report_pngs", base_key, make_pngs)   # script thread: pyplot/session safe

    box = st.session_state.setdefault("blobs", {})          # captured here; callables never touch st
    nkey = (notes.issues, repr(notes.plan), doc_no)
    notes = Notes(issues=notes.issues, plan=[dict(p) for p in notes.plan])
    makers = {
        "docx": lambda: render_docx(res, notes, doc_no, pngs),
        "pdf": lambda: render_pdf(res, notes, doc_no, pngs),
        "zip": lambda: render_png_zip(res, pngs),
    }
    mimes = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             "pdf": "application/pdf", "zip": "application/zip"}
    labels = {"docx": "Word 보고서", "pdf": "PDF 보고서", "zip": "차트 PNG 묶음(ZIP)"}
    for col, kind in zip(st.columns(3), ("docx", "pdf", "zip")):
        def data(kind=kind):   # built only when the file is requested, then cached
            k = (kind, *base_key, nkey)
            if k not in box:
                box[k] = makers[kind]()
            return box[k]
        col.download_button(labels[kind], data, file_name=f"{doc_no}.{kind}", mime=mimes[kind],
                            key=f"dl_{kind}", on_click=store.record_download, args=(jid, kind),
                            width="stretch", type="primary")


# ------------------------------------------------------------------ page
def analysis_page() -> None:
    ss = st.session_state
    store = get_store()
    files, base_slot = _sidebar_inputs(store)   # may clear pinned_rv on a new upload
    rv, rules = store.current_rules()
    if ss.get("pinned_rv") is not None and ss["pinned_rv"] != rv:
        rv, rules = ss["pinned_rv"], store.rules_by_version(ss["pinned_rv"])
    dictionary, targets = store.dictionary(), store.model_targets()
    as_cost = as_cost_value(str(ss.get("as_cost_in", 0)))

    st.title("시장 품질 리포트")
    if not files:
        st.markdown("### 분석할 엑셀 파일을 왼쪽에서 올려 주세요")
        st.markdown("RawData·생산량·판매량 엑셀을 그대로 올리면 판정, 차트, Word/PDF 보고서가 한 화면에서 나옵니다.")
        return

    ihash = input_hash(files)
    ov_repr = repr(sorted((ss["overrides"] or {}).items(), key=repr)) + repr(sorted(dictionary.items()))

    def run(base):
        key = analysis_key(ihash, rv, base, targets, as_cost, ov_repr)
        return _remember("analysis", key, lambda: analyze(
            files, base, rules, dictionary, as_cost, ss["overrides"] or None, targets))

    try:
        with st.spinner("분석 중..."):
            res = run(None)
            if isinstance(res, RawInput):
                _mapping_form(store, res)
                return
            options = base_options(res.dataset.first_month, res.dataset.last_occur_month)
            if ss.get("base_sel") not in options:
                ss["base_sel"] = options[0]
            with base_slot:
                st.selectbox("기준월", options, key="base_sel", format_func=_ym)
            if ss["base_sel"] != res.base:
                res = run(ss["base_sel"])
    except InputError as e:   # the files themselves need fixing: plain Korean message only
        st.error(str(e).replace("\n", "  \n"))   # markdown line breaks
        return
    except Exception as e:   # surface a readable message instead of a traceback
        st.error(f"분석 중 오류가 발생했습니다: {e}")
        with st.expander("자세한 오류 내용"):
            st.code(traceback.format_exc())
        return

    jobs = ss.setdefault("jobs", {})
    jobkey = (res.input_hash, rv, str(res.base))
    if jobkey not in jobs:
        jid = store.find_job_by_hash(res.input_hash, rv, str(res.base))
        jobs[jobkey] = (jid, True) if jid is not None else (store.save_job(res, files, rv), False)
    jid, reused = jobs[jobkey]
    if reused:
        st.caption(f"지난 작업 #{jid} 불러옴")

    st.caption(f"기준월 {_ym(res.base)} · 룰 v{rv}")
    _validation(res)
    _verdict_box(res)
    _alert_cards(res)
    _chart_section(res, rv, targets)
    notes = _notes_section(store, jid)
    _downloads(store, res, rv, targets, jid, notes, files)


pages = [
    st.Page(analysis_page, title="분석", default=True, url_path="analysis"),
    st.Page(str(Path(__file__).with_name("settings.py")), title="설정", url_path="settings"),
]
st.set_page_config(page_title="시장 품질 리포트", layout="wide")
apply_theme()   # before navigation so both pages get it
st.navigation(pages).run()
