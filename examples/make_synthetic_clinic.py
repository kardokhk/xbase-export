"""Generate a synthetic chiropractic practice database in Visual FoxPro format.

Every person, address, note and amount is fictitious (Faker, fixed seed).
The folder layout follows the documented layout of FoxPro-based practice
systems (a main folder holding DATA, IMAGES, SOUNDS, TEMPLATES and ARTICLES),
but the table and field names are illustrative: a real system's schema is
discovered from its files, not assumed.

Faults that real legacy databases contain are planted on purpose, so the
validation report has something to find:
  * records flagged deleted (merged duplicates, voided charges);
  * appointments that point at patients who no longer exist;
  * invalid stored dates (for example 31 February);
  * a table whose file was truncated after the header was written;
  * document references to files that are missing, and files no table references;
  * Windows-1252 accented names, CRLF line breaks in memo notes, OLE photo fields.

Usage: python make_synthetic_clinic.py OUTPUT_DIR [--patients 9000] [--seed 20261112]
"""
from __future__ import annotations

import argparse
import io
import os
import random
import struct
import sys
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from xbase_export.writer import write_vfp_table  # noqa: E402

from faker import Faker  # noqa: E402

CPT = [("98940", "CMT spinal 1-2 regions", Decimal("45.00")),
       ("98941", "CMT spinal 3-4 regions", Decimal("55.00")),
       ("98942", "CMT spinal 5 regions", Decimal("65.00")),
       ("97140", "Manual therapy, 15 min", Decimal("40.00")),
       ("97110", "Therapeutic exercise, 15 min", Decimal("38.00")),
       ("97012", "Mechanical traction", Decimal("25.00")),
       ("97014", "Electrical stimulation, unattended", Decimal("22.00")),
       ("99203", "New patient E/M, low", Decimal("120.00")),
       ("99213", "Established patient E/M, low", Decimal("85.00")),
       ("72040", "X-ray cervical spine, 2-3 views", Decimal("75.00")),
       ("72100", "X-ray lumbosacral spine, 2-3 views", Decimal("80.00"))]
ICD = [("M54.2", "Cervicalgia"), ("M54.50", "Low back pain, unspecified"),
       ("M99.01", "Segmental dysfunction, cervical region"),
       ("M99.02", "Segmental dysfunction, thoracic region"),
       ("M99.03", "Segmental dysfunction, lumbar region"), ("M54.6", "Pain in thoracic spine"),
       ("S13.4XXA", "Sprain of ligaments of cervical spine, initial"),
       ("M53.0", "Cervicocranial syndrome"), ("G44.209", "Tension-type headache"),
       ("M54.16", "Radiculopathy, lumbar region"), ("M62.830", "Muscle spasm of back")]
REGIONS = ["C1-C2", "C5-C6", "T4-T5", "T7-T8", "L4-L5", "L5-S1", "SI joint left", "SI joint right"]
SUBJ = ["Pt reports {pain} pain {sev}/10, {trend} since last visit. {extra}",
        "Pt presents with {pain} discomfort after {cause}. Pain {sev}/10. {extra}",
        "Follow-up. {pain} pain now {sev}/10, {trend}. Sleeping {sleep}."]
OBJ = ["Palpation: hypertonicity {region}. ROM reduced in {rom}. Orthopaedic tests: {test}.",
       "Subluxation noted {region}. Tenderness on palpation. {test}."]
PLAN = ["CMT {region}. Home exercises reviewed. Return in {days} days.",
        "Continue care plan, {freq} for {weeks} weeks. Re-exam at visit {n}."]

# Fictitious payers (no real company names).
INSURERS = ["Lakeside Mutual Health", "Prairie State Benefit Plan", "Northgate Health Partners",
            "Cedar Valley Insurance", "Summit Workers' Comp Trust", "Harborview Auto Claims",
            "Medicare (synthetic)", "Self-pay"]


