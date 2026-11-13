"""Report charts (Matplotlib, static PNG) - every chart draws its mandatory reference lines."""
from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.ticker import FuncFormatter, MaxNLocator

from qreport.metrics.cohort import KS, mis_table
from qreport.metrics.heatmap import heatmap
from qreport.metrics.market import market_control_limits, market_rate
from qreport.metrics.pchart import p_chart
from qreport.metrics.weibull import fit_weibull
from qreport.metrics.yoy import yoy
from qreport.periods import recent_12, weibull_segments
from qreport.render.chart_data import model_type_counts, pareto_table
from qreport.render.fonts import setup_fonts

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT2 = "#52514e"
GRID = "#e6e6e3"
CRIT = "#d03b3b"
WARN = "#fab219"
NEUTRAL = "#52514e"
PRIOR_BAR = "#c9c8c4"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#2a78d6", "#1c5cab", "#104281", "#0d366b"]
DIV_BLUE, DIV_MID, DIV_ORANGE = "#2a78d6", "#f0efec", "#eb6834"
NOT_YET = "#e6e6e3"
NO_PRIOR = "전년 같은 MIS (전년 데이터 없음)"


@dataclass
class ChartSpec:
    key: str
    title: str
    fig: Figure
    ref_lines: list[str]
    data: pd.DataFrame | None = None


# ---------------------------------------------------------------- helpers
def _ym(p) -> str:
    return pd.Period(p, "M").strftime("%y.%m")


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(TEXT2)
    ax.tick_params(colors=TEXT2, labelsize=9)
    ax.xaxis.label.set_color(TEXT2)
    ax.yaxis.label.set_color(TEXT2)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def _fig(w=9.0, h=4.8):
    setup_fonts()
    fig = Figure(figsize=(w, h), facecolor=SURFACE)
    ax = fig.add_subplot(111)
    _style(ax)
    return fig, ax


def _title(ax, text: str, pad=12) -> None:
    ax.set_title(text, color=TEXT, fontsize=12, fontweight="bold", loc="left", pad=pad)


def _fmt_pct(v: float) -> str:
    return f"{v * 100:.3f}%"


def _pct_axis(ax, which="y") -> None:
    axis = ax.yaxis if which == "y" else ax.xaxis
    axis.set_major_locator(MaxNLocator(nbins=6, steps=[1, 2, 5, 10]))
    axis.set_major_formatter(FuncFormatter(lambda v, _: _fmt_pct(v)))


def _legend(ax, handles=None, **kw) -> None:
    ax.legend(handles=handles, frameon=False, fontsize=9, labelcolor=TEXT2, **kw)


def _color(models: list[str], m: str) -> str:
    i = models.index(m) if m in models else 0
    return SERIES[i % len(SERIES)]


def _scope(model: str | None) -> str:
    return "전체" if model is None else model


def _dot(ax, x, y, color) -> None:
    ax.scatter(x, y, s=64, color=color, edgecolor=SURFACE, linewidth=1.5, zorder=5)


