"""Inventory and snapshot of the source folder.

Nothing in the source folder is opened for writing. The snapshot copy is
verified by SHA-256 against the original before any parsing starts.
"""
from __future__ import annotations

import csv
import hashlib
import os
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List

from .header import is_dbf

INDEX_EXT = {".cdx", ".idx", ".ndx", ".mdx", ".ntx"}
MEMO_EXT = {".fpt", ".dbt"}
PROGRAM_EXT = {".exe", ".dll", ".fxp", ".app", ".prg", ".scx", ".sct", ".frx", ".frt",
               ".vcx", ".vct", ".mnx", ".mnt", ".ocx", ".bat", ".ini", ".cfg", ".log", ".tmp"}
ARCHIVE_EXT = {".zip", ".7z", ".rar", ".bak", ".gz", ".tar"}

MAGIC = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF8", "image/gif"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"%PDF", "application/pdf"),
    (b"RIFF", "audio/wav"),
    (b"ID3", "audio/mpeg"),
    (b"{\\rtf", "application/rtf"),
    (b"PK\x03\x04", "application/zip"),
    (b"\xd0\xcf\x11\xe0", "application/x-ole-storage"),
    (b"DICM", "application/dicom"),
]


@dataclass
class FileEntry:
    relpath: str
    size: int
    sha256: str
    modified: str
    kind: str
    mime: str


def sha256_file(path: str, bufsize: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sniff_mime(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            head = fh.read(132)
    except OSError:
        return "unreadable"
    for sig, mime in MAGIC:
        if head.startswith(sig):
            return mime
    if len(head) >= 132 and head[128:132] == b"DICM":
        return "application/dicom"
    if head and all(32 <= b < 127 or b in (9, 10, 13) for b in head[:64]):
        return "text/plain"
    return "application/octet-stream"


def classify(path: str) -> (str, str):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".dbf":
        return ("table" if is_dbf(path) else "unreadable-table"), "application/x-dbf"
    if ext == ".dbc":
        return "database-container", "application/x-dbf"
    if ext in MEMO_EXT:
        return "memo", "application/x-memo"
    if ext in INDEX_EXT:
        return "index", "application/x-index"
    if ext in ARCHIVE_EXT:
        return "archive", sniff_mime(path)
    if ext in PROGRAM_EXT:
        return "program-or-config", "application/octet-stream"
    mime = sniff_mime(path)
    if mime.startswith(("image/", "audio/")) or mime in ("application/pdf", "application/rtf",
                                                         "application/dicom", "application/x-ole-storage"):
        return "document", mime
    if ext in (".txt", ".doc", ".docx", ".htm", ".html", ".xml", ".csv", ".jpg", ".jpeg",
               ".png", ".tif", ".tiff", ".pdf", ".wav", ".mp3", ".bmp"):
        return "document", mime
    return "other", mime


def build_inventory(root: str) -> List[FileEntry]:
    entries: List[FileEntry] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            st = os.stat(full)
            kind, mime = classify(full)
            entries.append(FileEntry(rel, st.st_size, sha256_file(full),
                                     datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                                     kind, mime))
    return entries


def write_manifest(entries: List[FileEntry], path: str):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(asdict(entries[0]).keys()) if entries else ["relpath"])
        w.writeheader()
        for e in entries:
            w.writerow(asdict(e))


def snapshot(root: str, dest: str, entries: List[FileEntry]) -> Dict[str, int]:
    """Copy the source tree to `dest` and verify every file by SHA-256."""
    copied = mismatched = 0
    for e in entries:
        src = os.path.join(root, e.relpath)
        dst = os.path.join(dest, e.relpath)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
        if sha256_file(dst) != e.sha256:
            mismatched += 1
    return {"copied": copied, "hash_mismatches": mismatched}


def summarise(entries: List[FileEntry]) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for e in entries:
        d = out.setdefault(e.kind, {"files": 0, "bytes": 0})
        d["files"] += 1
        d["bytes"] += e.size
    return out
