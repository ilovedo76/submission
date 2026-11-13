"""SQLite store: jobs, rule-set versions, column dictionary, targets, settings, notes, downloads."""
from __future__ import annotations

import json
import math
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pandas as pd

from qreport.ingest.sheets import DEFAULT_DICTIONARY
from qreport.notes import Notes
from qreport.pipeline import AnalysisResult
from qreport.render.content import TEMPLATE_VERSION
from qreport.ruleset import DEFAULT_RULES, RuleSet

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rule_set(version INTEGER PRIMARY KEY, value TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS column_dictionary(alias TEXT PRIMARY KEY, field TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS model_master(model TEXT PRIMARY KEY, target_rate REAL NOT NULL);
CREATE TABLE IF NOT EXISTS setting(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS job(
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, base_month TEXT NOT NULL,
  input_hash TEXT NOT NULL, rule_version INTEGER NOT NULL, template_version TEXT NOT NULL,
  status TEXT NOT NULL, verdict TEXT, alert_count INTEGER, uploaded_at TEXT, first_download_at TEXT);
CREATE TABLE IF NOT EXISTS metric_snapshot(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
  metric TEXT NOT NULL, model TEXT NOT NULL, prod_month TEXT NOT NULL, mis INTEGER NOT NULL,
  numerator REAL NOT NULL, denominator REAL NOT NULL, value REAL);
CREATE TABLE IF NOT EXISTS alert(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
  rule TEXT, severity TEXT, target TEXT, period TEXT, value REAL, threshold REAL, detail TEXT,
  ack_status TEXT NOT NULL DEFAULT 'open', owner TEXT);
CREATE TABLE IF NOT EXISTS engineer_note(
  job_id INTEGER PRIMARY KEY REFERENCES job(id) ON DELETE CASCADE, issues TEXT NOT NULL, plan TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS report_artifact(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, created_at TEXT NOT NULL);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _num(v) -> float | None:
    v = float(v)
    return v if math.isfinite(v) else None


def _json_default(o):
    if isinstance(o, (pd.Period, pd.Timestamp)):
        return str(o)
    if hasattr(o, "item"):
        return o.item()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def _conn(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path)
        con.execute("PRAGMA foreign_keys=ON")
        try:
            with con:
                yield con
        finally:
            con.close()

    def init(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA)
            if c.execute("SELECT COUNT(*) FROM rule_set").fetchone()[0] == 0:
                c.execute("INSERT INTO rule_set VALUES (1, ?, ?)", (DEFAULT_RULES.to_json(), _now()))
            if c.execute("SELECT COUNT(*) FROM column_dictionary").fetchone()[0] == 0:
                c.executemany("INSERT INTO column_dictionary VALUES (?, ?)",
                              list(DEFAULT_DICTIONARY.items()))

    # rules
    def current_rules(self) -> tuple[int, RuleSet]:
        with self._conn() as c:
            v, s = c.execute("SELECT version, value FROM rule_set ORDER BY version DESC LIMIT 1").fetchone()
        return v, RuleSet.from_json(s)

    def rules_by_version(self, version: int) -> RuleSet:
        with self._conn() as c:
            row = c.execute("SELECT value FROM rule_set WHERE version=?", (version,)).fetchone()
        if row is None:
            raise KeyError(f"rule set v{version} not found")
        return RuleSet.from_json(row[0])

    def save_rules(self, r: RuleSet) -> int:
        with self._conn() as c:
            v = c.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM rule_set").fetchone()[0]
            c.execute("INSERT INTO rule_set VALUES (?, ?, ?)", (v, r.to_json(), _now()))
        return v

    # dictionary
    def dictionary(self) -> dict[str, str]:
        with self._conn() as c:
            return dict(c.execute("SELECT alias, field FROM column_dictionary").fetchall())

    def add_alias(self, alias: str, field: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO column_dictionary VALUES (?, ?)", (alias, field))

    def remove_alias(self, alias: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM column_dictionary WHERE alias=?", (alias,))

    # targets
    def model_targets(self) -> dict[str, float]:
        with self._conn() as c:
            return dict(c.execute("SELECT model, target_rate FROM model_master").fetchall())

    def set_model_target(self, model: str, rate: float | None) -> None:
        with self._conn() as c:
            if rate is None:
                c.execute("DELETE FROM model_master WHERE model=?", (model,))
            else:
                c.execute("INSERT OR REPLACE INTO model_master VALUES (?, ?)", (model, float(rate)))

    # settings
    def get_setting(self, key: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO setting VALUES (?, ?)", (key, str(value)))

    # jobs
    def _job_dir(self, job_id: int) -> Path:
        return self.path.parent / "jobs" / str(job_id)

    @staticmethod
    def _snapshot_rows(result: AnalysisResult):
        m = result.metrics
        rows = []

        def pchart(pc, model):
            for r in pc.df.itertuples(index=False):
                rows.append(("1MIS", model, str(r.prod_month), 1, float(r.x), float(r.n), _num(r.p)))

        def kmis(t, model):
            for pm in t.counts.index:
                for k in t.counts.columns:
                    x = t.counts.at[pm, k]
                    if pd.isna(x):
                        continue
                    rows.append(("kMIS", model, str(pm), int(k), float(x),
                                 float(t.denom[pm]), _num(t.rate.at[pm, k])))

        pchart(m.pchart_total, "전체")
        kmis(m.mis_total, "전체")
        for mdl, pc in m.pchart_by_model.items():
            pchart(pc, mdl)
        for mdl, t in m.mis_by_model.items():
            kmis(t, mdl)
        return rows

    @staticmethod
    def _stored_name(i: int, name: str) -> str:
        base = Path(name.replace("\\", "/")).name
        if base in ("", ".", ".."):
            base = f"file{i}.xlsx"
        return f"{i:02d}__{base}"

    def save_job(self, result: AnalysisResult, files: list[tuple[str, bytes]],
                 rule_version: int | None = None) -> int:
        """Atomic: on any failure neither a job row nor a job directory remains."""
        if rule_version is None:
            rule_version = self.current_rules()[0]
        now = _now()
        jobs = self.path.parent / "jobs"
        tmp = jobs / f".tmp-{uuid.uuid4().hex}"
        final: Path | None = None
        try:
            tmp.mkdir(parents=True)
            for i, (name, data) in enumerate(files):
                (tmp / self._stored_name(i, name)).write_bytes(data)
            with self._conn() as c:
                job_id = c.execute(
                    "INSERT INTO job(created_at, base_month, input_hash, rule_version,"
                    " template_version, status, verdict, alert_count, uploaded_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (now, result.base.strftime("%Y-%m"), result.input_hash, rule_version,
                     TEMPLATE_VERSION, "DONE", result.verdict, len(result.alerts), now)).lastrowid
                c.executemany(
                    "INSERT INTO metric_snapshot(job_id, metric, model, prod_month, mis, numerator,"
                    " denominator, value) VALUES (?,?,?,?,?,?,?,?)",
                    [(job_id, *r) for r in self._snapshot_rows(result)])
                c.executemany(
                    "INSERT INTO alert(job_id, rule, severity, target, period, value, threshold,"
                    " detail, ack_status, owner) VALUES (?,?,?,?,?,?,?,?, 'open', NULL)",
                    [(job_id, a.rule, a.severity, a.target,
                      None if a.period is None else str(a.period), _num(a.value),
                      _num(a.threshold),
                      json.dumps(a.detail, ensure_ascii=False, default=_json_default))
                     for a in result.alerts])
                final = self._job_dir(job_id)
                if final.exists():
                    shutil.rmtree(final)  # stale dir from an earlier failed run
                tmp.rename(final)
            return job_id
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            if final is not None:
                shutil.rmtree(final, ignore_errors=True)
            raise

    def find_job_by_hash(self, input_hash: str, rule_version: int,
                         base_month: str | None = None) -> int | None:
        """base_month "YYYY-MM" narrows the match; None = any base month."""
        q, args = "SELECT id FROM job WHERE input_hash=? AND rule_version=?", [input_hash, rule_version]
        if base_month is not None:
            q, args = q + " AND base_month=?", args + [base_month]
        with self._conn() as c:
            row = c.execute(q + " ORDER BY id DESC LIMIT 1", args).fetchone()
        return row[0] if row else None

    def list_jobs(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT id, base_month, created_at, verdict, alert_count, uploaded_at,"
                             " first_download_at, rule_version FROM job ORDER BY id DESC").fetchall()
        out = []
        for id_, bm, ca, v, n, up, fd, rv in rows:
            el = None
            if up and fd:
                secs = (datetime.fromisoformat(fd) - datetime.fromisoformat(up)).total_seconds()
                el = max(0.0, secs / 60)
            out.append({"id": id_, "base_month": bm, "created_at": ca, "verdict": v,
                        "alert_count": n, "elapsed_min": el, "rule_version": rv})
        return out

    def load_job_files(self, job_id: int) -> list[tuple[str, bytes]]:
        d = self._job_dir(job_id)
        if not d.is_dir():
            return []
        return [(p.name.split("__", 1)[1], p.read_bytes())
                for p in sorted(d.iterdir()) if p.is_file()]

    # notes
    def save_notes(self, job_id: int, notes: Notes) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO engineer_note VALUES (?, ?, ?)",
                      (job_id, notes.issues, json.dumps(notes.plan, ensure_ascii=False)))

    def load_notes(self, job_id: int) -> Notes:
        with self._conn() as c:
            row = c.execute("SELECT issues, plan FROM engineer_note WHERE job_id=?",
                            (job_id,)).fetchone()
        return Notes(issues=row[0], plan=json.loads(row[1])) if row else Notes()

    def record_download(self, job_id: int, kind: str) -> None:
        now = _now()
        with self._conn() as c:
            c.execute("INSERT INTO report_artifact(job_id, kind, created_at) VALUES (?,?,?)",
                      (job_id, kind, now))
            c.execute("UPDATE job SET first_download_at=? WHERE id=? AND first_download_at IS NULL",
                      (now, job_id))
