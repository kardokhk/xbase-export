"""Write the data dictionary, relationship list, schema diagram and validation report."""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional

from .canon import canon
from .export import TableResult, pg_type
from .relations import Relation
from .validate import Reconciliation


def _fmt(v: Any) -> str:
    return "" if v is None else canon(v)


def dictionary_rows(results: List[TableResult], examples: bool) -> (List[dict], List[dict]):
    tables, columns = [], []
    for r in results:
        h = r.header
        tables.append({
            "table": r.name,
            "source_file": os.path.basename(r.source),
            "format": h.version_name,
            "codepage": h.codepage,
            "last_updated": h.last_update.isoformat() if h.last_update else "",
            "records_declared": h.record_count,
            "records_read": r.stats.records_read,
            "live": r.live_rows,
            "deleted": r.deleted_rows,
            "fields": len(h.data_fields),
            "memo_file": os.path.basename(r.stats.memo_file) if r.stats.memo_file else "",
            "memo_bytes": r.stats.memo_bytes,
            "blobs_extracted": r.blobs_written,
            "status": "ERROR: " + r.error if r.error else ("TRUNCATED" if r.stats.missing_records else "OK"),
        })
        for f in h.data_fields:
            p = r.profiles[f.name]
            columns.append({
                "table": r.name,
                "column": f.name,
                "xbase_type": f"{f.type} ({f.type_name})",
                "length": f.length,
                "decimals": f.decimals,
                "postgres_type": pg_type(f, h),
                "filled_pct": round(100 * p.nonempty / r.live_rows, 1) if r.live_rows else 0.0,
                "distinct": (f">{p.CAP:,}" if p.distinct_capped else len(p.distinct)),
                "min": _fmt(p.min) if examples or f.type in "DT" else "",
                "max": _fmt(p.max) if examples or f.type in "DT" else "",
                "max_text_length": p.max_len or "",
                "examples": " | ".join(p.examples) if examples else "(suppressed)",
            })
    return tables, columns


def write_dictionary(results: List[TableResult], rels: List[Relation], out_dir: str, examples: bool):
    tables, columns = dictionary_rows(results, examples)
    for name, rows in (("tables", tables), ("columns", columns)):
        with open(os.path.join(out_dir, f"dictionary_{name}.csv"), "w", newline="", encoding="utf-8-sig") as fh:
            if rows:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
    rel_rows = [{"child_table": x.child_table, "child_column": x.child_column,
                 "parent_table": x.parent_table, "parent_column": x.parent_column,
                 "rows_with_value": x.child_values, "matched": x.matched, "orphans": x.orphans,
                 "orphans_parent_deleted": x.orphans_parent_deleted, "orphans_parent_absent": x.orphans_absent,
                 "coverage_pct": round(100 * x.coverage, 2), "basis": x.basis} for x in rels]
    with open(os.path.join(out_dir, "relationships.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rel_rows[0]) if rel_rows else ["child_table"])
        w.writeheader()
        w.writerows(rel_rows)
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        return None
    wb = Workbook()
    sheets = [("Tables", tables), ("Columns", columns), ("Relationships", rel_rows)]
    for i, (title, rows) in enumerate(sheets):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = title
        if not rows:
            continue
        keys = list(rows[0])
        ws.append(keys)
        for row in rows:
            ws.append([row[k] for k in keys])
        for row in ws.iter_rows():
            for c in row:
                c.font = Font(name="Helvetica", size=10, color="000000", bold=(c.row == 1))
        for c in ws[1]:
            c.border = Border(bottom=Side(style="thin", color="000000"))
            c.alignment = Alignment(vertical="center")
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for j, k in enumerate(keys, 1):
            width = max(len(str(k)), *(len(str(r[k])) for r in rows[:500]))
            ws.column_dimensions[get_column_letter(j)].width = min(max(width + 2, 8), 60)
    path = os.path.join(out_dir, "data_dictionary.xlsx")
    wb.save(path)
    return path


def write_schema_dot(results: List[TableResult], rels: List[Relation], keys: Dict[str, List[str]],
                     out_dir: str, font: str = "Helvetica", keys_only: bool = False,
                     stem: str = "schema") -> Optional[str]:
    """Graphviz schema diagram. keys_only=True keeps key columns only, for print at page width."""
    lines = ["digraph schema {", f'  graph [rankdir=LR, splines=true, nodesep={0.2 if keys_only else 0.35}, '
             f'ranksep={0.4 if keys_only else 0.9}, bgcolor="white", fontname="{font}"];',
             f'  node [shape=plain, fontname="{font}", fontsize={12 if keys_only else 9}];',
             f'  edge [fontname="{font}", fontsize={11 if keys_only else 8}, color="black", penwidth=0.7, arrowsize=0.6];']
    linked_cols = {(x.child_table, x.child_column) for x in rels}
    for r in results:
        if r.error:
            continue
        rows = [f'<tr><td colspan="2"><b>{r.name.upper()}</b>  ({r.live_rows:,})</td></tr>']
        for f in r.header.data_fields:
            is_fk = (r.name, f.name) in linked_cols
            mark = "FK" if is_fk else ("PK" if f.name in keys.get(r.name, [])[:1] else "")
            if not mark and (keys_only or (len(r.header.data_fields) > 24 and f.type not in "M")):
                continue  # keep wide tables legible: show keys and memo fields only
            rows.append(f'<tr><td align="left" port="{f.name}_in">{f.name}</td>'
                        f'<td align="left" port="{f.name}_out">{f.type}{f.length if f.type in "CN" else ""}'
                        f'{" " + mark if mark else ""}</td></tr>')
        lines.append(f'  {r.name} [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="3">'
                     + "".join(rows) + "</table>>];")
    for x in rels:
        absent = getattr(x, "orphans_absent", 0)
        label = f"{absent:,} missing parent" + ("s" if absent != 1 else "") if absent else ""
        colour = ', color="#D55E00", fontcolor="#D55E00", penwidth=1.6, style=dashed' if absent else ""
        lines.append(f'  {x.child_table}:{x.child_column}_out:e -> {x.parent_table}:{x.parent_column}_in:w '
                     f'[label="{label}"{colour}];')
    lines.append("}")
    dot_path = os.path.join(out_dir, f"{stem}.dot")
    with open(dot_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    dot = shutil.which("dot")
    if dot:
        for fmt in ("svg", "png"):
            subprocess.run([dot, f"-T{fmt}", "-Gdpi=200", dot_path, "-o",
                            os.path.join(out_dir, f"{stem}.{fmt}")], check=False)
    return dot_path


def write_report(summary: Dict[str, Any], rec: Reconciliation, out_dir: str):
    data = dict(summary)
    data["checks"] = [c.__dict__ for c in rec.checks]
    data["check_counts"] = rec.counts()
    with open(os.path.join(out_dir, "validation_report.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)

    md = [f"# Extraction and validation report", "",
          f"Generated {datetime.now().isoformat(timespec='seconds')} by xbase-export {summary.get('version', '')}.", "",
          "## Summary", ""]
    for k, v in summary.get("headline", {}).items():
        md.append(f"- {k}: {v}")
    md += ["", "## Checks", "", "| Table | Check | Status | Detail |", "|---|---|---|---|"]
    order = {"FAIL": 0, "WARN": 1, "PASS": 2, "INFO": 3}
    for c in sorted(rec.checks, key=lambda c: (order.get(c.status, 9), c.table, c.check)):
        md.append(f"| {c.table} | {c.check} | {c.status} | {c.detail.replace('|', '/')} |")
    with open(os.path.join(out_dir, "validation_report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")
