import sqlite3
import time

from qreport.ingest.sheets import DEFAULT_DICTIONARY
from qreport.notes import Notes
from qreport.pipeline import analyze
from qreport.ruleset import DEFAULT_RULES
from qreport.store.db import Store


def make(tmp_path):
    s = Store(tmp_path / "q.db")
    s.init()
    return s


def test_rules_versioning(tmp_path):
    s = make(tmp_path)
    s.init()  # idempotent
    assert s.current_rules() == (1, DEFAULT_RULES)
    assert s.save_rules(DEFAULT_RULES.replace(min_count=4)) == 2
    v, r = s.current_rules()
    assert v == 2 and r.min_count == 4
    assert s.dictionary() == DEFAULT_DICTIONARY


def test_settings_targets_alias(tmp_path):
    s = make(tmp_path)
    assert s.get_setting("as_cost") is None
    s.set_setting("as_cost", "120000")
    assert s.get_setting("as_cost") == "120000"
    s.set_model_target("김치", 0.002)
    assert s.model_targets() == {"김치": 0.002}
    s.set_model_target("김치", None)
    assert s.model_targets() == {}
    s.add_alias("제품", "model")
    assert s.dictionary()["제품"] == "model"


def test_same_input_same_hash_and_notes(tmp_path, company_bytes):
    s = make(tmp_path)
    r1 = analyze([("c.xlsx", company_bytes)], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    j = s.save_job(r1, [("c.xlsx", company_bytes)])
    s.save_notes(j, Notes(issues="A", plan=[{"담당": "x", "일정": "y", "대책": "z"}]))
    r2 = analyze([("c.xlsx", company_bytes)], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    assert r2.input_hash == r1.input_hash and s.find_job_by_hash(r2.input_hash, 1) == j
    assert s.find_job_by_hash(r2.input_hash, 2) is None
    assert s.load_notes(j).issues == "A" and s.load_notes(j).plan[0]["대책"] == "z"
    assert [a.__dict__ for a in r2.alerts] == [a.__dict__ for a in r1.alerts]
    assert s.load_job_files(j) == [("c.xlsx", company_bytes)]
    with sqlite3.connect(s.path) as c:
        n = c.execute("SELECT COUNT(*) FROM alert WHERE job_id=?", (j,)).fetchone()[0]
    assert n == len(r1.alerts)
    j2 = s.save_job(r1, [("c.xlsx", company_bytes)])
    assert j2 != j and s.find_job_by_hash(r1.input_hash, 1) == j2


def test_snapshot_has_num_den(tmp_path, company_result):
    s = make(tmp_path)
    j = s.save_job(company_result, [])
    with sqlite3.connect(s.path) as c:
        rows = c.execute("SELECT metric, numerator, denominator FROM metric_snapshot"
                         " WHERE job_id=?", (j,)).fetchall()
    assert rows and all(n is not None and d is not None for _, n, d in rows)
    assert {"1MIS", "kMIS"} <= {m for m, _, _ in rows}


def test_elapsed_minutes(tmp_path, company_result):
    s = make(tmp_path)
    j = s.save_job(company_result, [])
    assert s.list_jobs()[0]["elapsed_min"] is None
    time.sleep(0.01)
    s.record_download(j, "docx")
    row = s.list_jobs()[0]
    assert row["id"] == j and row["elapsed_min"] >= 0 and row["base_month"] == "2025-12"


def test_same_basename_roundtrip(tmp_path, company_bytes):
    s = make(tmp_path)
    files = [("a/c.xlsx", company_bytes), ("b/c.xlsx", company_bytes[:-1] + b"\x00")]
    r = analyze([("c.xlsx", company_bytes)], None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    j = s.save_job(r, files)
    got = s.load_job_files(j)
    assert [n for n, _ in got] == ["c.xlsx", "c.xlsx"]
    assert [d for _, d in got] == [d for _, d in files]


def test_hostile_names_stay_inside_job_dir(tmp_path, company_result):
    s = make(tmp_path)
    names = ["", ".", "..", "a/../../x.xlsx"]
    j = s.save_job(company_result, [(n, b"x") for n in names])
    jobdir = tmp_path / "jobs" / str(j)
    assert len(list(jobdir.iterdir())) == 4
    assert sorted(p.name for p in (tmp_path / "jobs").iterdir()) == [str(j)]
    assert not (tmp_path / "x.xlsx").exists()
    assert [n for n, _ in s.load_job_files(j)] == ["file0.xlsx", "file1.xlsx", "file2.xlsx", "x.xlsx"]


def test_failed_write_leaves_nothing(tmp_path, company_result, monkeypatch):
    from pathlib import Path
    s = make(tmp_path)

    def boom(self, data):
        raise OSError("disk full")
    monkeypatch.setattr(Path, "write_bytes", boom)
    try:
        s.save_job(company_result, [("a.xlsx", b"x")])
        raise AssertionError("expected OSError")
    except OSError:
        pass
    assert s.list_jobs() == []
    assert list((tmp_path / "jobs").iterdir()) == []


def test_reloaded_company_files_reproduce_hash(tmp_path, company_bytes):
    s = make(tmp_path)
    files = [("c.xlsx", company_bytes)]
    r = analyze(files, None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    j = s.save_job(r, files)
    r2 = analyze(s.load_job_files(j), None, DEFAULT_RULES, DEFAULT_DICTIONARY)
    assert r2.input_hash == r.input_hash


def test_reloaded_same_basename_files_reproduce_hash(tmp_path, company_result):
    from qreport.pipeline import input_hash
    from tests.synth import make_workbook
    s = make(tmp_path)
    files = [("d1/w.xlsx", make_workbook(seed=1)), ("d2/w.xlsx", make_workbook(seed=2))]
    j = s.save_job(company_result, files)
    got = s.load_job_files(j)
    assert [n for n, _ in got] == ["w.xlsx", "w.xlsx"]
    assert input_hash(got) == input_hash([("w.xlsx", d) for _, d in files])
    assert input_hash(got) != input_hash(got[:1])


def test_remove_alias(tmp_path):
    from qreport.store.db import Store
    s = Store(tmp_path / "q.db")
    s.init()
    s.add_alias("제품", "model")
    assert "제품" in s.dictionary()
    s.remove_alias("제품")
    assert "제품" not in s.dictionary()


def test_find_job_by_base_month(tmp_path, company_result):
    from qreport.store.db import Store
    s = Store(tmp_path / "q.db")
    s.init()
    j = s.save_job(company_result, [("a.xlsx", b"x")])
    h, bm = company_result.input_hash, company_result.base.strftime("%Y-%m")
    assert s.find_job_by_hash(h, 1, bm) == j and s.find_job_by_hash(h, 1) == j
    assert s.find_job_by_hash(h, 1, "1999-01") is None


def test_rules_by_version(tmp_path):
    import dataclasses

    from qreport.ruleset import DEFAULT_RULES
    s = make(tmp_path)
    v = s.save_rules(dataclasses.replace(DEFAULT_RULES, ucl_sigma=2.0))
    assert s.rules_by_version(1) == DEFAULT_RULES
    assert s.rules_by_version(v).ucl_sigma == 2.0


def test_default_data_dir_outside_project(monkeypatch, tmp_path):
    from pathlib import Path

    from qreport.app.common import ROOT, data_dir
    monkeypatch.setenv("QREPORT_DATA", str(tmp_path / "x"))
    assert data_dir() == tmp_path / "x"
    monkeypatch.delenv("QREPORT_DATA")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    assert data_dir() == tmp_path / "lad" / "qreport" / "data"
    monkeypatch.delenv("LOCALAPPDATA")
    assert data_dir() == Path.home() / ".qreport" / "data"
    assert ROOT not in data_dir().parents
