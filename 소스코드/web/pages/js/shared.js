// Shared helpers for the dashboard (index.html) and the MVP page (mvp.html).
// Display rounding only (constraints: no statistics in web JS). Formats match qreport's
// rules/comments.py: _pct = f"{v*100:.3f}%", ratio_text = f"{v:.1f}배", change_text = f"{pct:+.0f}%".

export const TOTAL = "전체";
export const NA = "–";

const finite = (x) => typeof x === "number" && Number.isFinite(x);

/** Python's f"{x:.{d}f}": the exact binary value, ties (exactly representable halves) to even.
 *  JS toFixed differs only on exact ties (it rounds them up), detected from the exact expansion. */
export function pyFixed(x, d) {
  const s = x.toFixed(d);
  const exact = Math.abs(x).toFixed(Math.min(100, d + 60));
  const tail = exact.slice(exact.indexOf(".") + 1 + d);
  if (!/^50*$/.test(tail)) return s;
  // exact tie: toFixed rounded away from zero; step back if that left an odd last digit
  const digits = s.replace("-", "").replace(".", "");
  if (Number(digits[digits.length - 1]) % 2 === 0) return s;
  const step = 10 ** -d;
  return (x > 0 ? x - step / 2 : x + step / 2).toFixed(d);
}

/** rate (0.00136) -> "0.136%"; null / NaN -> "–". */
export function fmtPct(rate) {
  return finite(rate) ? `${pyFixed(rate * 100, 3)}%` : NA;
}

/** 1.94 -> "1.9배". */
export function fmtRatio(x) {
  return finite(x) ? `${pyFixed(x, 1)}배` : NA;
}

/** 57.2 -> "+57%", -3.4 -> "-3%". */
export function fmtChange(pct) {
  if (!finite(pct)) return NA;
  const s = pyFixed(pct, 0);           // "-0" for -0.3, like Python
  return s.startsWith("-") ? `${s}%` : `+${s}%`;
}

/** Thousands separators, integer display (counts, won). */
export function fmtInt(x) {
  return finite(x) ? Math.round(x).toLocaleString("en-US") : NA;
}

/** Same id as web_entry.run_id: f"{inputHash[:16]}_{baseMonth}". */
export function runId(inputHash, baseMonth) {
  return `${String(inputHash).slice(0, 16)}_${baseMonth}`;
}

const stamp = (r) => String(r?.updatedAt ?? r?.createdAt ?? "");

/** Runs newest first by updatedAt (then createdAt, then id for a stable order). */
export function sortRuns(runs) {
  return [...(runs || [])].sort((a, b) =>
    stamp(b).localeCompare(stamp(a)) || String(b.id ?? "").localeCompare(String(a.id ?? "")));
}

/** Default dashboard run: newest non-sample run; only samples -> newest sample; none -> null. */
export function defaultRun(runs) {
  const sorted = sortRuns(runs);
  return sorted.find((r) => r.kind !== "sample") ?? sorted[0] ?? null;
}

/** Rows of one series for a model (null / "" / "전체" -> the overall rows). Missing -> []. */
export function seriesFor(series, name, model) {
  const scope = model ? model : TOTAL;
  return series?.[name]?.[scope] ?? [];
}

export const SEVERITY_STYLE = Object.freeze({
  "높음": Object.freeze({ label: "높음", icon: "▲", color: "#d03b3b" }),
  "중간": Object.freeze({ label: "중간", icon: "●", color: "#b86e00" }),
  "낮음": Object.freeze({ label: "낮음", icon: "○", color: "#6b6865" }),
});

export const VERDICT_STYLE = Object.freeze({
  "개선 필요": Object.freeze({ color: "#ffffff", bg: "#d03b3b" }),
  "주의": Object.freeze({ color: "#3a2a00", bg: "#eda100" }),
  "정상": Object.freeze({ color: "#ffffff", bg: "#1b8a5f" }),
});

/** "2026-11-12T15:40:00Z" -> "2026-11-12 15:40" in the viewer's local time. */
export function fmtTime(iso) {
  if (!iso) return NA;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** "2025-12" -> "25.12" (qreport's month label). */
export function ymLabel(m) {
  return typeof m === "string" && /^\d{4}-\d{2}$/.test(m) ? `${m.slice(2, 4)}.${m.slice(5)}` : String(m ?? "");
}
