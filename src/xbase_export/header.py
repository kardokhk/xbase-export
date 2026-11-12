"""Parse xBase (dBase / FoxPro / Visual FoxPro) table headers directly from bytes.

The parser reads only the header and field descriptors. It never writes to the
source file and never depends on the vendor application being installed.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

from .codepages import codepage_for_ldid

# Version byte -> human-readable format name.
VERSIONS = {
    0x02: "FoxBASE",
    0x03: "dBase III / FoxPro (no memo)",
    0x04: "dBase IV (no memo)",
    0x05: "dBase V (no memo)",
    0x30: "Visual FoxPro",
    0x31: "Visual FoxPro (autoincrement)",
    0x32: "Visual FoxPro (varchar/varbinary)",
    0x43: "dBase IV SQL table",
    0x63: "dBase IV SQL system",
    0x83: "dBase III (with memo)",
    0x8B: "dBase IV (with memo)",
    0x8E: "dBase IV (with SQL table)",
    0xCB: "dBase IV SQL table (with memo)",
    0xF5: "FoxPro 2.x (with memo)",
    0xFB: "FoxBASE (with memo)",
}

VFP_VERSIONS = {0x30, 0x31, 0x32}

# VFP field flags
FLAG_SYSTEM = 0x01
FLAG_NULLABLE = 0x02
FLAG_BINARY = 0x04
FLAG_AUTOINC = 0x08

# Field type -> descriptive name
FIELD_TYPES = {
    "C": "character",
    "V": "varchar",
    "N": "numeric",
    "F": "float",
    "D": "date",
    "T": "datetime",
    "L": "logical",
    "M": "memo",
    "G": "general (OLE)",
    "P": "picture",
    "B": "double / binary memo",
    "I": "integer",
    "Y": "currency",
    "Q": "varbinary",
    "W": "blob",
    "0": "null flags (system)",
    "+": "autoincrement",
    "@": "timestamp",
    "O": "double (dBase 7)",
}


class HeaderError(ValueError):
    """Raised when a file is not a readable xBase table."""


@dataclass
class FieldDef:
    name: str
    type: str
    offset: int  # offset inside the record, after the 1-byte deletion flag
    length: int
    decimals: int
    flags: int = 0

    @property
    def nullable(self) -> bool:
        return bool(self.flags & FLAG_NULLABLE)

    @property
    def is_system(self) -> bool:
        return bool(self.flags & FLAG_SYSTEM) or self.type == "0"

    @property
    def type_name(self) -> str:
        return FIELD_TYPES.get(self.type, f"unknown ({self.type!r})")


@dataclass
class TableHeader:
    path: str
    version: int
    last_update: Optional[date]
    record_count: int
    header_length: int
    record_length: int
    table_flags: int
    ldid: int
    codepage: str
    fields: List[FieldDef] = field(default_factory=list)
    file_size: int = 0

    @property
    def version_name(self) -> str:
        return VERSIONS.get(self.version, f"unknown (0x{self.version:02X})")

    @property
    def is_vfp(self) -> bool:
        return self.version in VFP_VERSIONS

    @property
    def memo_types(self) -> set:
        """Field types whose record bytes are a pointer into the memo file.

        In Visual FoxPro, B is an 8-byte double held in the record; in dBase
        IV/V it is a binary memo.
        """
        return set("MGPW") | (set() if self.is_vfp else {"B"})

    @property
    def has_memo_fields(self) -> bool:
        return any(f.type in self.memo_types for f in self.fields)

    @property
    def data_fields(self) -> List[FieldDef]:
        return [f for f in self.fields if not f.is_system]

    @property
    def records_physically_present(self) -> int:
        """Records that fit in the file, which detects truncated tables."""
        if self.record_length <= 0:
            return 0
        body = self.file_size - self.header_length
        if body <= 0:
            return 0
        n = body // self.record_length
        return min(n, self.record_count) if self.record_count else n

    @property
    def expected_size(self) -> int:
        return self.header_length + self.record_count * self.record_length


def _parse_date(yy: int, mm: int, dd: int) -> Optional[date]:
    # Year is stored as an offset from 1900. Many writers store years >= 2000
    # as 100+, a few as 0-99 meaning 2000-2099; both are handled.
    year = 1900 + yy
    if year < 1980:
        year += 100
    try:
        return date(year, mm, dd)
    except ValueError:
        return None


def read_header(path: str) -> TableHeader:
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(32)
        if len(head) < 32:
            raise HeaderError(f"{path}: file shorter than a DBF header")
        version = head[0]
        if version not in VERSIONS:
            raise HeaderError(f"{path}: unrecognised version byte 0x{version:02X}")
        yy, mm, dd = head[1], head[2], head[3]
        record_count, header_length, record_length = struct.unpack("<IHH", head[4:12])
        table_flags = head[28]
        ldid = head[29]
        if header_length < 33 or header_length > size:
            raise HeaderError(f"{path}: implausible header length {header_length}")

        descriptors = fh.read(header_length - 32)

    fields: List[FieldDef] = []
    running_offset = 1  # the deletion flag occupies byte 0 of each record
    pos = 0
    while pos + 32 <= len(descriptors) and descriptors[pos] != 0x0D:
        d = descriptors[pos:pos + 32]
        raw_name = d[0:11].split(b"\x00", 1)[0]
        name = raw_name.decode("ascii", errors="replace").strip().upper()
        ftype = chr(d[11])
        displacement = struct.unpack("<I", d[12:16])[0]
        length = d[16]
        decimals = d[17]
        flags = d[18] if version in VFP_VERSIONS else 0
        if ftype == "C" and version not in VFP_VERSIONS and decimals:
            # Clipper / FoxPro extension: long character fields use the
            # decimals byte as the high byte of the length.
            length = length + decimals * 256
            decimals = 0
        offset = displacement if (version in VFP_VERSIONS and displacement) else running_offset
        fields.append(FieldDef(name, ftype, offset, length, decimals, flags))
        running_offset += length
        pos += 32

    if not fields:
        raise HeaderError(f"{path}: no field descriptors found")

    computed = 1 + sum(f.length for f in fields)
    if computed != record_length:
        raise HeaderError(
            f"{path}: field lengths sum to {computed} but header says record length {record_length}")

    return TableHeader(
        path=path,
        version=version,
        last_update=_parse_date(yy, mm, dd),
        record_count=record_count,
        header_length=header_length,
        record_length=record_length,
        table_flags=table_flags,
        ldid=ldid,
        codepage=codepage_for_ldid(ldid),
        fields=fields,
        file_size=size,
    )


def is_dbf(path: str) -> bool:
    try:
        read_header(path)
        return True
    except (HeaderError, OSError, struct.error):
        return False
