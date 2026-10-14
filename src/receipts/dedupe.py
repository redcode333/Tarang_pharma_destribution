"""Find a claim's bill on an earlier claim: same bill number at the same vendor, or the same photo.

Claims are fed in submission order; each is checked against everything indexed before it,
then indexed itself. Processed (train) claims are indexed under both the clerk's and the
model's bill number and vendor, so one clerk typo (A/124 for A/1324) cannot hide a repeat.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import imagehash
from rapidfuzz import fuzz

from .normalize import is_blank
from .vendors import vendor_key

PHASH_MAX_DISTANCE = 4   # same as scripts/audit.py: 0 = same file; template look-alikes start at 6
VENDOR_MATCH = 80        # rapidfuzz token_sort_ratio on vendor_key(); the clerk's spellings vary a lot


def bill_key(number) -> str:
    """Bill number for matching: letters and digits only, lower case, leading zeros dropped."""
    s = re.sub(r"[^a-z0-9]", "", str(number or "").lower())
    return s.lstrip("0") or s


def same_vendor(a: str, b: str) -> bool:
    ka, kb = vendor_key(a), vendor_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    ta, tb = set(ka.split()), set(kb.split())
    if min(len(ta), len(tb)) >= 2 and (ta <= tb or tb <= ta):
        return True
    return fuzz.token_sort_ratio(ka, kb) >= VENDOR_MATCH


@dataclass
class _Entry:
    claim_id: str
    emp_id: str
    bills: list[tuple[str, str]]  # (bill_key, vendor as read/typed)
    phash: imagehash.ImageHash | None


@dataclass
class DuplicateIndex:
    entries: list[_Entry] = field(default_factory=list)

    def find(self, bills: list[tuple[str, str]], phash: str | None) -> dict | None:
        """First earlier claim with the same bill or the same photo, as {claim_id, emp_id, why}."""
        keys = [(bill_key(n), v) for n, v in bills if not is_blank(n) and bill_key(n)]
        h = imagehash.hex_to_hash(phash) if phash else None
        for e in self.entries:
            if h is not None and e.phash is not None and (h - e.phash) <= PHASH_MAX_DISTANCE:
                return {"claim_id": e.claim_id, "emp_id": e.emp_id, "why": "same photo"}
            for k, v in keys:
                if any(k == ek and same_vendor(v, ev) for ek, ev in e.bills):
                    return {"claim_id": e.claim_id, "emp_id": e.emp_id, "why": "same bill number and vendor"}
        return None

    def add(self, claim_id: str, emp_id: str, bills: list[tuple[str, str]], phash: str | None) -> None:
        keys = [(bill_key(n), v) for n, v in bills if not is_blank(n) and bill_key(n)]
        self.entries.append(_Entry(claim_id, emp_id, keys, imagehash.hex_to_hash(phash) if phash else None))