def _month_ticks(ax, months) -> None:
    step = max(1, len(months) // 12)
    x = np.arange(len(months))
    ax.set_xticks(x[::step], [_ym(m) for m in list(months)[::step]], rotation=45)


# ---------------------------------------------------------------- charts
def _trend(ds, base, rules, model, targets) -> ChartSpec:
    mr = market_control_limits(ds, base, rules, model)
    pbar = float(mr["pbar"].iloc[0]) if len(mr) else float("nan")
    ucl = mr["ucl"].to_numpy()
    rate = mr["rate"].to_numpy()
    fig, ax = _fig()
    x = np.arange(len(mr))
    ax.plot(x, rate, color=SERIES[0], lw=2, marker="o", ms=4)
    ax.step(x, ucl, where="mid", color=CRIT, lw=1.5, ls="--")
    ax.axhline(pbar, color=NEUTRAL, lw=1.5, ls="--")
    refs = ["중심선", "관리상한"]
    handles = [Line2D([], [], color=SERIES[0], lw=2, marker="o", label=f"{_scope(model)} 시장 불량률"),
               Line2D([], [], color=NEUTRAL, lw=1.5, ls="--", label=f"중심선 {_fmt_pct(pbar)}"),
               Line2D([], [], color=CRIT, lw=1.5, ls="--", label="관리상한")]
    breach = rate > ucl
    if breach.any():
        _dot(ax, x[breach], rate[breach], CRIT)
        handles.append(Line2D([], [], color=CRIT, lw=0, marker="o", ms=8, label="관리상한 초과"))
    tgt = (targets or {}).get(_scope(model))
    if tgt is not None and math.isfinite(tgt):
        ax.axhline(tgt, color=WARN, lw=2)
        refs.append("목표")
        handles.append(Line2D([], [], color=WARN, lw=2, label=f"목표 {_fmt_pct(tgt)}"))
    if len(x) and not breach[-1]:
        _dot(ax, [x[-1]], [rate[-1]], SERIES[0])
    if len(x):
        ax.annotate(_fmt_pct(rate[-1]), (x[-1], rate[-1]), textcoords="offset points",
                    xytext=(0, 9), ha="center", fontsize=9, color=TEXT)
    _month_ticks(ax, mr["month"])
    ax.set_xlabel("발생월")
    ax.set_ylabel("시장 불량률 (%)")
    _pct_axis(ax)
    ax.margins(y=0.2)
    _legend(ax, handles, loc="upper center", bbox_to_anchor=(0.5, -0.28), ncol=4)
    title = f"{_scope(model)} 발생월별 시장 불량률 추이 (%)"
    _title(ax, title)
    return ChartSpec("trend", title, fig, refs, mr)


def _pchart(ds, base, rules, model, key) -> ChartSpec:
    pc = p_chart(ds, base, rules, model)
    df = pc.df
    fig, ax = _fig()
    x = np.arange(len(df))
    ax.plot(x, df["p"], color=SERIES[0], lw=2, marker="o", ms=4)
    ax.step(x, df["ucl"], where="mid", color=CRIT, lw=1.5, ls="--")
    ax.axhline(pc.pbar, color=NEUTRAL, lw=1.5, ls="--")
    handles = [Line2D([], [], color=SERIES[0], lw=2, marker="o", label="1MIS 불량률"),
               Line2D([], [], color=NEUTRAL, lw=1.5, ls="--", label=f"중심선 {_fmt_pct(pc.pbar)}"),
               Line2D([], [], color=CRIT, lw=1.5, ls="--", label="관리상한 (UCL)")]
    if pc.test1:
        sel = df["prod_month"].isin(pc.test1).to_numpy()
        _dot(ax, x[sel], df["p"].to_numpy()[sel], CRIT)
        for xi, pi, m in zip(x[sel], df["p"].to_numpy()[sel], df["prod_month"][sel]):
            ax.annotate(_ym(m), (xi, pi), textcoords="offset points", xytext=(0, 8), ha="center",
                        fontsize=8, color=TEXT)
        handles.append(Line2D([], [], color=CRIT, lw=0, marker="o", ms=8, label="관리상한 초과"))
    if len(df) and df["prod_month"].iloc[-1] not in pc.test1:
        _dot(ax, [x[-1]], [df["p"].iloc[-1]], SERIES[0])
    if len(df):
        ax.annotate(_fmt_pct(df["p"].iloc[-1]), (x[-1], df["p"].iloc[-1]), textcoords="offset points",
                    xytext=(-10, -3), ha="right", fontsize=9, color=TEXT)
    _month_ticks(ax, df["prod_month"])
    ax.set_xlabel("생산월")
    ax.set_ylabel("1MIS 불량률 (%)")
    _pct_axis(ax)
    ax.margins(y=0.2)
    _legend(ax, handles, loc="upper center", bbox_to_anchor=(0.5, -0.28), ncol=4)
    title = f"{_scope(model)} 생산월별 1MIS 불량률 P 관리도 (%)"
    _title(ax, title)
    return ChartSpec(key, title, fig, ["중심선", "관리상한"], df)


def _mis_curve(ds, base, rules, model) -> ChartSpec:
    rate = mis_table(ds, base, model).rate
    hm = heatmap(ds, base, rules, model)
    fig, ax = _fig()
    coh = [m for m in recent_12(base).months() if m in rate.index][-6:]
    handles = []
    for m in coh:
        col = SERIES[(m.month - 1) % len(SERIES)]  # entity colour: fixed per calendar month of production
        r = rate.loc[m].dropna()
        ax.plot(r.index, r.values, color=col, lw=2, marker="o", ms=5)
        handles.append(Line2D([], [], color=col, lw=2, marker="o", label=f"{m.strftime('%Y-%m')} 생산"))
    refs = []
    if not hm.baseline.empty:
        b = hm.baseline.dropna()
        ax.plot(b.index, b.values, color=NEUTRAL, lw=2, ls="--")
        handles.append(Line2D([], [], color=NEUTRAL, lw=2, ls="--", label="전년 같은 MIS"))
        refs.append("전년 같은 MIS")
    if not refs:
        refs.append(NO_PRIOR)
        ax.text(0.98, 0.04, "전년 같은 MIS 기준선 없음 (전년 데이터 부족)", transform=ax.transAxes, ha="right",
                fontsize=9, color=TEXT2)
    ax.set_xticks(KS, [f"{k}MIS" for k in KS])
    ax.set_xlabel("사용 개월 (누적)")
    ax.set_ylabel("누적 불량률 (%)")
    _pct_axis(ax)
    _legend(ax, handles, loc="upper left", ncol=2)
    title = f"{_scope(model)} 생산월별 MIS 누적 불량률 곡선 (%)"
    _title(ax, title)
    return ChartSpec("mis_curve", title, fig, refs, rate)


def _heatmap(ds, base, rules, model) -> ChartSpec:
    hm = heatmap(ds, base, rules, model)
    rows = [m for m in recent_12(base).months() if m in hm.rate.index]
    sub = hm.rate.loc[rows]
    n = len(rows)
    setup_fonts()
    fig = Figure(figsize=(8.4, 0.42 * (n + 2) + 2.2), facecolor=SURFACE)
    ax = fig.add_subplot(111)
    _style(ax)
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    vals = sub.to_numpy(dtype=float)
    vmax = np.nanmax(vals) if np.isfinite(vals).any() else 1.0
    cmap = LinearSegmentedColormap.from_list("seq", SEQ)
    refs = []
    for i, m in enumerate(rows):
        for j, k in enumerate(KS):
            v = vals[i, j]
            if np.isnan(v):
                ax.add_patch(Rectangle((j, i), 1, 1, facecolor=NOT_YET, edgecolor=SURFACE, lw=2))
                ax.text(j + .5, i + .5, "관찰 전", ha="center", va="center", fontsize=8, color=TEXT2)
                continue
            frac = v / vmax if vmax > 0 else 0
            ax.add_patch(Rectangle((j, i), 1, 1, facecolor=cmap(frac), edgecolor=SURFACE, lw=2))
            ax.text(j + .5, i + .5, _fmt_pct(v), ha="center", va="center", fontsize=7.5,
                    color="#ffffff" if frac > 0.45 else TEXT)
            if bool(hm.highlight.loc[m, k]):
                ax.add_patch(Rectangle((j + .04, i + .04), .92, .92, fill=False, edgecolor=CRIT, lw=2,
                                       zorder=4))
    if not hm.baseline.empty:
        refs.append("전년 같은 MIS")
        for j, k in enumerate(KS):
            b = hm.baseline.get(k, np.nan)
            ax.add_patch(Rectangle((j, n + .3), 1, 1, facecolor=SURFACE, edgecolor=NEUTRAL, lw=1.2, ls="--"))
            ax.text(j + .5, n + .8, "-" if np.isnan(b) else _fmt_pct(b), ha="center", va="center",
                    fontsize=7.5, color=TEXT)
        ax.text(-.1, n + .8, "전년 같은 MIS", ha="right", va="center", fontsize=9, color=TEXT2)
    if not refs:
        refs.append(NO_PRIOR)
        ax.text(0, n + .8, "전년 같은 MIS 기준선 없음 (전년 데이터 부족)", ha="left", va="center",
                fontsize=9, color=TEXT2)
    ax.set_xlim(0, len(KS))
    ax.set_ylim(n + 1.4, 0)
    ax.set_xticks([j + .5 for j in range(len(KS))], [f"{k}MIS" for k in KS])
    ax.set_yticks([i + .5 for i in range(n)], [m.strftime("%Y-%m") for m in rows])
    ax.tick_params(length=0)
    ax.xaxis.tick_top()
    handles = [Patch(facecolor=cmap(.5), label="누적 불량률 (진할수록 높음)"),
               Patch(facecolor=NOT_YET, label="관찰 전"),
               Patch(facecolor="none", edgecolor=CRIT, lw=2, label="전년 같은 MIS 초과")]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.01), ncol=3, frameon=False,
              fontsize=9, labelcolor=TEXT2)
    title = f"{_scope(model)} 생산월 x MIS 누적 불량률 히트맵 (%)"
    ax.set_title(title, color=TEXT, fontsize=12, fontweight="bold", loc="left", pad=30)
    return ChartSpec("heatmap", title, fig, refs, sub)


