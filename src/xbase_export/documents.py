"""Index scanned documents, X-rays, sound files and other attachments.

Two kinds of link are recovered:
  * a table column holds a file path (absolute, mapped drive or relative);
  * a folder in the path is named after a key value (for example a patient ID).
Each file gets its SHA-256, MIME type from its magic bytes, and the record it
belongs to. Referenced files that do not exist, and files nobody references,
are both listed.
"""
from __future__ import annotations

import csv
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from .export import TableResult, sql_ident
from .inventory import FileEntry

PATHLIKE = re.compile(r"[\\/].*\.[A-Za-z0-9]{2,4}$")


@dataclass
class DocLink:
    relpath: str
    exists: bool
    table: str
    column: str
    source_row: int
    owner_key: str
    raw_path: str


def path_columns(results: List[TableResult], min_fraction: float = 0.5) -> List[Tuple[str, str]]:
    cols = []
    for r in results:
        for c, p in r.profiles.items():
            if p.ftype not in "CVM" or p.nonempty == 0:
                continue
            sample = list(p.distinct)[:500] if not p.distinct_capped else p.examples
            if sample and sum(bool(PATHLIKE.search(s)) for s in sample) / len(sample) >= min_fraction:
                cols.append((r.name, c))
    return cols


class PathResolver:
    def __init__(self, entries: List[FileEntry]):
        self.by_lower: Dict[str, str] = {e.relpath.lower(): e.relpath for e in entries}

    def resolve(self, raw: str) -> Optional[str]:
        p = raw.strip().replace("\\", "/")
        p = re.sub(r"^[A-Za-z]:/", "", p).lstrip("/")
        parts = [x for x in p.split("/") if x]
        for i in range(len(parts)):
            cand = "/".join(parts[i:]).lower()
            if cand in self.by_lower:
                return self.by_lower[cand]
        return None


def link_documents(con: sqlite3.Connection, results: List[TableResult], entries: List[FileEntry],
                   owner_columns: Dict[str, str], hub_keys: Set[str]) -> Tuple[List[DocLink], List[FileEntry]]:
    """Return (links from tables, unreferenced document files).

    owner_columns maps table -> column that identifies the owning record
    (usually the patient key) so each document can be attributed.
    """
    resolver = PathResolver(entries)
    links: List[DocLink] = []
    referenced: Set[str] = set()
    for table, col in path_columns(results):
        owner = owner_columns.get(table)
        sel = f"SELECT {sql_ident(col)}, _source_row" + (f", {sql_ident(owner)}" if owner else ", ''") + \
              f" FROM {sql_ident(table)} WHERE _deleted = 0 AND {sql_ident(col)} IS NOT NULL"
        for raw, row, key in con.execute(sel):
            if not raw:
                continue
            rel = resolver.resolve(raw)
            if rel:
                referenced.add(rel)
            links.append(DocLink(rel or raw, rel is not None, table, col, row, str(key or ""), raw))
    docs = [e for e in entries if e.kind == "document"]
    unreferenced = [e for e in docs if e.relpath not in referenced]
    return links, unreferenced


def folder_owner(relpath: str, hub_keys: Set[str]) -> str:
    for part in relpath.split("/")[:-1]:
        if part in hub_keys:
            return part
    return ""


def write_document_index(path: str, entries: List[FileEntry], links: List[DocLink], hub_keys: Set[str]):
    by_rel = {e.relpath: e for e in entries}
    linked = {}
    for l in links:
        if l.exists:
            linked.setdefault(l.relpath, l)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["relpath", "exists", "size", "sha256", "mime", "owner_key", "linked_by",
                    "table", "source_row", "raw_reference"])
        for l in links:
            e = by_rel.get(l.relpath)
            w.writerow([l.relpath, l.exists, e.size if e else "", e.sha256 if e else "", e.mime if e else "",
                        l.owner_key, f"{l.table}.{l.column}", l.table, l.source_row, l.raw_path])
        for e in entries:
            if e.kind != "document" or e.relpath in linked:
                continue
            owner = folder_owner(e.relpath, hub_keys)
            w.writerow([e.relpath, True, e.size, e.sha256, e.mime, owner,
                        "folder-name" if owner else "unlinked", "", "", ""])
