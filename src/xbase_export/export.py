"""Export one xBase table to CSV, SQLite and PostgreSQL load scripts in one pass.

Outputs per table:
  csv/<table>.csv            live records, UTF-8 with BOM (opens cleanly in Excel)
  csv/deleted/<table>.csv    records flagged deleted in the source, same columns
  blobs/<table>/<field>/...  binary memo / OLE / blob values, one file each
  rows in export.sqlite      both live and deleted, with _deleted = 1/0
The column profile and the canonical fingerprint are accumulated as rows are
written, so the validator can compare them against independent re-reads.
"""
from __future__ import annotations

import csv
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .canon import Fingerprint, canon
from .header import FieldDef, TableHeader
from .inventory import MAGIC
from .reader import TableReader, ReadStats

META_COLS = ["_source_row", "_deleted"]
PG_RESERVED = {"user", "order", "group", "desc", "date", "time", "type", "end", "default",
               "check", "limit", "offset", "table", "column", "select", "from", "where", "to",
               "primary", "references", "case", "when", "then", "else", "all", "any", "as"}


def sql_ident(name: str) -> str:
    n = re.sub(r"[^0-9a-zA-Z_]", "_", name).lower()
    if not n or n[0].isdigit():
        n = "_" + n
    return f'"{n}"'


def table_name(path: str) -> str:
    return re.sub(r"[^0-9a-zA-Z_]", "_", os.path.splitext(os.path.basename(path))[0]).lower()


def pg_type(f: FieldDef, h: TableHeader) -> str:
    t = f.type
    if t in "CV":
        return f"VARCHAR({max(f.length, 1)})"
    if t in "NF":
        if f.decimals == 0:
            return "INTEGER" if f.length <= 9 else "BIGINT" if f.length <= 18 else f"NUMERIC({f.length},0)"
        return f"NUMERIC({f.length},{f.decimals})"
    if t == "D":
        return "DATE"
    if t in "T@":
        return "TIMESTAMP"
    if t == "L":
        return "BOOLEAN"
    if t in "I+":
        return "INTEGER"
    if t == "Y":
        return "NUMERIC(19,4)"
    if t == "B" and h.is_vfp or t == "O":
        return "DOUBLE PRECISION"
    return "TEXT"  # memo text, or a relative path to an extracted binary file


def sqlite_type(f: FieldDef, h: TableHeader) -> str:
    p = pg_type(f, h)
    if p.startswith(("VARCHAR", "TEXT")):
        return "TEXT"
    if p in ("INTEGER", "BIGINT") or p.endswith(",0)"):
        return "INTEGER"
    if p == "BOOLEAN":
        return "INTEGER"
    if p in ("DATE", "TIMESTAMP"):
        return "TEXT"
    return "REAL"


def csv_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, datetime):
        s = v.strftime("%Y-%m-%d %H:%M:%S")
        return s + (f".{v.microsecond // 1000:03d}" if v.microsecond else "")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return format(v, "f")
    return str(v)


def sqlite_value(v: Any) -> Any:
    if v is None or isinstance(v, (int, float, str)):
        return v  # bool is an int subclass and stores as 0/1
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() and v.as_tuple().exponent >= 0 else float(v)
    return csv_text(v)


def blob_extension(data: bytes) -> str:
    for sig, mime in MAGIC:
        if data.startswith(sig):
            return {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/bmp": ".bmp",
                    "image/tiff": ".tif", "application/pdf": ".pdf", "audio/wav": ".wav",
                    "audio/mpeg": ".mp3", "application/rtf": ".rtf", "application/zip": ".zip",
                    "application/x-ole-storage": ".ole", "application/dicom": ".dcm"}.get(mime, ".bin")
    return ".bin"


@dataclass
class ColumnProfile:
    name: str
    ftype: str
    length: int
    decimals: int
    nonempty: int = 0
    distinct: set = field(default_factory=set)
    distinct_capped: bool = False
    min: Any = None
    max: Any = None
    max_len: int = 0
    examples: List[str] = field(default_factory=list)

    CAP = 200_000

    def add(self, v: Any):
        if v is None or v == "":
            return
        self.nonempty += 1
        key = canon(v)
        if not self.distinct_capped:
            self.distinct.add(key)
            if len(self.distinct) > self.CAP:
                self.distinct_capped = True
        if isinstance(v, (int, float, Decimal, date, datetime)) and not isinstance(v, bool):
            if self.min is None or v < self.min:
                self.min = v
            if self.max is None or v > self.max:
                self.max = v
        if isinstance(v, str):
            self.max_len = max(self.max_len, len(v))
        if len(self.examples) < 3 and key not in self.examples and not isinstance(v, bytes):
            self.examples.append(key[:60])


@dataclass
class TableResult:
    name: str
    source: str
    header: TableHeader
    stats: ReadStats
    live_rows: int = 0
    deleted_rows: int = 0
    blobs_written: int = 0
    profiles: Dict[str, ColumnProfile] = field(default_factory=dict)
    fingerprint: Optional[Fingerprint] = None
    error: Optional[str] = None


