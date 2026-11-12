"""End-to-end regression test on a small synthetic clinic (fixed seed)."""
import csv
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from xbase_export.cli import main

HERE = os.path.dirname(__file__)
GEN = os.path.join(HERE, "..", "examples", "make_synthetic_clinic.py")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    pytest.importorskip("faker")
    pytest.importorskip("PIL")
    base = tmp_path_factory.mktemp("clinic")
    subprocess.run([sys.executable, GEN, str(base / "src"), "--patients", "150", "--seed", "7"], check=True,
                   capture_output=True)
    out = base / "out"
    assert main(["run", str(base / "src" / "ACS"), str(out)]) == 0
    with open(out / "validation_report.json") as fh:
        rep = json.load(fh)
    return out, rep


def test_every_value_reconciles(run):
    out, rep = run
    fails = [c for c in rep["checks"] if c["status"] == "FAIL"]
    # The only failure is the deliberately truncated table, and its rows are recovered.
    assert [(c["table"], c["check"]) for c in fails] == [("recall", "record-count")]
    for c in rep["checks"]:
        if c["check"].startswith("values-"):
            assert c["status"] == "PASS", c


def test_counts_match_sqlite(run):
    out, rep = run
    con = sqlite3.connect(out / "export.sqlite")
    with open(out / "dictionary_tables.csv", encoding="utf-8-sig") as fh:
        for t in csv.DictReader(fh):
            n = con.execute(f'SELECT COUNT(*) FROM "{t["table"]}"').fetchone()[0]
            assert n == int(t["records_read"])
            live = con.execute(f'SELECT COUNT(*) FROM "{t["table"]}" WHERE _deleted = 0').fetchone()[0]
            assert live == int(t["live"])


def test_relationships_and_orphans(run):
    out, rep = run
    rels = {(r["child_table"], r["child_column"]): r for r in rep["relationships"]}
    assert rels[("visit", "PATID")]["parent_table"] == "patient"
    assert rels[("charge", "VISITID")]["parent_table"] == "visit"
    appt = rels[("appt", "PATID")]
    assert appt["orphans_absent"] > 0  # appointments of patients removed from the table


def test_missing_documents_listed(run):
    out, rep = run
    with open(out / "documents_index.csv", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert sum(r["exists"] == "False" for r in rows) > 0
    assert any(r["linked_by"] == "folder-name" for r in rows)


def test_tampered_csv_is_detected(run):
    from xbase_export.export import export_table
    from xbase_export.validate import fp_csv
    out, _ = run
    src = out / "snapshot" / "DATA" / "PATIENT.DBF"
    con = sqlite3.connect(":memory:")
    tmp = out / "tamper"
    res = export_table(str(src), str(tmp), con)
    path = tmp / "csv" / "patient.csv"
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    rows[1][1] = rows[1][1] + "X"  # change one surname, nothing else
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh).writerows(rows)
    diff = res.fingerprint.diff(fp_csv(res, str(tmp)))
    assert set(diff) == {"LASTNAME"}
