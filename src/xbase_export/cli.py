"""Command-line entry point.

    xbase-export run  SOURCE_DIR OUTPUT_DIR [--no-snapshot] [--examples]
                      [--psql "psql -h host -d db"] [--codepage cp1252]
    xbase-export inspect TABLE.DBF
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sqlite3
import sys
import time
from datetime import datetime

from . import __version__
from .documents import link_documents, write_document_index
from .export import TableResult, export_table, write_postgres_scripts
from .header import read_header
from .inventory import build_inventory, snapshot, summarise, write_manifest
from .reader import ReadStats
from .relations import candidate_keys, count_orphans, infer_relations
from .report import write_dictionary, write_report, write_schema_dot
from .validate import reconcile
from .export import sql_ident
from .canon import canon
import csv


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def cmd_inspect(args):
    h = read_header(args.table)
    print(json.dumps({
        "format": h.version_name, "codepage": h.codepage, "last_update": str(h.last_update),
        "records_declared": h.record_count, "records_present": h.records_physically_present,
        "header_length": h.header_length, "record_length": h.record_length,
        "fields": [{"name": f.name, "type": f.type, "length": f.length, "decimals": f.decimals,
                    "nullable": f.nullable} for f in h.fields],
    }, indent=2))


def cmd_run(args):
    t0 = time.time()
    src = os.path.abspath(args.source)
    out = os.path.abspath(args.output)
    if out.startswith(src + os.sep) or out == src:
        sys.exit("Output folder must be outside the source folder (the source is never written to).")
    os.makedirs(out, exist_ok=True)

    log(f"Inventory of {src}")
    entries = build_inventory(src)
    write_manifest(entries, os.path.join(out, "source_manifest.csv"))
    kinds = summarise(entries)
    log("  " + ", ".join(f"{k}: {v['files']:,} files" for k, v in sorted(kinds.items())))

    work = src
    snap_info = None
    if not args.no_snapshot:
        work = os.path.abspath(args.snapshot_dir) if args.snapshot_dir else os.path.join(out, "snapshot")
        if work.startswith(src + os.sep) or work == src:
            sys.exit("Snapshot folder must be outside the source folder.")
        log("Snapshot copy with SHA-256 verification")
        snap_info = snapshot(src, work, entries)
        if snap_info["hash_mismatches"]:
            sys.exit(f"Snapshot verification failed for {snap_info['hash_mismatches']} file(s); stopping.")
        log(f"  {snap_info['copied']:,} files copied, all hashes match")

    con = sqlite3.connect(os.path.join(out, "export.sqlite"))
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    results = []
    for e in entries:
        if e.kind not in ("table", "unreadable-table"):
            continue
        path = os.path.join(work, e.relpath)
        try:
            r = export_table(path, out, con, args.codepage)
            log(f"  {e.relpath}: {r.live_rows:,} live, {r.deleted_rows:,} deleted"
                + (f", {r.stats.missing_records:,} MISSING" if r.stats.missing_records else ""))
        except Exception as ex:  # noqa: BLE001 - one bad table must not stop the run
            from .header import TableHeader
            r = TableResult(os.path.splitext(os.path.basename(path))[0].lower(), path,
                            None, ReadStats(), error=f"{type(ex).__name__}: {ex}")  # type: ignore[arg-type]
            log(f"  {e.relpath}: ERROR {r.error}")
        results.append(r)
    ok = [r for r in results if not r.error]
    write_postgres_scripts(ok, out)

    log("Inferring keys and relationships")
    keys = candidate_keys(ok)
    rels = infer_relations(ok)
    for x in rels:
        vals = (canon(v) for (v,) in con.execute(
            f"SELECT {sql_ident(x.child_column)} FROM {sql_ident(x.child_table)} WHERE _deleted = 0"))
        deleted_parents = {canon(v) for (v,) in con.execute(
            f"SELECT {sql_ident(x.parent_column)} FROM {sql_ident(x.parent_table)} WHERE _deleted = 1")}
        count_orphans(x, vals, deleted_parents)
    log(f"  {sum(len(v) for v in keys.values())} candidate keys, {len(rels)} relationships")

    # Hub table: the one most other tables point at (usually patients).
    hub_keys = set()
    owner_columns = {}
    if rels:
        from collections import Counter
        hub, _ = Counter(x.parent_table for x in rels).most_common(1)[0]
        hub_col = next(x.parent_column for x in rels if x.parent_table == hub)
        hub_keys = {str(v) for (v,) in con.execute(
            f"SELECT {sql_ident(hub_col)} FROM {sql_ident(hub)} WHERE _deleted = 0")}
        for x in rels:
            if x.parent_table == hub:
                owner_columns[x.child_table] = x.child_column
        log(f"  hub table: {hub}.{hub_col}")

    log("Linking documents and images")
    links, unref = link_documents(con, ok, entries, owner_columns, hub_keys)
    write_document_index(os.path.join(out, "documents_index.csv"), entries, links, hub_keys)
    missing = [l for l in links if not l.exists]

    with open(os.path.join(out, "data_issues.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["table", "source_row", "field", "raw_value_as_stored", "issue"])
        for r in ok:
            for row, fname, raw in r.stats.issues:
                w.writerow([r.name, row, fname, raw, "unparseable; exported as empty"])
            if r.stats.missing_records:
                w.writerow([r.name, "", "", "", f"{r.stats.missing_records} records declared in header "
                                                 f"but absent from file (truncated)"])

    write_dictionary(ok, rels, out, args.examples)
    write_schema_dot(ok, rels, keys, out, font=args.font)
    write_schema_dot(ok, rels, keys, out, font=args.font, keys_only=True, stem="schema_keys")

    log("Reconciling: toolkit reader vs dbfread vs CSV vs SQLite" + (" vs PostgreSQL" if args.psql else ""))
    rec = reconcile(results, con, out, shlex.split(args.psql) if args.psql else None)
    for x in rels:
        status = "WARN" if x.orphans_absent else ("INFO" if x.orphans else "PASS")
        detail = f"{x.matched:,} of {x.child_values:,} rows match a live {x.parent_table}.{x.parent_column}"
        if x.orphans_parent_deleted:
            detail += f"; {x.orphans_parent_deleted:,} point at a row flagged deleted in {x.parent_table} (recovered)"
        if x.orphans_absent:
            detail += f"; {x.orphans_absent:,} point at no row in {x.parent_table} at all (kept, listed)"
        rec.add(x.child_table, f"link {x.child_column}->{x.parent_table}", status, detail)
    docs = [e for e in entries if e.kind == "document"]
    rec.add("documents", "referenced-files", "PASS" if not missing else "WARN",
            f"{len(links) - len(missing):,} of {len(links):,} file references resolve to a file"
            + (f"; {len(missing):,} referenced files do not exist" if missing else ""))
    folder_linked = sum(1 for e in unref if any(p in hub_keys for p in e.relpath.split("/")[:-1]))
    rec.add("documents", "unreferenced-files", "INFO",
            f"{len(docs):,} document files; {len(unref):,} not referenced by any table, "
            f"of which {folder_linked:,} are attributed by folder name")
    con.close()

    elapsed = time.time() - t0
    total_live = sum(r.live_rows for r in ok)
    total_del = sum(r.deleted_rows for r in ok)
    summary = {
        "version": __version__,
        "source": src,
        "snapshot": snap_info,
        "elapsed_seconds": round(elapsed, 1),
        "inventory": kinds,
        "headline": {
            "Tables exported": f"{len(ok)} of {len(results)}",
            "Live records": f"{total_live:,}",
            "Deleted-flagged records recovered": f"{total_del:,}",
            "Memo text recovered (MB)": f"{sum(r.stats.memo_bytes for r in ok) / 1e6:.1f}",
            "Relationships inferred": f"{len(rels)}",
            "Document files indexed": f"{len(docs):,}",
            "Run time (s)": f"{elapsed:.1f}",
        },
        "relationships": [x.__dict__ | {"_missing": None} for x in rels],
    }
    for x in summary["relationships"]:
        x.pop("_missing", None)
    write_report(summary, rec, out)
    counts = rec.counts()
    log(f"Done in {elapsed:.1f} s. Checks: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return 1 if counts.get("FAIL") and args.strict else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xbase-export", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="inventory, snapshot, export, validate")
    r.add_argument("source")
    r.add_argument("output")
    r.add_argument("--no-snapshot", action="store_true", help="read the source in place (it is still never written)")
    r.add_argument("--snapshot-dir", help="where to put the verified snapshot (default OUTPUT/snapshot), "
                                          "e.g. an encrypted drive")
    r.add_argument("--examples", action="store_true",
                   help="include example values in the data dictionary (off by default: they may be patient data)")
    r.add_argument("--psql", help='psql command to load and verify PostgreSQL, e.g. "psql -h localhost -d ehr"')
    r.add_argument("--codepage", help="override the codepage declared in the table headers")
    r.add_argument("--font", default="Helvetica", help="font for the schema diagram")
    r.add_argument("--strict", action="store_true", help="exit 1 if any check fails")
    r.set_defaults(func=cmd_run)
    i = sub.add_parser("inspect", help="print one table's header")
    i.add_argument("table")
    i.set_defaults(func=cmd_inspect)
    args = ap.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