def _pareto(ds, base, model) -> ChartSpec:
    table = pareto_table(ds, base, model)
    c, cum = table["count"], table["cum_share"]
    setup_fonts()
    fig = Figure(figsize=(9.0, 6.2), facecolor=SURFACE)
    gs = fig.add_gridspec(2, 1, height_ratios=[3, 1.7], hspace=0.12)
    ax = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1], sharex=ax)
    _style(ax)
    _style(ax2)
    x = np.arange(len(c))
    ax.bar(x, c.values, color=SERIES[0], edgecolor=SURFACE, linewidth=2)
    for xi, v in zip(x, c.values):
        ax.annotate(f"{int(v):,}", (xi, v), textcoords="offset points", xytext=(0, 3), ha="center",
                    fontsize=8, color=TEXT)
    ax.set_ylabel("불량 건수 (건)")
    ax.tick_params(labelbottom=False)
    ax2.plot(x, cum.values * 100, color=SERIES[0], lw=2, marker="o", ms=5)
    ax2.axhline(80, color=NEUTRAL, lw=1.5, ls="--")
    ax2.set_ylim(0, 108)
    ax2.set_ylabel("누적 비율 (%)")
    ax2.set_xticks(x, [str(i) for i in c.index], rotation=35, ha="right")
    ax2.set_xlabel("불량 유형")
    ax2.set_xlim(-.6, len(c) - .4)
    ax2.legend(handles=[Line2D([], [], color=SERIES[0], lw=2, marker="o", label="누적 비율"),
                        Line2D([], [], color=NEUTRAL, lw=1.5, ls="--", label="80% 기준선")],
               loc="lower right", frameon=False, fontsize=9, labelcolor=TEXT2)
    title = f"{_scope(model)} 불량 유형별 파레토 (최근 12개월, 건수)"
    ax.set_title(title, color=TEXT, fontsize=12, fontweight="bold", loc="left", pad=12)
    return ChartSpec("pareto", title, fig, ["80% 누적"], table)


