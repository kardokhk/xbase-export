"""Infer primary keys and foreign-key relationships from the data itself.

Legacy xBase applications rarely declare relationships; they live in program
code. Here a column is a candidate key when every live value is non-empty and
unique. A column in another table is reported as referencing it when the names
match (or the values are almost entirely contained in the key) and most values
are found. Values that are not found are reported as orphans, never dropped.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .export import TableResult

KEY_TYPES = set("CNI+")


@dataclass
class Relation:
    child_table: str
    child_column: str
    parent_table: str
    parent_column: str
    child_values: int        # non-empty live values in the child column
    matched: int
    orphans: int
    basis: str               # "name+values" or "values"
    orphans_parent_deleted: int = 0   # parent row exists but is flagged deleted
    orphans_absent: int = 0           # no parent row at all

    @property
    def coverage(self) -> float:
        return self.matched / self.child_values if self.child_values else 0.0


def candidate_keys(results: List[TableResult]) -> Dict[str, List[str]]:
    keys: Dict[str, List[str]] = {}
    for r in results:
        if r.error or r.live_rows == 0:
            continue
        for c, p in r.profiles.items():
            if p.ftype in KEY_TYPES and not p.distinct_capped and p.nonempty == r.live_rows \
                    and len(p.distinct) == r.live_rows and r.live_rows >= 2:
                keys.setdefault(r.name, []).append(c)
    return keys


def infer_relations(results: List[TableResult], min_coverage: float = 0.90,
                    value_only_coverage: float = 0.99, min_distinct: int = 20) -> List[Relation]:
    by_name = {r.name: r for r in results if not r.error}
    keys = candidate_keys(results)
    rels: List[Relation] = []
    for child in by_name.values():
        child_keys = keys.get(child.name, [])
        for col, prof in child.profiles.items():
            if prof.ftype not in KEY_TYPES or prof.nonempty == 0 or prof.distinct_capped:
                continue
            options = []
            for ptable, pcols in keys.items():
                for pcol in pcols:
                    same_name = pcol == col
                    if ptable == child.name:
                        continue  # self-references are not inferred
                    if not same_name and (len(prof.distinct) < min_distinct or col in child_keys):
                        continue  # unrelated surrogate keys can overlap by chance
                    pvals = by_name[ptable].profiles[pcol].distinct
                    hit = len(prof.distinct & pvals)
                    frac = hit / len(prof.distinct)
                    if frac < (min_coverage if same_name else value_only_coverage):
                        continue
                    options.append((same_name, frac, ptable, pcol, prof.distinct - pvals))
            if not options:
                continue
            same_name, frac, ptable, pcol, missing = max(options, key=lambda o: (o[0], o[1]))
            rel = Relation(child.name, col, ptable, pcol, prof.nonempty, 0, 0,
                           "name+values" if same_name else "values")
            rel._missing = missing  # type: ignore[attr-defined]
            rels.append(rel)
    return rels


def count_orphans(rel: Relation, child_values_iter, deleted_parent_values=frozenset()) -> Relation:
    """Count matched and orphan rows (by row, not distinct value).

    Orphans are split into rows whose parent is flagged deleted in the source
    (recoverable: the parent is in the deleted export) and rows whose parent
    does not exist anywhere.
    """
    missing = getattr(rel, "_missing", set())
    matched = pdel = absent = 0
    for v in child_values_iter:
        if v == "":
            continue
        if v in missing:
            if v in deleted_parent_values:
                pdel += 1
            else:
                absent += 1
        else:
            matched += 1
    rel.matched, rel.orphans = matched, pdel + absent
    rel.orphans_parent_deleted, rel.orphans_absent = pdel, absent
    rel.child_values = matched + pdel + absent
    return rel
