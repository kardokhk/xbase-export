"""Stream records out of an xBase table, including records flagged as deleted.

Every value is decoded into a Python object (str, int, Decimal, float, date,
datetime, bool, bytes or None). Problems are counted rather than raised, so
that one damaged record never stops an export; the counts feed the validation
report.
"""
from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterator, List, Optional

from .header import TableHeader, FieldDef, read_header
from .memo import MemoFile, MemoError, find_memo_file

JULIAN_OFFSET = 1721425  # JDN = date.toordinal() + JULIAN_OFFSET


@dataclass
class Record:
    row: int            # 1-based physical record number
    deleted: bool
    values: Dict[str, Any]


@dataclass
class ReadStats:
    records_read: int = 0
    deleted: int = 0
    truncated_tail_bytes: int = 0
    missing_records: int = 0     # header count minus records physically present
    decode_errors: Counter = field(default_factory=Counter)   # field -> count
    bad_values: Counter = field(default_factory=Counter)      # field -> count
    memo_errors: Counter = field(default_factory=Counter)     # field -> count
    memo_bytes: int = 0
    memo_file: Optional[str] = None
    memo_missing: bool = False
    issues: List[tuple] = field(default_factory=list)  # (row, field, raw text), capped


class TableReader:
    def __init__(self, path: str, codepage: Optional[str] = None):
        self.header: TableHeader = read_header(path)
        self.path = path
        self.codepage = codepage or self.header.codepage
        self.stats = ReadStats()
        self.memo_path = find_memo_file(path) if self.header.has_memo_fields else None
        self.stats.memo_file = self.memo_path
        self.stats.memo_missing = self.header.has_memo_fields and self.memo_path is None
        self._nullable_bits = self._null_bit_map()
        self._last_bad = False

    # ----- null flag handling (Visual FoxPro) -----
    def _null_bit_map(self) -> Dict[str, int]:
        """Bit index in _NullFlags for each nullable field.

        VFP assigns one bit to every nullable field and an extra bit to every
        V/Q field (marking a value shorter than the field width), in field order.
        """
        bits: Dict[str, int] = {}
        varlen: Dict[str, int] = {}
        i = 0
        for f in self.header.fields:
            if f.is_system:
                continue
            if f.type in "VQ":
                varlen[f.name] = i
                i += 1
            if f.nullable:
                bits[f.name] = i
                i += 1
        self._varlen_bits = varlen
        return bits

    def _null_flags(self, rec: bytes) -> Optional[int]:
        for f in self.header.fields:
            if f.type == "0":
                return int.from_bytes(rec[f.offset:f.offset + f.length], "little")
        return None

    # ----- iteration -----
    def __iter__(self) -> Iterator[Record]:
        return self.records()

    def records(self) -> Iterator[Record]:
        h = self.header
        present = h.records_physically_present
        self.stats.missing_records = max(h.record_count - present, 0)
        memo = MemoFile(self.memo_path) if self.memo_path else None
        try:
            with open(self.path, "rb") as fh:
                fh.seek(h.header_length)
                for row in range(1, present + 1):
                    rec = fh.read(h.record_length)
                    if len(rec) < h.record_length:
                        self.stats.truncated_tail_bytes = len(rec)
                        self.stats.missing_records += 1
                        break
                    flag = rec[0:1]
                    deleted = flag == b"*"
                    nulls = self._null_flags(rec)
                    values: Dict[str, Any] = {}
                    for f in h.fields:
                        if f.is_system:
                            continue
                        raw = rec[f.offset:f.offset + f.length]
                        if nulls is not None and f.name in self._nullable_bits \
                                and nulls >> self._nullable_bits[f.name] & 1:
                            values[f.name] = None
                            continue
                        if nulls is not None and f.name in self._varlen_bits \
                                and nulls >> self._varlen_bits[f.name] & 1:
                            raw = raw[:raw[-1]] if raw else raw
                        values[f.name] = self._decode(f, raw, memo)
                        if values[f.name] is None and self._last_bad and len(self.stats.issues) < 50_000:
                            self.stats.issues.append((row, f.name, raw.decode("latin-1").strip()))
                        self._last_bad = False
                    self.stats.records_read += 1
                    if deleted:
                        self.stats.deleted += 1
                    yield Record(row, deleted, values)
        finally:
            if memo:
                memo.close()

    # ----- value decoding -----
    def _text(self, f: FieldDef, raw: bytes) -> str:
        try:
            return raw.decode(self.codepage)
        except UnicodeDecodeError:
            self.stats.decode_errors[f.name] += 1
            return raw.decode(self.codepage, errors="replace")

    def _decode(self, f: FieldDef, raw: bytes, memo: Optional[MemoFile]) -> Any:
        t = f.type
        try:
            if t in "CV":
                if f.flags & 0x04:  # binary character field
                    return raw.rstrip(b"\x00 ")
                return self._text(f, raw).rstrip(" \x00")
            if t in "NF":
                s = raw.strip(b" \x00*")
                if not s:
                    return None
                txt = s.decode("ascii")
                if f.decimals == 0 and "." not in txt:
                    return int(txt)
                return Decimal(txt)
            if t == "D":
                s = raw.strip(b" \x00")
                if not s or s == b"00000000":
                    return None
                return datetime.strptime(s.decode("ascii"), "%Y%m%d").date()
            if t == "L":
                c = raw[:1].upper()
                if c in b"TY":
                    return True
                if c in b"FN":
                    return False
                return None
            if t == "I" or t == "+":
                return struct.unpack("<i", raw)[0]
            if t == "Y":
                return Decimal(struct.unpack("<q", raw)[0]) / Decimal(10000)
            if t == "B" and self.header.is_vfp:
                return struct.unpack("<d", raw)[0]
            if t == "O":
                return struct.unpack("<d", raw)[0]
            if t in "T@":
                if raw.strip(b" \x00") == b"":
                    return None
                day, ms = struct.unpack("<ii", raw)
                if day == 0:
                    return None
                base = datetime.fromordinal(day - JULIAN_OFFSET)
                return base + timedelta(milliseconds=ms)
            if t == "Q":
                return raw
            if t in self.header.memo_types:
                return self._memo(f, raw, memo)
        except (ValueError, struct.error, InvalidOperation, OverflowError):
            self.stats.bad_values[f.name] += 1
            self._last_bad = True
            return None
        # Unknown type: keep the bytes so nothing is silently lost.
        return raw

    def _memo(self, f: FieldDef, raw: bytes, memo: Optional[MemoFile]) -> Any:
        if len(raw) == 4:
            block = struct.unpack("<I", raw)[0]
        else:
            s = raw.strip(b" \x00")
            block = int(s) if s else 0
        if block == 0:
            return None
        if memo is None:
            self.stats.memo_errors[f.name] += 1
            return None
        try:
            data = memo.read(block)
        except MemoError:
            self.stats.memo_errors[f.name] += 1
            return None
        if data is None:
            return None
        self.stats.memo_bytes += len(data)
        if f.type == "M" and not (f.flags & 0x04):
            return self._text(f, data)
        return bytes(data)  # G, P, W, binary memo: keep as bytes


def read_table(path: str, codepage: Optional[str] = None) -> List[Record]:
    return list(TableReader(path, codepage))