def _model_type(ds, base, model) -> ChartSpec:
    recent, prior = model_type_counts(ds, base, model)
    mat = recent - prior
    models, types = list(mat.index), list(mat.columns)
    lim = max(1.0, float(np.abs(mat.to_numpy()).max()))
    cmap = LinearSegmentedColormap.from_list("div", [DIV_BLUE, DIV_MID, DIV_ORANGE])
    fig, ax = _fig(max(7.5, 0.95 * len(types) + 3), 0.55 * len(models) + 2.6)
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    im = ax.imshow(mat.to_numpy(), cmap=cmap, vmin=-lim, vmax=lim, aspect="auto")
    for i in range(len(models)):
        for j in range(len(types)):
            v = int(mat.iloc[i, j])
            ax.text(j, i, f"{v:+d}" if v else "0", ha="center", va="center", fontsize=9,
                    color="#ffffff" if abs(v) / lim > 0.85 else TEXT)
    ax.set_xticks(range(len(types)), types, rotation=30, ha="right")
    ax.set_yticks(range(len(models)), models)
    ax.tick_params(length=0)
    cb = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.03)
    cb.set_label("전년 대비 증감 (건)", color=TEXT2, fontsize=9)
    cb.ax.tick_params(colors=TEXT2, labelsize=8)
    cb.outline.set_visible(False)
    title = f"{_scope(model)} 모델 x 불량 유형 전년 대비 증감 (최근 12개월 - 전년 12개월, 건; 주황=증가, 파랑=감소)"
    _title(ax, title)
    return ChartSpec("model_type", title, fig, ["전년 대비 증감"], mat)


def _monthly(ds, base, model) -> pd.DataFrame:
    mr = market_rate(ds, model)
    return mr[mr["month"] <= base]


def _mean_rate(ds, base, rules, model) -> float:
    t = market_control_limits(ds, base, rules, model)
    return float(t["pbar"].iloc[0]) if len(t) else float("nan")


