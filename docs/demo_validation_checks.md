# Extraction and validation report

Generated 2026-11-12T13:03:11 by xbase-export 0.1.0.

## Summary

- Tables exported: 13 of 13
- Live records: 704,849
- Deleted-flagged records recovered: 1,657
- Memo text recovered (MB): 30.0
- Relationships inferred: 19
- Document files indexed: 8,764
- Run time (s): 117.8

## Checks

| Table | Check | Status | Detail |
|---|---|---|---|
| recall | record-count | FAIL | header declares 3,551 records, file holds 3,513: 38 missing (truncated or damaged table). Recovered rows exported. |
| appt | link PATID->patient | WARN | 118,676 of 120,154 rows match a live patient.PATID; 1,445 point at a row flagged deleted in patient (recovered); 33 point at no row in patient at all (kept, listed) |
| charge | field-values | WARN | unparseable values exported as empty: SVCDATE=67 |
| documents | referenced-files | WARN | 8,708 of 8,722 file references resolve to a file; 14 referenced files do not exist |
| visit | field-values | WARN | unparseable values exported as empty: VISITDATE=49 |
| * | postgres-load | PASS | loaded with generated scripts into PostgreSQL 18.6 |
| appt | link PROVID->provider | PASS | 120,154 of 120,154 rows match a live provider.PROVID |
| appt | record-count | PASS | 120,960 declared = 120,960 read (120,154 live, 806 flagged deleted) |
| appt | values-csv | PASS | 120,960 rows x 9 columns identical to source |
| appt | values-dbfread | PASS | 120,960 rows x 9 columns identical to source |
| appt | values-postgres | PASS | 120,960 rows x 9 columns identical to source |
| appt | values-sqlite | PASS | 120,960 rows x 9 columns identical to source |
| charge | link CPT->cptcode | PASS | 147,567 of 147,567 rows match a live cptcode.CPT |
| charge | link PROVID->provider | PASS | 147,567 of 147,567 rows match a live provider.PROVID |
| charge | link VISITID->visit | PASS | 147,567 of 147,567 rows match a live visit.VISITID |
| charge | record-count | PASS | 148,308 declared = 148,308 read (147,567 live, 741 flagged deleted) |
| charge | values-csv | PASS | 148,308 rows x 11 columns identical to source |
| charge | values-dbfread | PASS | 148,308 rows x 11 columns identical to source |
| charge | values-postgres | PASS | 148,308 rows x 11 columns identical to source |
| charge | values-sqlite | PASS | 148,308 rows x 11 columns identical to source |
| cptcode | record-count | PASS | 11 declared = 11 read (11 live, 0 flagged deleted) |
| cptcode | values-csv | PASS | 11 rows x 3 columns identical to source |
| cptcode | values-dbfread | PASS | 11 rows x 3 columns identical to source |
| cptcode | values-postgres | PASS | 11 rows x 3 columns identical to source |
| cptcode | values-sqlite | PASS | 11 rows x 3 columns identical to source |
| diagnos | link ICD10->icdcode | PASS | 207,829 of 207,829 rows match a live icdcode.ICD10 |
| diagnos | link VISITID->visit | PASS | 207,829 of 207,829 rows match a live visit.VISITID |
| diagnos | record-count | PASS | 207,829 declared = 207,829 read (207,829 live, 0 flagged deleted) |
| diagnos | values-csv | PASS | 207,829 rows x 4 columns identical to source |
| diagnos | values-dbfread | PASS | 207,829 rows x 4 columns identical to source |
| diagnos | values-postgres | PASS | 207,829 rows x 4 columns identical to source |
| diagnos | values-sqlite | PASS | 207,829 rows x 4 columns identical to source |
| document | record-count | PASS | 8,722 declared = 8,722 read (8,722 live, 0 flagged deleted) |
| document | values-csv | PASS | 8,722 rows x 6 columns identical to source |
| document | values-dbfread | PASS | 8,722 rows x 6 columns identical to source |
| document | values-postgres | PASS | 8,722 rows x 6 columns identical to source |
| document | values-sqlite | PASS | 8,722 rows x 6 columns identical to source |
| icdcode | record-count | PASS | 11 declared = 11 read (11 live, 0 flagged deleted) |
| icdcode | values-csv | PASS | 11 rows x 2 columns identical to source |
| icdcode | values-dbfread | PASS | 11 rows x 2 columns identical to source |
| icdcode | values-postgres | PASS | 11 rows x 2 columns identical to source |
| icdcode | values-sqlite | PASS | 11 rows x 2 columns identical to source |
| insco | record-count | PASS | 8 declared = 8 read (8 live, 0 flagged deleted) |
| insco | values-csv | PASS | 8 rows x 8 columns identical to source |
| insco | values-dbfread | PASS | 8 rows x 8 columns identical to source |
| insco | values-postgres | PASS | 8 rows x 8 columns identical to source |
| insco | values-sqlite | PASS | 8 rows x 8 columns identical to source |
| patient | link PROVID->provider | PASS | 8,915 of 8,915 rows match a live provider.PROVID |
| patient | record-count | PASS | 9,025 declared = 9,025 read (8,915 live, 110 flagged deleted) |
| patient | values-csv | PASS | 9,025 rows x 20 columns identical to source |
| patient | values-dbfread | PASS | 9,025 rows x 20 columns identical to source |
| patient | values-postgres | PASS | 9,025 rows x 20 columns identical to source |
| patient | values-sqlite | PASS | 9,025 rows x 20 columns identical to source |
| patins | link INSCOID->insco | PASS | 7,353 of 7,353 rows match a live insco.INSCOID |
| patins | record-count | PASS | 7,353 declared = 7,353 read (7,353 live, 0 flagged deleted) |
| patins | values-csv | PASS | 7,353 rows x 9 columns identical to source |
| patins | values-dbfread | PASS | 7,353 rows x 9 columns identical to source |
| patins | values-postgres | PASS | 7,353 rows x 9 columns identical to source |
| patins | values-sqlite | PASS | 7,353 rows x 9 columns identical to source |
| payment | link INSCOID->insco | PASS | 84,601 of 84,601 rows match a live insco.INSCOID |
| payment | record-count | PASS | 96,756 declared = 96,756 read (96,756 live, 0 flagged deleted) |
| payment | values-csv | PASS | 96,756 rows x 7 columns identical to source |
| payment | values-dbfread | PASS | 96,756 rows x 7 columns identical to source |
| payment | values-postgres | PASS | 96,756 rows x 7 columns identical to source |
| payment | values-sqlite | PASS | 96,756 rows x 7 columns identical to source |
| provider | record-count | PASS | 5 declared = 5 read (5 live, 0 flagged deleted) |
| provider | values-csv | PASS | 5 rows x 6 columns identical to source |
| provider | values-dbfread | PASS | 5 rows x 6 columns identical to source |
| provider | values-postgres | PASS | 5 rows x 6 columns identical to source |
| provider | values-sqlite | PASS | 5 rows x 6 columns identical to source |
| recall | values-csv | PASS | 3,513 rows x 4 columns identical to source |
| recall | values-dbfread | PASS | 3,513 rows x 4 columns identical to source |
| recall | values-postgres | PASS | 3,513 rows x 4 columns identical to source |
| recall | values-sqlite | PASS | 3,513 rows x 4 columns identical to source |
| visit | link PROVID->provider | PASS | 104,005 of 104,005 rows match a live provider.PROVID |
| visit | record-count | PASS | 104,005 declared = 104,005 read (104,005 live, 0 flagged deleted) |
| visit | values-csv | PASS | 104,005 rows x 10 columns identical to source |
| visit | values-dbfread | PASS | 104,005 rows x 10 columns identical to source |
| visit | values-postgres | PASS | 104,005 rows x 10 columns identical to source |
| visit | values-sqlite | PASS | 104,005 rows x 10 columns identical to source |
| charge | link PATID->patient | INFO | 145,772 of 147,567 rows match a live patient.PATID; 1,795 point at a row flagged deleted in patient (recovered) |
| diagnos | link PATID->patient | INFO | 205,266 of 207,829 rows match a live patient.PATID; 2,563 point at a row flagged deleted in patient (recovered) |
| document | link PATID->patient | INFO | 8,632 of 8,722 rows match a live patient.PATID; 90 point at a row flagged deleted in patient (recovered) |
| documents | unreferenced-files | INFO | 8,764 document files; 56 not referenced by any table, of which 39 are attributed by folder name |
| patins | link PATID->patient | INFO | 7,255 of 7,353 rows match a live patient.PATID; 98 point at a row flagged deleted in patient (recovered) |
| payment | link PATID->patient | INFO | 95,590 of 96,756 rows match a live patient.PATID; 1,166 point at a row flagged deleted in patient (recovered) |
| recall | dbfread-partial-record | INFO | file ends in a partial record; dbfread would parse that fragment as a row, so it was compared on complete records only |
| recall | link PATID->patient | INFO | 3,476 of 3,513 rows match a live patient.PATID; 37 point at a row flagged deleted in patient (recovered) |
| visit | link APPTID->appt | INFO | 103,309 of 104,005 rows match a live appt.APPTID; 696 point at a row flagged deleted in appt (recovered) |
| visit | link PATID->patient | INFO | 102,746 of 104,005 rows match a live patient.PATID; 1,259 point at a row flagged deleted in patient (recovered) |
