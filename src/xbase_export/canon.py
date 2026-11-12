"""Canonical text form of a value, and order-independent column fingerprints.

The same logical value must canonicalise identically whether it came from
the source table, a CSV file, SQLite or another reader. Fingerprints are sums
of 64-bit hashes modulo 2**64, so they do not depend on row order and can be
compared between systems that return rows in different orders.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, Optional

MASK = (1 << 64) - 1


def canon_number(v: Any) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        d = Decimal(repr(v))
    elif isinstance(v, Decimal):
        d = v
    else:
        try:
            d = Decimal(str(v).strip())
        except InvalidOperation:
            return str(v)
    if d == 0:
        return "0"
    d = d.normalize()
    return format(d, "f")


def canon(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float, Decimal)):
        return canon_number(v)
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%dT%H:%M:%S") + (f".{v.microsecond // 1000:03d}" if v.microsecond else "")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "sha256:" + hashlib.sha256(bytes(v)).hexdigest()
    return str(v)


def canon_from_text(text: Optional[str], ftype: str, decimals: int = 0) -> str:
    """Canonicalise a value read back as text (CSV) given the source field type."""
    if text is None or text == "":
        return ""
    if ftype in "NFIY+BO":
        return canon_number(text)
    if ftype == "L":
        t = text.strip().lower()
        return "1" if t in ("true", "t", "1", "y", "yes") else "0" if t in ("false", "f", "0", "n", "no") else text
    if ftype in "T@":
        # Accept both 'YYYY-MM-DD HH:MM:SS' and ISO 'T' separators.
        t = text.replace(" ", "T")
        if "." in t:
            head, frac = t.split(".", 1)
            ms = int(frac[:3].ljust(3, "0"))
            return head + (f".{ms:03d}" if ms else "")
        return t
    return text


def h64(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "little")


class Fingerprint:
    """Accumulates per-column fingerprints and non-empty counts."""

    def __init__(self, columns: Iterable[str]):
        self.columns = list(columns)
        self.sums: Dict[str, int] = {c: 0 for c in self.columns}
        self.nonempty: Dict[str, int] = {c: 0 for c in self.columns}
        self.rows = 0

    def add_canonical(self, row: Dict[str, str]):
        self.rows += 1
        for c in self.columns:
            s = row.get(c, "")
            if s != "":
                self.nonempty[c] += 1
            self.sums[c] = (self.sums[c] + h64(c + "\x1f" + s)) & MASK

    def as_dict(self) -> Dict[str, Any]:
        return {"rows": self.rows,
                "columns": {c: {"nonempty": self.nonempty[c], "hash": f"{self.sums[c]:016x}"}
                            for c in self.columns}}

    def diff(self, other: "Fingerprint") -> Dict[str, str]:
        """Columns that disagree, with a short reason."""
        out: Dict[str, str] = {}
        if self.rows != other.rows:
            out["__rows__"] = f"{self.rows} vs {other.rows}"
        for c in self.columns:
            if c not in other.sums:
                out[c] = "missing column"
            elif self.sums[c] != other.sums[c]:
                out[c] = (f"value mismatch (non-empty {self.nonempty[c]} vs {other.nonempty[c]})")
        return out
