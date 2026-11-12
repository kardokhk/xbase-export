"""Minimal Visual FoxPro table writer.

Used to build test fixtures and synthetic demonstration databases. It writes
the on-disk format that FoxPro-based practice-management systems use: a .DBF
table (version 0x30) and, when the table has memo fields, a .FPT memo file.
It is not used on client data: extraction is read-only.
"""
from __future__ import annotations

import struct
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from .codepages import ldid_for_codepage
from .reader import JULIAN_OFFSET

FieldSpec = Tuple[str, str, int, int]  # name, type, length, decimals

FIXED_LENGTHS = {"D": 8, "L": 1, "M": 4, "G": 4, "I": 4, "Y": 8, "T": 8, "B": 8}
MEMO_BLOCK = 64


class FptWriter:
    def __init__(self, path: str, block_size: int = MEMO_BLOCK):
        self.path = path
        self.block_size = block_size
        self.next_block = 512 // block_size
        self.chunks: List[bytes] = []

    def add(self, data: bytes, memo_type: int = 1) -> int:
        block = self.next_block
        payload = struct.pack(">II", memo_type, len(data)) + data
        pad = (-len(payload)) % self.block_size
        self.chunks.append(payload + b"\x00" * pad)
        self.next_block += (len(payload) + pad) // self.block_size
        return block

    def write(self):
        head = struct.pack(">I", self.next_block) + b"\x00\x00" + struct.pack(">H", self.block_size)
        head += b"\x00" * (512 - len(head))
        with open(self.path, "wb") as fh:
            fh.write(head)
            for c in self.chunks:
                fh.write(c)


def _encode(ftype: str, length: int, decimals: int, value: Any, codepage: str,
            memo: Optional[FptWriter], flags: int = 0) -> bytes:
    if ftype == "C":
        if value is None:
            value = ""
        b = str(value).encode(codepage)[:length]
        return b + b" " * (length - len(b))
    if ftype == "N":
        if value is None or value == "":
            return b" " * length
        s = f"{Decimal(value):.{decimals}f}" if decimals else str(int(value))
        if len(s) > length:
            return b"*" * length  # FoxPro writes asterisks on overflow
        return s.rjust(length).encode("ascii")
    if ftype == "D":
        if value is None:
            return b" " * 8
        if isinstance(value, str):  # allow raw (possibly invalid) strings for fixtures
            return value.encode("ascii")[:8].ljust(8)
        return value.strftime("%Y%m%d").encode("ascii")
    if ftype == "L":
        return b"?" if value is None else (b"T" if value else b"F")
    if ftype == "I":
        return struct.pack("<i", int(value or 0))
    if ftype == "Y":
        return struct.pack("<q", int((Decimal(value or 0) * 10000).to_integral_value()))
    if ftype == "B":
        return struct.pack("<d", float(value or 0))
    if ftype == "T":
        if value is None:
            return b"\x00" * 8
        if isinstance(value, date) and not isinstance(value, datetime):
            value = datetime(value.year, value.month, value.day)
        day = value.toordinal() + JULIAN_OFFSET
        ms = ((value.hour * 60 + value.minute) * 60 + value.second) * 1000 + value.microsecond // 1000
        return struct.pack("<ii", day, ms)
    if ftype in "MG":
        if value is None or value == "" or value == b"":
            return struct.pack("<I", 0)
        assert memo is not None
        if isinstance(value, bytes):
            data, mtype = value, (2 if ftype == "G" else 0)
        else:
            data, mtype = str(value).encode(codepage), 1
        return struct.pack("<I", memo.add(data, mtype))
    raise ValueError(f"writer does not support type {ftype}")


def write_vfp_table(path: str, fields: Sequence[FieldSpec], rows: Iterable[Sequence[Any]],
                    codepage: str = "cp1252", deleted: Optional[Iterable[int]] = None,
                    last_update: Optional[date] = None, has_cdx: bool = False) -> int:
    """Write rows to a Visual FoxPro table. Returns the number of records.

    `deleted` holds 0-based row indices to flag with '*', which is how xBase
    marks a record deleted while leaving its contents in the file.
    """
    specs = []
    for name, ftype, length, dec in fields:
        length = FIXED_LENGTHS.get(ftype, length)
        specs.append((name.upper(), ftype, length, dec))
    has_memo = any(t in "MG" for _, t, _, _ in specs)
    memo = FptWriter(path[:-4] + ".FPT" if path.upper().endswith(".DBF") else path + ".fpt") \
        if has_memo else None
    deleted = set(deleted or ())

    body = bytearray()
    count = 0
    for i, row in enumerate(rows):
        body += b"*" if i in deleted else b" "
        for (name, ftype, length, dec), value in zip(specs, row):
            enc = _encode(ftype, length, dec, value, codepage, memo)
            assert len(enc) == length, (name, len(enc), length)
            body += enc
        count += 1

    record_length = 1 + sum(s[2] for s in specs)
    header_length = 32 + 32 * len(specs) + 1 + 263
    d = last_update or date.today()
    flags = (0x01 if has_cdx else 0) | (0x02 if has_memo else 0)
    head = struct.pack("<BBBBIHH", 0x30, d.year - 1900, d.month, d.day, count,
                       header_length, record_length)
    head += b"\x00" * 16 + bytes([flags, ldid_for_codepage(codepage)]) + b"\x00\x00"
    desc = bytearray()
    offset = 1
    for name, ftype, length, dec in specs:
        nb = name.encode("ascii")[:10]
        desc += nb + b"\x00" * (11 - len(nb))
        desc += ftype.encode("ascii") + struct.pack("<I", offset)
        desc += bytes([length, dec, 0]) + b"\x00" * 13
        offset += length
    with open(path, "wb") as fh:
        fh.write(head)
        fh.write(desc)
        fh.write(b"\x0d" + b"\x00" * 263)
        fh.write(body)
        fh.write(b"\x1a")
    if memo:
        memo.write()
    return count