def export_table(path: str, out_dir: str, con: sqlite3.Connection,
                 codepage: Optional[str] = None) -> TableResult:
    reader = TableReader(path, codepage)
    h = reader.header
    name = table_name(path)
    fields = h.data_fields
    cols = [f.name for f in fields]
    res = TableResult(name, path, h, reader.stats)
    res.profiles = {f.name: ColumnProfile(f.name, f.type, f.length, f.decimals) for f in fields}
    res.fingerprint = Fingerprint(cols + ["_deleted"])

    os.makedirs(os.path.join(out_dir, "csv", "deleted"), exist_ok=True)
    live_path = os.path.join(out_dir, "csv", f"{name}.csv")
    del_path = os.path.join(out_dir, "csv", "deleted", f"{name}.csv")
    header_row = cols + META_COLS

    sql_cols = ", ".join(f"{sql_ident(f.name)} {sqlite_type(f, h)}" for f in fields)
    con.execute(f"DROP TABLE IF EXISTS {sql_ident(name)}")
    con.execute(f"CREATE TABLE {sql_ident(name)} ({sql_cols}, _source_row INTEGER, _deleted INTEGER)")
    placeholders = ", ".join("?" * (len(cols) + 2))
    insert = f"INSERT INTO {sql_ident(name)} VALUES ({placeholders})"

    blob_fields = {f.name for f in fields if f.type in h.memo_types and f.type != "M"} | \
                  {f.name for f in fields if f.type == "M" and f.flags & 0x04} | \
                  {f.name for f in fields if f.type == "Q"}

    batch: List[tuple] = []
    with open(live_path, "w", newline="", encoding="utf-8-sig") as lf, \
            open(del_path, "w", newline="", encoding="utf-8-sig") as dfh:
        lw, dw = csv.writer(lf), csv.writer(dfh)
        lw.writerow(header_row)
        dw.writerow(header_row)
        for rec in reader:
            vals = rec.values
            for bf in blob_fields:
                data = vals.get(bf)
                if isinstance(data, (bytes, bytearray)) and data:
                    rel = os.path.join("blobs", name, bf.lower(), f"{rec.row:07d}{blob_extension(data)}")
                    full = os.path.join(out_dir, rel)
                    os.makedirs(os.path.dirname(full), exist_ok=True)
                    with open(full, "wb") as bfh:
                        bfh.write(data)
                    res.blobs_written += 1
                    res.profiles[bf].add(data)
                    vals = dict(vals)
                    vals[bf] = rel.replace(os.sep, "/")
            row_text = [csv_text(vals[c]) for c in cols] + [str(rec.row), "1" if rec.deleted else "0"]
            (dw if rec.deleted else lw).writerow(row_text)
            batch.append(tuple(sqlite_value(vals[c]) for c in cols) + (rec.row, 1 if rec.deleted else 0))
            if len(batch) >= 5000:
                con.executemany(insert, batch)
                batch.clear()
            if rec.deleted:
                res.deleted_rows += 1
            else:
                res.live_rows += 1
                for c in cols:
                    if c not in blob_fields:
                        res.profiles[c].add(vals[c])
            # Fingerprint over the source values (blob fields hashed by content).
            fp_row = {c: canon(rec.values[c]) for c in cols}
            fp_row["_deleted"] = "1" if rec.deleted else "0"
            res.fingerprint.add_canonical(fp_row)
    if batch:
        con.executemany(insert, batch)
    con.commit()
    return res


def write_postgres_scripts(results: List[TableResult], out_dir: str):
    """schema_postgres.sql (DDL) and load_postgres.sql (psql \\copy from the CSVs)."""
    ddl = ["-- PostgreSQL schema generated from the source table headers.",
           "-- Column widths and numeric precision follow the source definitions.",
           "BEGIN;"]
    load = ["-- Run from the export folder:  psql -d <database> -f schema_postgres.sql -f load_postgres.sql",
            "\\set ON_ERROR_STOP on", "BEGIN;"]
    for r in results:
        if r.error:
            continue
        h = r.header
        cols = [f"  {sql_ident(f.name)} {pg_type(f, h)}" for f in h.data_fields]
        cols += ["  _source_row INTEGER NOT NULL", "  _deleted BOOLEAN NOT NULL DEFAULT FALSE"]
        ddl.append(f"DROP TABLE IF EXISTS {sql_ident(r.name)};")
        ddl.append(f"CREATE TABLE {sql_ident(r.name)} (\n" + ",\n".join(cols) + "\n);")
        for sub in ("csv", "csv/deleted"):
            load.append(f"\\copy {sql_ident(r.name)} FROM '{sub}/{r.name}.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')")
    ddl.append("COMMIT;")
    load.append("COMMIT;")
    with open(os.path.join(out_dir, "schema_postgres.sql"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(ddl) + "\n")
    with open(os.path.join(out_dir, "load_postgres.sql"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(load) + "\n")
