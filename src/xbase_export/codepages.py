"""Map the xBase language driver ID (header byte 29) to a Python codec."""

LDID_TO_CODEPAGE = {
    0x01: "cp437", 0x02: "cp850", 0x03: "cp1252", 0x04: "mac_roman",
    0x08: "cp865", 0x09: "cp437", 0x0A: "cp850", 0x0B: "cp437",
    0x0D: "cp437", 0x0E: "cp850", 0x0F: "cp437", 0x10: "cp850",
    0x11: "cp437", 0x12: "cp850", 0x13: "cp932", 0x14: "cp850",
    0x15: "cp437", 0x16: "cp850", 0x17: "cp865", 0x18: "cp437",
    0x19: "cp437", 0x1A: "cp850", 0x1B: "cp437", 0x1C: "cp863",
    0x1D: "cp850", 0x1F: "cp852", 0x22: "cp852", 0x23: "cp852",
    0x24: "cp860", 0x25: "cp850", 0x26: "cp866", 0x37: "cp850",
    0x40: "cp852", 0x4D: "cp936", 0x4E: "cp949", 0x4F: "cp950",
    0x50: "cp874", 0x57: "cp1252", 0x58: "cp1252", 0x59: "cp1252",
    0x64: "cp852", 0x65: "cp866", 0x66: "cp865", 0x67: "cp861",
    0x6A: "cp737", 0x6B: "cp857", 0x6C: "cp863", 0x78: "cp950",
    0x79: "cp949", 0x7A: "cp936", 0x7B: "cp932", 0x7C: "cp874",
    0x7D: "cp1255", 0x7E: "cp1256", 0x96: "mac_cyrillic",
    0x97: "mac_latin2", 0x98: "mac_greek", 0xC8: "cp1250",
    0xC9: "cp1251", 0xCA: "cp1254", 0xCB: "cp1253", 0xCC: "cp1257",
}

# Windows practice-management systems in North America almost always write
# Western European ANSI text when the header leaves the byte at zero.
DEFAULT_CODEPAGE = "cp1252"


def codepage_for_ldid(ldid: int) -> str:
    return LDID_TO_CODEPAGE.get(ldid, DEFAULT_CODEPAGE)


def ldid_for_codepage(codepage: str) -> int:
    for k, v in LDID_TO_CODEPAGE.items():
        if v == codepage:
            return k
    raise KeyError(codepage)
