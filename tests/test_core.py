import os
import struct
from datetime import date, datetime
from decimal import Decimal

import pytest

from xbase_export.canon import canon, canon_from_text, Fingerprint
from xbase_export.header import read_header, HeaderError
from xbase_export.reader import TableReader
from xbase_export.writer import write_vfp_table

FIELDS = [("PATID", "C", 6, 0), ("NAME", "C", 30, 0), ("DOB", "D", 8, 0), ("FEE", "N", 8, 2),
          ("BAL", "Y", 8, 0), ("ACTIVE", "L", 1, 0), ("NOTE", "M", 4, 0), ("SEEN", "T", 8, 0),
          ("VISITS", "I", 4, 0)]
ROWS = [
    ["000001", "José Zoë O'Brien", date(1980, 2, 29), Decimal("45.50"), Decimal("12.3456"), True,
     "Allergic to latex.\r\nUse vinyl gloves.", datetime(2019, 5, 1, 13, 45, 7), 12],
    ["000002", "Siobhán Ní Bhriain", None, None, Decimal("0"), False, None, None, 0],
    ["000003", "Deleted Person", date(1975, 1, 1), Decimal("10"), Decimal("-5.25"), None, "old", None, -3],
]


@pytest.fixture
def table(tmp_path):
    p = str(tmp_path / "PAT.DBF")
    write_vfp_table(p, FIELDS, ROWS, deleted=[2], last_update=date(2026, 10, 31))
    return p


def test_header_known_answer(table):
    h = read_header(table)
    assert h.version == 0x30 and h.is_vfp
    assert h.codepage == "cp1252"
    assert h.record_count == 3
    assert h.last_update == date(2026, 10, 31)
    assert [f.name for f in h.fields] == [f[0] for f in FIELDS]
    assert h.record_length == 1 + 6 + 30 + 8 + 8 + 8 + 1 + 4 + 8 + 4
    assert h.records_physically_present == 3
    assert h.has_memo_fields


def test_values_and_deleted_flag(table):
    recs = list(TableReader(table))
    assert [r.deleted for r in recs] == [False, False, True]
    a, b, c = (r.values for r in recs)
    assert a["NAME"] == "José Zoë O'Brien"
    assert a["DOB"] == date(1980, 2, 29)
    assert a["FEE"] == Decimal("45.50")
    assert a["BAL"] == Decimal("12.3456")
    assert a["NOTE"] == "Allergic to latex.\r\nUse vinyl gloves."  # CRLF preserved
    assert a["SEEN"] == datetime(2019, 5, 1, 13, 45, 7)
    assert a["VISITS"] == 12 and c["VISITS"] == -3
    assert b["DOB"] is None and b["FEE"] is None and b["NOTE"] is None and b["SEEN"] is None
    assert b["ACTIVE"] is False and c["ACTIVE"] is None
    assert c["NOTE"] == "old"  # deleted records keep their memo


def test_agrees_with_dbfread(table):
    import dbfread
    ours = [{k: canon(v) for k, v in r.values.items()} for r in TableReader(table) if not r.deleted]
    t = dbfread.DBF(table, encoding="cp1252")
    theirs = [{k: canon(v) for k, v in rec.items()} for rec in t]
    assert ours == theirs


def test_truncated_table_detected(tmp_path):
    p = str(tmp_path / "R.DBF")
    write_vfp_table(p, [("ID", "C", 4, 0)], [[f"{i:04d}"] for i in range(10)])
    size = os.path.getsize(p)
    with open(p, "r+b") as fh:
        fh.truncate(size - 1 - 5 * 2 - 2)  # lose 2 full records and part of a third
    r = TableReader(p)
    rows = list(r)
    assert len(rows) == 7
    assert r.stats.missing_records == 3
    assert rows[-1].values["ID"] == "0006"


def test_invalid_date_and_overflow_are_counted_not_raised(tmp_path):
    p = str(tmp_path / "V.DBF")
    write_vfp_table(p, [("D", "D", 8, 0), ("N", "N", 3, 0)], [["20190231", 5], [date(2020, 1, 2), 12345]])
    r = TableReader(p)
    rows = [x.values for x in r]
    assert rows[0]["D"] is None and rows[1]["D"] == date(2020, 1, 2)
    assert rows[1]["N"] is None  # '***' overflow written by FoxPro
    assert r.stats.bad_values["D"] == 1
    assert (1, "D", "20190231") in r.stats.issues


def _dbase3_with_memo(path):
    """Hand-built dBase III table with a .DBT memo: an independent known answer."""
    fields = [(b"ID", b"C", 3), (b"TXT", b"M", 10)]
    reclen = 1 + 3 + 10
    hlen = 32 + 32 * len(fields) + 1
    head = struct.pack("<BBBBIHH", 0x83, 99, 12, 31, 2, hlen, reclen) + b"\x00" * 20
    desc = b"".join(n.ljust(11, b"\x00") + t + b"\x00" * 4 + bytes([ln, 0]) + b"\x00" * 14 for n, t, ln in fields)
    recs = b" 01 " + b"1".rjust(10) + b"*02 " + b" " * 10
    with open(path, "wb") as fh:
        fh.write(head + desc + b"\x0d" + recs + b"\x1a")
    memo = bytearray(512)
    memo[0:4] = struct.pack("<I", 2)
    memo += b"Old dBase note\x1a\x1a".ljust(512, b"\x00")
    with open(path[:-4] + ".DBT", "wb") as fh:
        fh.write(memo)


def test_dbase3_memo(tmp_path):
    p = str(tmp_path / "OLD.DBF")
    _dbase3_with_memo(p)
    h = read_header(p)
    assert h.version == 0x83 and not h.is_vfp and h.last_update == date(1999, 12, 31)
    rows = list(TableReader(p))
    assert rows[0].values == {"ID": "01", "TXT": "Old dBase note"}
    assert rows[1].deleted and rows[1].values["TXT"] is None


def test_not_a_table(tmp_path):
    p = tmp_path / "x.dbf"
    p.write_bytes(b"MZ" + b"\x00" * 100)
    with pytest.raises(HeaderError):
        read_header(str(p))


@pytest.mark.parametrize("value,text,ftype", [
    (Decimal("45.50"), "45.50", "N"), (45.5, "45.5", "N"), (Decimal("0.000"), "0", "N"),
    (12, "12", "I"), (True, "true", "L"), (False, "f", "L"),
    (datetime(2019, 5, 1, 13, 45, 7), "2019-05-01 13:45:07", "T"),
    (datetime(2019, 5, 1, 13, 45, 7, 250000), "2019-05-01 13:45:07.250", "T"),
])
def test_canon_round_trip(value, text, ftype):
    assert canon(value) == canon_from_text(text, ftype)


def test_fingerprint_order_independent_and_detects_change():
    rows = [{"A": "1", "B": "x"}, {"A": "2", "B": "y"}]
    f1, f2, f3 = Fingerprint("AB"), Fingerprint("AB"), Fingerprint("AB")
    for r in rows:
        f1.add_canonical(r)
    for r in reversed(rows):
        f2.add_canonical(r)
    f3.add_canonical(rows[0])
    f3.add_canonical({"A": "2", "B": "z"})
    assert not f1.diff(f2)
    assert set(f1.diff(f3)) == {"B"}