def _histogram(ds, base, rules, model) -> ChartSpec:
    mr = _monthly(ds, base, model).dropna(subset=["rate"])
    mean = _mean_rate(ds, base, rules, model)
    fig, ax = _fig()
    ax.hist(mr["rate"], bins=min(12, max(4, len(mr) // 2)), color=SERIES[0], edgecolor=SURFACE, linewidth=2)
    ax.axvline(mean, color=NEUTRAL, lw=2, ls="--", label=f"전체 평균 {_fmt_pct(mean)}")
    ax.set_xlabel("월별 시장 불량률 (%)")
    ax.set_ylabel("월 수 (개월)")
    _pct_axis(ax, "x")
    _legend(ax, loc="upper right")
    title = f"{_scope(model)} 월별 시장 불량률 분포 (%)"
    _title(ax, title)
    return ChartSpec("histogram", title, fig, ["전체 평균"], mr)


def _boxplot(ds, base, rules, model) -> ChartSpec:
    groups = {"전체": _monthly(ds, base, None)} if model is None else {}
    for m in ([model] if model else ds.models):
        groups[m] = _monthly(ds, base, m)
    names = list(groups)
    mean = _mean_rate(ds, base, rules, model)
    fig, ax = _fig(max(7.0, 1.4 * len(names) + 3), 4.8)
    bp = ax.boxplot([g["rate"].dropna().to_numpy() for g in groups.values()], patch_artist=True,
                    widths=0.55, medianprops=dict(color=TEXT, lw=2), whiskerprops=dict(color=TEXT2),
                    capprops=dict(color=TEXT2),
                    flierprops=dict(marker="o", markerfacecolor=TEXT2, markeredgecolor=SURFACE, ms=6))
    for patch, n in zip(bp["boxes"], names):
        patch.set(facecolor=NEUTRAL if n == "전체" else _color(list(ds.models), n), edgecolor=SURFACE,
                  lw=2, alpha=.9)
    ax.axhline(mean, color=NEUTRAL, lw=2, ls="--", label=f"전체 평균 {_fmt_pct(mean)}")
    ax.set_xticks(range(1, len(names) + 1), names)
    ax.set_ylabel("월별 시장 불량률 (%)")
    _pct_axis(ax)
    _legend(ax, loc="upper right")
    title = f"{_scope(model)} 모델별 월별 시장 불량률 분포 (%)"
    _title(ax, title)
    df = pd.concat([g.assign(group=n) for n, g in groups.items()], ignore_index=True)
    return ChartSpec("boxplot", title, fig, ["전체 평균"], df)


_SEG_NAMES = {"S1": "S1 (최근)", "S2": "S2", "S3": "S3 (과거)", "PRIOR": "전년 12개월",
              "RECENT6": "최근 6개월"}


def _weibull(ds, base, rules, model, result) -> ChartSpec:
    seg = weibull_segments(base, rules.seg_months)
    if model is None:
        fits = [(_SEG_NAMES[k], result.metrics.weibull.get(k), SERIES[0]) for k in seg]
        fits += [(m, result.metrics.weibull_by_model.get(m), _color(list(ds.models), m)) for m in ds.models]
    else:
        fits = [(_SEG_NAMES[k], fit_weibull(ds, base, w, model, label=k), SERIES[0]) for k, w in seg.items()]
    fits = [(n, f, c) for n, f, c in fits if f is not None]
    fig, ax = _fig(max(8.0, 1.0 * len(fits) + 3), 5.0)
    rows = []
    for i, (n, f, c) in enumerate(fits):
        ax.errorbar([i], [f.beta], yerr=[[f.beta - f.beta_lo], [f.beta_hi - f.beta]], fmt="o", color=c,
                    ms=9, mec=SURFACE, mew=1.5, lw=2, capsize=5)
        ax.annotate(f"{f.beta:.2f}", (i, f.beta), textcoords="offset points", xytext=(10, 0), fontsize=8,
                    color=TEXT, va="center")
        rows.append({"label": n, "beta": f.beta, "beta_lo": f.beta_lo, "beta_hi": f.beta_hi,
                     "n_fail": f.n_fail})
    ax.axhline(1.0, color=NEUTRAL, lw=2, ls="--")
    ax.set_xticks(range(len(fits)), [n for n, _, _ in fits], rotation=30, ha="right")
    ax.set_xlim(-.6, max(len(fits) - .4, .6))
    ax.set_ylabel("형상모수 β (95% 신뢰구간)")
    ax.set_xlabel("구간 / 모델")
    _legend(ax, [Line2D([], [], color=SERIES[0], marker="o", lw=2, label="β 추정치 (95% 신뢰구간)"),
                 Line2D([], [], color=NEUTRAL, lw=2, ls="--", label="β=1 (일정 고장)")], loc="upper left")
    title = f"{_scope(model)} 와이블 형상모수 β 추정 (β>1 마모, β<1 초기 고장)"
    _title(ax, title)
    return ChartSpec("weibull", title, fig, ["β=1"], pd.DataFrame(rows))


def _yoy(ds, base, rules, model) -> ChartSpec:
    df = yoy(ds, base, rules, "model")
    if model is not None:
        df = df[df["group"] == model].reset_index(drop=True)
    else:
        df = pd.concat([yoy(ds, base, rules, "total"), df], ignore_index=True)
    fig, ax = _fig(max(7.0, 1.6 * len(df) + 3), 4.8)
    refs = ["전년"]
    if df.empty:
        refs = ["전년 (전년 데이터 없음)"]
        ax.text(0.5, 0.5, "전년 12개월 데이터가 부족하여 비교할 수 없음", transform=ax.transAxes, ha="center",
                color=TEXT2)
    x = np.arange(len(df))
    w = 0.38
    models = list(ds.models)
    ax.bar(x - w / 2, df["p0"], w, color=PRIOR_BAR, edgecolor=SURFACE, linewidth=2)
    cols = [NEUTRAL if g == "전체" else _color(models, g) for g in df["group"]]
    ax.bar(x + w / 2, df["p1"], w, color=cols, edgecolor=SURFACE, linewidth=2)
    for xi, p0, p1 in zip(x, df["p0"], df["p1"]):
        ax.annotate(_fmt_pct(p0), (xi - w / 2, p0), textcoords="offset points", xytext=(0, 3),
                    ha="center", fontsize=8, color=TEXT2)
        ax.annotate(_fmt_pct(p1), (xi + w / 2, p1), textcoords="offset points", xytext=(0, 3),
                    ha="center", fontsize=8, color=TEXT)
    ax.set_xticks(x, list(df["group"]))
    ax.set_ylabel("시장 불량률 (%)")
    _pct_axis(ax)
    ax.margins(y=0.15)
    _legend(ax, [Patch(facecolor=PRIOR_BAR, label="전년 12개월 (기준)"),
                 Patch(facecolor=SERIES[0], label="최근 12개월 (모델 색)")], loc="upper left")
    title = f"{_scope(model)} 전년 대비 시장 불량률 (전년 12개월 vs 최근 12개월, %)"
    _title(ax, title)
    return ChartSpec("yoy", title, fig, refs, df)


# ---------------------------------------------------------------- public
def build_charts(result, model: str | None = None,
                 targets: dict[str, float] | None = None) -> dict[str, ChartSpec]:
    """Overall chart set; model=None adds pchart_<model> per model, model="X" filters everything to X.
    targets = {"전체"|model: target market defect rate (fraction)} adds a target line to trend."""
    setup_fonts()
    ds, base, rules = result.dataset, result.base, result.rules
    out: dict[str, ChartSpec] = {}
    out["trend"] = _trend(ds, base, rules, model, targets)
    out["pchart"] = _pchart(ds, base, rules, model, "pchart")
    if model is None:
        for m in ds.models:
            out[f"pchart_{m}"] = _pchart(ds, base, rules, m, f"pchart_{m}")
    out["mis_curve"] = _mis_curve(ds, base, rules, model)
    out["heatmap"] = _heatmap(ds, base, rules, model)
    out["pareto"] = _pareto(ds, base, model)
    out["model_type"] = _model_type(ds, base, model)
    out["histogram"] = _histogram(ds, base, rules, model)
    out["boxplot"] = _boxplot(ds, base, rules, model)
    out["weibull"] = _weibull(ds, base, rules, model, result)
    out["yoy"] = _yoy(ds, base, rules, model)
    return out


def to_png(spec: ChartSpec, dpi: int = 300) -> bytes:
    buf = io.BytesIO()
    spec.fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor=SURFACE)
    return buf.getvalue()
