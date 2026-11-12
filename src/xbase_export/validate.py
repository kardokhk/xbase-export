"""Independent reconciliation of every exported table.

Each table is fingerprinted four ways and the fingerprints must agree value
for value, column by column:
  1. the toolkit's own reader, while exporting (source of truth);
  2. dbfread, an independent open-source xBase reader, re-reading the snapshot;
  3. the CSV files, re-read from disk;
  4. the SQLite database, re-queried;
  5. optionally PostgreSQL, after loading with the generated scripts.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import sqlite3
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .canon import Fingerprint, canon, canon_from_text
from .export import TableResult, sql_ident

csv.field_size_limit(1 << 30)


@dataclass
class Check:
    table: str
    check: str
    status: str   # PASS / WARN / FAIL / INFO
    detail: str = ""


@dataclass
class Reconciliation:
    checks: List[Check] = field(default_factory=list)
    fingerprints: Dict[str, Dict[str, Fingerprint]] = field(default_factory=dict)

    def add(self, *a, **k):
        self.checks.append(Check(*a, **k))

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for c in self.checks:
            out[c.status] = out.get(c.status, 0) + 1
        return out


def _file_hash(out_dir: str, rel: str) -> str:
    try:
        with open(os.path.join(out_dir, rel), "rb") as fh:
            return "sha256:" + hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return "missing-file:" + rel


def _blob_columns(r: TableResult) -> set:
    h = r.header
    return {f.name for f in h.data_fields
            if (f.type in h.memo_types and f.type != "M") or (f.type == "M" and f.flags & 0x04) or f.type == "Q"}


def fp_dbfread(r: TableResult) -> Fingerprint:
    import dbfread
    from dbfread.field_parser import FieldParser

    class LenientParser(FieldParser):
        # The toolkit's reader reports invalid dates as empty and counts them;
        # mirror that so the comparison tests everything else.
        def parseD(self, field, data):
            try:
                return super().parseD(field, data)
            except ValueError:
                return None

        def parseN(self, field, data):
            try:
                return super().parseN(field, data)
            except ValueError:
                return None

    cols = [f.name for f in r.header.data_fields]
    fp = Fingerprint(cols + ["_deleted"])
    source = r.source
    h = r.header
    tmpdir = None
    if h.file_size > h.header_length + h.records_physically_present * h.record_length + 1:
        # dbfread reads until end of file and parses a trailing partial record
        # as a row. Compare it on the complete records only, using a trimmed copy.
        import shutil
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="xbx_")
        source = os.path.join(tmpdir, os.path.basename(r.source))
        with open(r.source, "rb") as fi, open(source, "wb") as fo:
            fo.write(fi.read(h.header_length + h.records_physically_present * h.record_length))
            fo.write(b"\x1a")
        if r.stats.memo_file:
            shutil.copy(r.stats.memo_file, os.path.join(tmpdir, os.path.basename(r.stats.memo_file)))
    t = dbfread.DBF(source, encoding=r.header.codepage, char_decode_errors="replace",
                    ignore_missing_memofile=True, parserclass=LenientParser, load=False)
    for deleted, it in ((False, t.records), (True, t.deleted)):
        for rec in it:
            row = {c: canon(rec.get(c)) for c in cols}
            row["_deleted"] = "1" if deleted else "0"
            fp.add_canonical(row)
    fp.trimmed = bool(tmpdir)  # type: ignore[attr-defined]
    if tmpdir:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    return fp


def fp_csv(r: TableResult, out_dir: str) -> Fingerprint:
    fields = {f.name: f for f in r.header.data_fields}
    cols = list(fields)
    blobs = _blob_columns(r)
    fp = Fingerprint(cols + ["_deleted"])
    for sub in ("csv", os.path.join("csv", "deleted")):
        with open(os.path.join(out_dir, sub, f"{r.name}.csv"), newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                out = {}
                for c in cols:
                    v = row[c]
                    if c in blobs and v:
                        out[c] = _file_hash(out_dir, v)
                    else:
                        out[c] = canon_from_text(v, fields[c].type, fields[c].decimals)
                out["_deleted"] = row["_deleted"]
                fp.add_canonical(out)
    return fp


def _canon_typed(v, ftype: str, decimals: int, blob: bool, out_dir: str) -> str:
    if v is None or v == "":
        return ""
    if blob:
        return _file_hash(out_dir, v)
    return canon_from_text(str(v), ftype, decimals)


def fp_sqlite(r: TableResult, con: sqlite3.Connection, out_dir: str) -> Fingerprint:
    fields = r.header.data_fields
    cols = [f.name for f in fields]
    blobs = _blob_columns(r)
    fp = Fingerprint(cols + ["_deleted"])
    sel = ", ".join(sql_ident(c) for c in cols) + ", _deleted"
    for row in con.execute(f"SELECT {sel} FROM {sql_ident(r.name)}"):
        out = {f.name: _canon_typed(v, f.type, f.decimals, f.name in blobs, out_dir) for f, v in zip(fields, row)}
        out["_deleted"] = str(row[-1])
        fp.add_canonical(out)
    return fp


def fp_postgres(r: TableResult, psql: List[str], out_dir: str) -> Fingerprint:
    fields = r.header.data_fields
    cols = [f.name for f in fields]
    blobs = _blob_columns(r)
    fp = Fingerprint(cols + ["_deleted"])
    sel = ", ".join(sql_ident(c) for c in cols) + ", _deleted::int"
    cmd = psql + ["-X", "-q", "-v", "ON_ERROR_STOP=1", "-c",
                  f"\\copy (SELECT {sel} FROM {sql_ident(r.name)}) TO STDOUT WITH (FORMAT csv)"]
    proc = subprocess.run(cmd, check=True, capture_output=True)
    # Decode without newline translation: memo text keeps its CRLF line breaks.
    for row in csv.reader(io.StringIO(proc.stdout.decode("utf-8"), newline="")):
        out = {f.name: _canon_typed(v, f.type, f.decimals, f.name in blobs, out_dir) for f, v in zip(fields, row)}
        out["_deleted"] = row[-1]
        fp.add_canonical(out)
    return fp


def load_postgres(psql: List[str], out_dir: str) -> str:
    proc = subprocess.run(psql + ["-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", "schema_postgres.sql",
                                  "-f", "load_postgres.sql"],
                          cwd=out_dir, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-2000:])
    ver = subprocess.run(psql + ["-X", "-At", "-c", "SHOW server_version"], capture_output=True, text=True)
    return ver.stdout.strip()


def reconcile(results: List[TableResult], con: sqlite3.Connection, out_dir: str,
              psql: Optional[List[str]] = None) -> Reconciliation:
    rec = Reconciliation()
    pg_version = None
    if psql:
        try:
            pg_version = load_postgres(psql, out_dir)
            rec.add("*", "postgres-load", "PASS", f"loaded with generated scripts into PostgreSQL {pg_version}")
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            rec.add("*", "postgres-load", "FAIL", str(e))
            psql = None

    for r in results:
        if r.error:
            rec.add(r.name, "parse", "FAIL", r.error)
            continue
        h, s = r.header, r.stats
        # Structural checks
        if s.missing_records:
            rec.add(r.name, "record-count", "FAIL",
                    f"header declares {h.record_count:,} records, file holds {h.records_physically_present:,}: "
                    f"{s.missing_records:,} missing (truncated or damaged table). Recovered rows exported.")
        else:
            rec.add(r.name, "record-count", "PASS",
                    f"{h.record_count:,} declared = {s.records_read:,} read "
                    f"({r.live_rows:,} live, {r.deleted_rows:,} flagged deleted)")
        if s.memo_missing:
            rec.add(r.name, "memo-file", "FAIL", "table has memo fields but no .FPT/.DBT file")
        if s.memo_errors:
            rec.add(r.name, "memo-pointers", "WARN",
                    "unreadable memo pointers: " + ", ".join(f"{k}={v}" for k, v in s.memo_errors.items()))
        if s.decode_errors:
            rec.add(r.name, "text-encoding", "WARN",
                    f"bytes not valid in {h.codepage}: " + ", ".join(f"{k}={v}" for k, v in s.decode_errors.items()))
        if s.bad_values:
            rec.add(r.name, "field-values", "WARN",
                    "unparseable values exported as empty: " + ", ".join(f"{k}={v}" for k, v in s.bad_values.items()))

        fps = {"toolkit": r.fingerprint}
        try:
            fps["dbfread"] = fp_dbfread(r)
            if getattr(fps["dbfread"], "trimmed", False):
                rec.add(r.name, "dbfread-partial-record", "INFO",
                        "file ends in a partial record; dbfread would parse that fragment as a row, "
                        "so it was compared on complete records only")
        except Exception as e:  # noqa: BLE001
            rec.add(r.name, "dbfread", "WARN", f"independent reader could not read table: {e}")
        fps["csv"] = fp_csv(r, out_dir)
        fps["sqlite"] = fp_sqlite(r, con, out_dir)
        if psql:
            try:
                fps["postgres"] = fp_postgres(r, psql, out_dir)
            except subprocess.CalledProcessError as e:
                rec.add(r.name, "postgres", "FAIL", e.stderr[-500:])
        rec.fingerprints[r.name] = fps
        base = fps["toolkit"]
        for label, fp in fps.items():
            if label == "toolkit":
                continue
            d = base.diff(fp)
            if d:
                rec.add(r.name, f"values-{label}", "FAIL",
                        f"{len(d)} column(s) differ: " + "; ".join(f"{k}: {v}" for k, v in list(d.items())[:5]))
            else:
                rec.add(r.name, f"values-{label}", "PASS",
                        f"{fp.rows:,} rows x {len(fp.columns) - 1} columns identical to source")
    return rec
