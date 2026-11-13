"""설정 페이지: 룰 값, 모델별 목표 불량률, 열 이름 사전. Runs as a script (st.Page path)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from qreport.app.common import get_store
from qreport.ingest.sheets import FIELDS
from qreport.ruleset import RuleSet

FIELD_LABELS = {"model": "model (모델)", "prod_month": "prod_month (생산월)",
                "occur_month": "occur_month (발생월)", "defect_type": "defect_type (불량 유형)",
                "use_months": "use_months (사용개월)"}


def _rules_form(store) -> None:
    version, cur = store.current_rules()
    st.subheader("룰 값")
    st.caption(f"현재 룰 버전: v{version}. 저장하면 새 버전이 만들어지고, 이전 작업은 그 당시 버전으로 남습니다.")
    with st.form("rules_form"):
        c1, c2, c3 = st.columns(3)
        ucl = c1.number_input("관리상한 σ (ucl_sigma)", 0.5, 10.0, float(cur.ucl_sigma), 0.5, key="r_ucl")
        minc = c2.number_input("최소 건수 (min_count)", 0, 100, int(cur.min_count), 1, key="r_min")
        run = c3.number_input("연속 개월 (run_length)", 2, 36, int(cur.run_length), 1, key="r_run")
        sr = c1.number_input("유형 급증 배수 (surge_ratio)", 1.0, 20.0, float(cur.surge_ratio), 0.1, key="r_sr")
        sm = c2.number_input("유형 급증 최소 건수 (surge_min)", 0, 1000, int(cur.surge_min), 1, key="r_sm")
        al = c3.number_input("유의수준 (alpha)", 0.001, 0.5, float(cur.alpha), 0.01, format="%.3f", key="r_al")
        od = c1.number_input("산포 초과 σz (overdispersion_sigma_z)", 0.0, 10.0,
                             float(cur.overdispersion_sigma_z), 0.1, key="r_od")
        lr = c2.number_input("수준 상승 배수 (level_rise_ratio)", 1.0, 20.0,
                             float(cur.level_rise_ratio), 0.1, key="r_lr")
        basis = c3.selectbox("히트맵 기준 (heatmap_basis)", ["max", "mean"],
                             index=["max", "mean"].index(cur.heatmap_basis), key="r_basis")
        st.markdown("와이블 구간 개월 (seg_months)")
        s1, s2, s3 = st.columns(3)
        segs = (s1.number_input("구간 1", 1, 24, int(cur.seg_months[0]), 1, key="r_s1"),
                s2.number_input("구간 2", 1, 24, int(cur.seg_months[1]), 1, key="r_s2"),
                s3.number_input("구간 3", 1, 24, int(cur.seg_months[2]), 1, key="r_s3"))
        saved = st.form_submit_button("룰 저장 (새 버전)", type="primary")
    if saved:
        new = RuleSet(ucl_sigma=ucl, min_count=int(minc), run_length=int(run), surge_ratio=sr,
                      surge_min=int(sm), alpha=al, overdispersion_sigma_z=od, level_rise_ratio=lr,
                      heatmap_basis=basis, seg_months=tuple(int(x) for x in segs),
                      prior_months=cur.prior_months)   # comparison periods are fixed at 12 months
        if new == cur:
            st.info(f"변경된 값이 없어 v{version}을 그대로 사용합니다.")
        else:
            v = store.save_rules(new)
            st.success(f"저장했습니다. 새 룰 버전: v{v}")


def _targets_editor(store) -> None:
    st.subheader("모델별 목표 불량률")
    st.caption("최근 12개월 시장 불량률 목표(%)입니다. '전체'를 적으면 전체 목표가 됩니다. 비우면 삭제됩니다.")
    cur = store.model_targets()
    df = pd.DataFrame({"모델": list(cur), "목표 불량률(%)": [round(v * 100, 4) for v in cur.values()]})
    edited = st.data_editor(df, num_rows="dynamic", key="targets_editor", hide_index=True,
                            column_config={"목표 불량률(%)": st.column_config.NumberColumn(
                                min_value=0.0, max_value=100.0, format="%.3f")})
    if st.button("목표 저장", key="targets_save"):
        new = {}
        for r in edited.to_dict("records"):
            name, pct = str(r.get("모델") or "").strip(), r.get("목표 불량률(%)")
            if name and pct is not None and not pd.isna(pct):
                new[name] = float(pct) / 100
        for name in set(cur) - set(new):
            store.set_model_target(name, None)
        for name, rate in new.items():
            store.set_model_target(name, rate)
        st.success(f"목표 {len(new)}건 저장")


def _dictionary_editor(store) -> None:
    st.subheader("열 이름 사전")
    st.caption("엑셀 열 제목이 바뀌어도 인식되도록 별칭을 등록합니다.")
    cur = store.dictionary()
    df = pd.DataFrame({"열 이름": list(cur), "항목": [FIELD_LABELS[f] for f in cur.values()]})
    edited = st.data_editor(
        df, num_rows="dynamic", key="dict_editor", hide_index=True,
        column_config={"항목": st.column_config.SelectboxColumn(
            options=[FIELD_LABELS[f] for f in FIELDS], required=True)})
    if st.button("사전 저장", key="dict_save"):
        back = {v: k for k, v in FIELD_LABELS.items()}
        new = {str(r["열 이름"]).strip(): back[r["항목"]] for r in edited.to_dict("records")
               if r.get("열 이름") and str(r["열 이름"]).strip() and r.get("항목") in back}
        for alias in set(cur) - set(new):
            store.remove_alias(alias)
        for alias, f in new.items():
            if cur.get(alias) != f:
                store.add_alias(alias, f)
        st.success(f"열 이름 {len(new)}건 저장")


def render() -> None:
    st.title("설정")
    store = get_store()
    _rules_form(store)
    st.divider()
    _targets_editor(store)
    st.divider()
    _dictionary_editor(store)


render()
