"""Read memo files (.FPT for FoxPro / Visual FoxPro, .DBT for dBase)."""
from __future__ import annotations

import os
import struct
from typing import Optional

MEMO_EXTENSIONS = (".fpt", ".dbt")


def find_memo_file(dbf_path: str) -> Optional[str]:
    """Return the memo file that sits beside a table, matching case-insensitively."""
    folder = os.path.dirname(dbf_path) or "."
    stem = os.path.splitext(os.path.basename(dbf_path))[0].lower()
    try:
        entries = os.listdir(folder)
    except OSError:
        return None
    for ext in MEMO_EXTENSIONS:
        for entry in entries:
            if entry.lower() == stem + ext:
                return os.path.join(folder, entry)
    return None


class MemoError(ValueError):
    pass


class MemoFile:
    """Random-access reader. Returns raw bytes; decoding is the caller's job."""

    def __init__(self, path: str):
        self.path = path
        self.kind = os.path.splitext(path)[1].lower()
        self._fh = open(path, "rb")
        self.size = os.path.getsize(path)
        head = self._fh.read(512)
        if self.kind == ".fpt":
            self.block_size = struct.unpack(">H", head[6:8])[0] or 64
            self.dbase4 = False
        else:
            # dBase IV writes the block size at bytes 20-21; dBase III always uses 512.
            bs = struct.unpack("<H", head[20:22])[0] if len(head) >= 22 else 0
            self.dbase4 = bs not in (0, 512) or self._looks_dbase4()
            self.block_size = bs if (self.dbase4 and bs) else 512

    def _looks_dbase4(self) -> bool:
        self._fh.seek(512)
        return self._fh.read(4) == b"\xff\xff\x08\x00"

    def close(self):
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def read(self, block: int) -> Optional[bytes]:
        """Return the memo stored at `block`, or None when the pointer is empty.

        Raises MemoError for a pointer that lands outside the file, which is
        how a damaged memo file usually shows itself.
        """
        if block <= 0:
            return None
        start = block * self.block_size
        if start >= self.size:
            raise MemoError(f"block {block} beyond end of {self.path}")
        self._fh.seek(start)
        if self.kind == ".fpt":
            head = self._fh.read(8)
            if len(head) < 8:
                raise MemoError(f"truncated memo header at block {block}")
            _mtype, length = struct.unpack(">II", head)
            if start + 8 + length > self.size:
                raise MemoError(f"memo at block {block} runs past end of file")
            return self._fh.read(length)
        if self.dbase4:
            head = self._fh.read(8)
            if head[:4] != b"\xff\xff\x08\x00":
                raise MemoError(f"bad dBase IV memo signature at block {block}")
            length = struct.unpack("<I", head[4:8])[0] - 8
            return self._fh.read(max(length, 0))
        # dBase III: read until the 0x1A terminator.
        chunks = []
        while True:
            chunk = self._fh.read(self.block_size)
            if not chunk:
                break
            cut = chunk.find(b"\x1a")
            if cut >= 0:
                chunks.append(chunk[:cut])
                break
            chunks.append(chunk)
        return b"".join(chunks)