def tiny_jpeg(rng: random.Random) -> bytes:
    from PIL import Image
    img = Image.new("L", (32, 32), color=rng.randint(40, 200))
    for _ in range(40):
        img.putpixel((rng.randrange(32), rng.randrange(32)), rng.randint(0, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return buf.getvalue()


def tiny_pdf(text: str) -> bytes:
    body = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(body) + body + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def tiny_wav(rng: random.Random) -> bytes:
    n = 800
    data = bytes(rng.randrange(256) for _ in range(n))
    return (b"RIFF" + struct.pack("<I", 36 + n) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 8000, 1, 8)
            + b"data" + struct.pack("<I", n) + data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--patients", type=int, default=9000)
    ap.add_argument("--seed", type=int, default=20261112)
    ap.add_argument("--start-year", type=int, default=2006)
    ap.add_argument("--end", default="2026-10-31")
    a = ap.parse_args()

    rng = random.Random(a.seed)
    fake = Faker("en_US")
    Faker.seed(a.seed)
    end = date.fromisoformat(a.end)
    start = date(a.start_year, 1, 1)
    span = (end - start).days

    root = os.path.join(a.out, "ACS")
    data_dir = os.path.join(root, "DATA")
    for sub in ("DATA", "IMAGES", "SOUNDS", "TEMPLATES", "ARTICLES"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)

    def D(name):
        return os.path.join(data_dir, name)

    # ---------------- providers ----------------
    providers = [("DR01", "Hollis", "Marguerite", "DC", "1000000017"),
                 ("DR02", "Okafor", "Daniel", "DC", "1000000025"),
                 ("DR03", "Lindqvist", "Søren", "DC", "1000000033"),
                 ("DR04", "Reyes", "Ana Lucía", "DC", "1000000041"),
                 ("PT01", "Brennan", "Clodagh", "PT", "1000000058")]
    write_vfp_table(D("PROVIDER.DBF"),
                    [("PROVID", "C", 4, 0), ("LASTNAME", "C", 25, 0), ("FIRSTNAME", "C", 20, 0),
                     ("CREDENT", "C", 6, 0), ("NPI", "C", 10, 0), ("ACTIVE", "L", 1, 0)],
                    [list(p) + [True] for p in providers], has_cdx=True)

    # ---------------- insurers ----------------
    write_vfp_table(D("INSCO.DBF"),
                    [("INSCOID", "C", 4, 0), ("NAME", "C", 40, 0), ("PAYERID", "C", 10, 0),
                     ("PHONE", "C", 14, 0), ("ADDR1", "C", 40, 0), ("CITY", "C", 25, 0),
                     ("STATE", "C", 2, 0), ("ZIP", "C", 10, 0)],
                    [[f"I{i + 1:03d}", n, f"SYN{i + 1:05d}", fake.numerify("(###) ###-####"),
                      fake.street_address(), fake.city(), fake.state_abbr(), fake.zipcode()]
                     for i, n in enumerate(INSURERS)], has_cdx=True)

    # ---------------- patients ----------------
    accents = ["José", "Zoë", "Renée", "Björn", "Chloé", "Ñuñez", "François", "Siobhán", "Mátyás", "Inés"]
    patients = []
    photos = {}
    deleted_pat = set()
    for i in range(a.patients):
        pid = f"{i + 1:06d}"
        first = rng.choice(accents) if rng.random() < 0.03 else fake.first_name()
        last = fake.last_name()
        if rng.random() < 0.01:
            last = "O'" + last
        dob = fake.date_of_birth(minimum_age=4, maximum_age=92)
        first_visit = start + timedelta(days=rng.randrange(span))
        sex = rng.choice("FM")
        note = None
        if rng.random() < 0.25:
            note = rng.choice(["Prefers morning appointments.", "Referred by Dr. Patel (PCP).",
                               "Allergic to latex.\r\nUse vinyl gloves.", "Spanish-speaking; daughter interprets.",
                               "Pacemaker: no e-stim.", "MVA 03/2014, claim with Harborview Auto Claims."])
        patients.append(dict(PATID=pid, LASTNAME=last, FIRSTNAME=first, MI=fake.random_uppercase_letter()
                             if rng.random() < .6 else "", DOB=dob, SEX=sex, ADDR1=fake.street_address(),
                             CITY=fake.city(), STATE=fake.state_abbr(), ZIP=fake.zipcode(),
                             PHONE=fake.numerify("(###) ###-####"),
                             EMAIL=(f"{first[:1]}{last}{rng.randint(1, 99)}@example.com".lower()
                                    .replace("'", "") if rng.random() < .7 else ""),
                             PROVID=rng.choice(providers[:4])[0], FIRSTVIS=first_visit, LASTVIS=None,
                             ACTIVE=True, REFSRC=rng.choice(["Web", "Friend", "PCP", "Insurance", "Walk-in", ""]),
                             NOTES=note, CREATED=datetime.combine(first_visit, datetime.min.time())
                             + timedelta(minutes=rng.randrange(480, 1080))))
        if rng.random() < 0.05:
            photos[pid] = tiny_jpeg(rng)
        if rng.random() < 0.012:
            deleted_pat.add(i)  # merged duplicate, flagged deleted

    # Duplicates created by front-desk re-registration (live, for the dedupe review)
    for i in rng.sample(range(len(patients)), 25):
        p = dict(patients[i])
        p["PATID"] = f"{len(patients) + 1:06d}"
        p["PHONE"] = fake.numerify("(###) ###-####")
        patients.append(p)

    # Patients hard-deleted from the table long ago, still referenced by old rows.
    ghost_ids = [f"{a.patients + 500 + k:06d}" for k in range(9)]

    # ---------------- insurance policies ----------------
    patins = []
    for p in patients:
        if rng.random() < 0.82:
            ins = rng.randrange(len(INSURERS) - 1)
            patins.append([p["PATID"], f"I{ins + 1:03d}", fake.bothify("???#########").upper(),
                           fake.bothify("GRP####"), rng.choice(["Self", "Spouse", "Child"]),
                           p["FIRSTVIS"], None, Decimal(rng.choice(["20", "25", "30", "40", "0"])), 1])

    # ---------------- appointments, visits, diagnoses, charges, payments ----------------
    appts, visits, diags, charges, payments, docs, recalls = [], [], [], [], [], [], []
    appt_id = visit_id = charge_id = pay_id = doc_id = 0
    bad_dates = 0
    lastvis = {}
    for idx, p in enumerate(patients):
        pid = p["PATID"]
        n_episodes = rng.choices([1, 2, 3, 4], weights=[60, 25, 10, 5])[0]
        for _ in range(n_episodes):
            ep_start = p["FIRSTVIS"] + timedelta(days=rng.randrange(0, max(1, (end - p["FIRSTVIS"]).days)))
            if ep_start > end:
                continue
            n_visits = max(1, int(rng.expovariate(1 / 9)))
            d = ep_start
            dx = rng.sample(ICD, rng.randint(1, 3))
            prov = p["PROVID"] if rng.random() < .85 else rng.choice(providers)[0]
            for v in range(n_visits):
                if d > end:
                    break
                appt_id += 1
                status = rng.choices(["Kept", "No-show", "Cancelled", "Rescheduled"], weights=[86, 5, 7, 2])[0]
                hh, mm = rng.choice(range(8, 18)), rng.choice([0, 15, 30, 45])
                appts.append([appt_id, pid, prov, d, f"{hh:02d}:{mm:02d}", rng.choice([15, 15, 30, 45, 60]),
                              status, "New" if v == 0 else "Regular", ""])
                if status == "Kept":
                    visit_id += 1
                    vdate = d
                    if rng.random() < 0.0004:
                        vdate = "20190231"  # invalid stored date
                        bad_dates += 1
                    pain = rng.choice(["neck", "low back", "mid back", "shoulder", "hip", "headache-related neck"])
                    region = rng.choice(REGIONS)
                    subj = rng.choice(SUBJ).format(pain=pain, sev=rng.randint(2, 8),
                                                   trend=rng.choice(["improving", "unchanged", "slightly worse"]),
                                                   cause=rng.choice(["lifting", "a fall", "a car accident",
                                                                     "gardening", "long drive"]),
                                                   extra=rng.choice(["", "Numbness L hand intermittently.",
                                                                     "Pain worse in the morning."]),
                                                   sleep=rng.choice(["well", "poorly"]))
                    obj = rng.choice(OBJ).format(region=region,
                                                 rom=rng.choice(["flexion", "rotation L", "lateral flexion R"]),
                                                 test=rng.choice(["Kemp's +R", "SLR negative", "Spurling's -",
                                                                  "Yeoman's +L"]))
                    assess = "; ".join(f"{c} {t}" for c, t in dx)
                    plan = rng.choice(PLAN).format(region=region, days=rng.choice([2, 3, 7]),
                                                   freq=rng.choice(["2x/week", "3x/week", "1x/week"]),
                                                   weeks=rng.choice([2, 4, 6]), n=v + 6)
                    visits.append([visit_id, appt_id, pid, prov, vdate, subj, obj, assess, plan,
                                   rng.choice(["Signed", "Signed", "Signed", "Draft"])])
                    for c, _t in dx:
                        diags.append([visit_id, pid, c, dx.index((c, _t)) + 1])
                    codes = [CPT[0 if rng.random() < .5 else rng.choice([1, 2])]]
                    if v == 0:
                        codes.append(CPT[7])
                        if rng.random() < .35:
                            codes.append(CPT[9] if "neck" in pain else CPT[10])
                    elif rng.random() < .3:
                        codes.append(CPT[8] if rng.random() < .2 else rng.choice(CPT[3:7]))
                    total = Decimal(0)
                    for code, desc, fee in codes:
                        charge_id += 1
                        units = 2 if code in ("97140", "97110") and rng.random() < .4 else 1
                        amt = fee * units
                        total += amt
                        charges.append([charge_id, visit_id, pid, vdate, code, desc[:30], units, amt,
                                        rng.choice(["", "", "25", "GP"]) if code.startswith("97") else "",
                                        prov, "Posted"])
                    if rng.random() < .93:
                        pay_id += 1
                        paydate = (d + timedelta(days=rng.randint(0, 45))) if isinstance(vdate, date) else d
                        payments.append([pay_id, pid, paydate, (total * Decimal(rng.choice(["1", "0.8", "0.6", "0.2"])))
                                         .quantize(Decimal("0.01")),
                                         rng.choice(["Insurance", "Card", "Cash", "Check"]),
                                         rng.choice([f"I{k + 1:03d}" for k in range(7)] + [""]),
                                         fake.bothify("REF#######")])
                    lastvis[pid] = d
                d = d + timedelta(days=rng.choice([2, 3, 3, 4, 7, 7, 14]))
            # documents for this episode
            if rng.random() < 0.30:
                for k in range(rng.randint(1, 3)):
                    doc_id += 1
                    kind = rng.choice(["XRAY", "XRAY", "INTAKE", "MRI-RPT", "REFERRAL"])
                    ext = ".JPG" if kind == "XRAY" else ".PDF"
                    rel = f"IMAGES\\{pid}\\{kind}_{ep_start:%Y%m%d}_{k + 1}{ext}"
                    drive = rng.choice(["C:\\ACS\\", "F:\\ACS\\", "\\\\FRONTDESK\\ACS\\", ""])
                    docs.append([doc_id, pid, ep_start, kind, drive + rel, ""])

    for p in patients:
        lv = lastvis.get(p["PATID"])
        p["LASTVIS"] = lv
        p["ACTIVE"] = bool(lv and (end - lv).days < 540)

    # Orphan appointments for hard-deleted patients
    for g in ghost_ids:
        for _ in range(rng.randint(2, 6)):
            appt_id += 1
            d = start + timedelta(days=rng.randrange(600))
            appts.append([appt_id, g, "DR01", d, "09:00", 30, "Kept", "Regular", "patient record removed"])

    # Recall reminders (this table will be truncated)
    for p in patients:
        if p["LASTVIS"] and rng.random() < .4:
            recalls.append([p["PATID"], p["LASTVIS"] + timedelta(days=rng.choice([30, 90, 180])),
                            rng.choice(["Re-exam", "Maintenance", "Annual X-ray review"]), rng.random() < .5])

    # ---------------- write tables ----------------
    pcols = [("PATID", "C", 6, 0), ("LASTNAME", "C", 30, 0), ("FIRSTNAME", "C", 25, 0), ("MI", "C", 1, 0),
             ("DOB", "D", 8, 0), ("SEX", "C", 1, 0), ("ADDR1", "C", 40, 0), ("CITY", "C", 25, 0),
             ("STATE", "C", 2, 0), ("ZIP", "C", 10, 0), ("PHONE", "C", 14, 0), ("EMAIL", "C", 50, 0),
             ("PROVID", "C", 4, 0), ("FIRSTVIS", "D", 8, 0), ("LASTVIS", "D", 8, 0), ("ACTIVE", "L", 1, 0),
             ("REFSRC", "C", 10, 0), ("NOTES", "M", 4, 0), ("PHOTO", "G", 4, 0), ("CREATED", "T", 8, 0)]
    prow = []
    for p in patients:
        prow.append([p[c] for c, *_ in pcols[:-2]] + [photos.get(p["PATID"]), p["CREATED"]])
    write_vfp_table(D("PATIENT.DBF"), pcols, prow, deleted=deleted_pat, has_cdx=True)

    write_vfp_table(D("PATINS.DBF"),
                    [("PATID", "C", 6, 0), ("INSCOID", "C", 4, 0), ("MEMBERID", "C", 14, 0), ("GROUPNO", "C", 10, 0),
                     ("RELATION", "C", 8, 0), ("EFFDATE", "D", 8, 0), ("TERMDATE", "D", 8, 0),
                     ("COPAY", "Y", 8, 0), ("PRIORITY", "N", 1, 0)], patins, has_cdx=True)
    write_vfp_table(D("APPT.DBF"),
                    [("APPTID", "I", 4, 0), ("PATID", "C", 6, 0), ("PROVID", "C", 4, 0), ("APPTDATE", "D", 8, 0),
                     ("APPTTIME", "C", 5, 0), ("DURATION", "N", 3, 0), ("STATUS", "C", 12, 0),
                     ("APPTTYPE", "C", 10, 0), ("COMMENT", "C", 40, 0)], appts,
                    deleted=set(rng.sample(range(len(appts)), len(appts) // 150)), has_cdx=True)
    write_vfp_table(D("VISIT.DBF"),
                    [("VISITID", "I", 4, 0), ("APPTID", "I", 4, 0), ("PATID", "C", 6, 0), ("PROVID", "C", 4, 0),
                     ("VISITDATE", "D", 8, 0), ("SUBJECTIVE", "M", 4, 0), ("OBJECTIVE", "M", 4, 0),
                     ("ASSESSMENT", "M", 4, 0), ("PLAN", "M", 4, 0), ("SIGNSTAT", "C", 8, 0)], visits, has_cdx=True)
    write_vfp_table(D("DIAGNOS.DBF"),
                    [("VISITID", "I", 4, 0), ("PATID", "C", 6, 0), ("ICD10", "C", 8, 0), ("SEQ", "N", 1, 0)],
                    diags, has_cdx=True)
    write_vfp_table(D("CHARGE.DBF"),
                    [("CHARGEID", "I", 4, 0), ("VISITID", "I", 4, 0), ("PATID", "C", 6, 0), ("SVCDATE", "D", 8, 0),
                     ("CPT", "C", 5, 0), ("DESCRIPT", "C", 30, 0), ("UNITS", "N", 2, 0), ("AMOUNT", "N", 9, 2),
                     ("MODIFIER", "C", 2, 0), ("PROVID", "C", 4, 0), ("STATUS", "C", 8, 0)], charges,
                    deleted=set(rng.sample(range(len(charges)), len(charges) // 200)), has_cdx=True)
    write_vfp_table(D("PAYMENT.DBF"),
                    [("PAYID", "I", 4, 0), ("PATID", "C", 6, 0), ("PAYDATE", "D", 8, 0), ("AMOUNT", "Y", 8, 0),
                     ("METHOD", "C", 10, 0), ("INSCOID", "C", 4, 0), ("REFNO", "C", 12, 0)], payments, has_cdx=True)
    write_vfp_table(D("DOCUMENT.DBF"),
                    [("DOCID", "I", 4, 0), ("PATID", "C", 6, 0), ("DOCDATE", "D", 8, 0), ("DOCTYPE", "C", 10, 0),
                     ("FILEPATH", "C", 80, 0), ("NOTE", "C", 40, 0)], docs, has_cdx=True)
    write_vfp_table(D("CPTCODE.DBF"), [("CPT", "C", 5, 0), ("DESCRIPT", "C", 40, 0), ("FEE", "Y", 8, 0)],
                    [list(c) for c in CPT])
    write_vfp_table(D("ICDCODE.DBF"), [("ICD10", "C", 8, 0), ("DESCRIPT", "C", 60, 0)], [list(c) for c in ICD])
    rpath = D("RECALL.DBF")
    write_vfp_table(rpath, [("PATID", "C", 6, 0), ("DUEDATE", "D", 8, 0), ("REASON", "C", 20, 0),
                            ("SENT", "L", 1, 0)], recalls)
    # Simulate a crash during a write: header updated, tail of the file lost.
    reclen = 1 + 6 + 8 + 20 + 1
    size = os.path.getsize(rpath)
    with open(rpath, "r+b") as fh:
        fh.truncate(size - 1 - int(reclen * 37.5))

    # Placeholder index files: indexes are rebuilt by the application and carry no data.
    for name in os.listdir(data_dir):
        if name.endswith(".DBF") and name not in ("CPTCODE.DBF", "ICDCODE.DBF", "RECALL.DBF"):
            with open(D(name[:-4] + ".CDX"), "wb") as fh:
                fh.write(b"\x00" * 512)

    # ---------------- document files ----------------
    missing = set(rng.sample(range(len(docs)), min(14, len(docs))))
    for i, d in enumerate(docs):
        rel = d[4].split("ACS\\", 1)[-1].replace("\\", os.sep)
        full = os.path.join(root, rel)
        if i in missing:
            continue
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(tiny_jpeg(rng) if full.endswith(".JPG") else tiny_pdf(f"SYNTHETIC {d[3]} patient {d[1]} document {d[0]}"))
    # Files dropped into patient folders without a DOCUMENT row
    for pid in rng.sample([p["PATID"] for p in patients], 40):
        folder = os.path.join(root, "IMAGES", pid)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"SCAN_{rng.randint(1000, 9999)}.JPG"), "wb") as fh:
            fh.write(tiny_jpeg(rng))
    for k in range(12):
        with open(os.path.join(root, "SOUNDS", f"DICT_{k + 1:03d}.WAV"), "wb") as fh:
            fh.write(tiny_wav(rng))
    for name in ("NEWPATIENT_INTAKE.PDF", "HIPAA_NOTICE.PDF", "REEXAM_TEMPLATE.RTF"):
        with open(os.path.join(root, "TEMPLATES", name), "wb") as fh:
            fh.write(tiny_pdf(name) if name.endswith(".PDF") else b"{\\rtf1\\ansi Synthetic template}")
    with open(os.path.join(root, "ARTICLES", "POSTURE_TIPS.PDF"), "wb") as fh:
        fh.write(tiny_pdf("Posture tips (synthetic)"))
    # Program files and a nightly backup archive, as found in a real install
    with open(os.path.join(root, "ACS.EXE"), "wb") as fh:
        fh.write(b"MZ" + b"\x00" * 1022)
    with zipfile.ZipFile(os.path.join(root, "DATA", "BACKUP_20261030.ZIP"), "w") as z:
        z.writestr("README.TXT", "synthetic nightly backup placeholder")

    print(f"patients {len(patients):,} (deleted-flagged {len(deleted_pat)}), appointments {len(appts):,}, "
          f"visits {len(visits):,} (invalid dates {bad_dates}), diagnoses {len(diags):,}, charges {len(charges):,}, "
          f"payments {len(payments):,}, documents {len(docs):,} (missing files {len(missing)}), "
          f"recalls {len(recalls):,} (last 38 records lost to truncation), photos {len(photos):,}, ghost patients {len(ghost_ids)}")


if __name__ == "__main__":
    main()
