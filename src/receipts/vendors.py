"""What the processed claims already tell us about each vendor, from claims_train clerk columns only.

- the GSTIN the clerk typed most often for the vendor (valid values only), used to repair misreads
- the clerk's most common spelling of the vendor, used to output one consistent name

Vendor names are matched loosely: the clerk alone has ~117 spellings of ~70 vendors.
"""
from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process

from .normalize import fix_gstin, gstin_problems, is_blank

REPO_ROOT = Path(__file__).resolve().parents[2]
TRAIN_CSV = REPO_ROOT / "data" / "claims_train.csv"
MATCH_SCORE = 90
# Location tails the clerk appends ("- Wardha", "(Ngp)", "Nagpur"); dropped before matching.
_TAIL = re.compile(r"\s*(?:[-,]\s*)?\(?\b(?:ngp|nagpur|wardha|amravati|chandrapur|yavatmal)\b\)?\s*$", re.I)


def vendor_key(name) -> str:
    s = _TAIL.sub("", str(name))
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    s = re.sub(r"\b(hotel|the)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class VendorBook:
    def __init__(self, train: pd.DataFrame) -> None:
        gst: dict[str, Counter] = {}
        names: dict[str, Counter] = {}
        for vendor, raw in zip(train["clerk_vendor"], train["clerk_gstin"]):
            if is_blank(vendor):
                continue
            k = vendor_key(vendor)
            names.setdefault(k, Counter())[str(vendor).strip()] += 1
            g = fix_gstin(raw)[0] if not is_blank(raw) else ""
            if g and not gstin_problems(g):
                gst.setdefault(k, Counter())[g] += 1
        self.gstin = {k: c.most_common(1)[0][0] for k, c in gst.items()}
        self.names = names
        self._keys = list(names)

    def match(self, vendor) -> str | None:
        """Key of the known vendor this name refers to, or None."""
        k = vendor_key(vendor)
        if not k or not self._keys:
            return None
        if k in self.names:
            return k
        best = process.extractOne(k, self._keys, scorer=fuzz.token_sort_ratio, score_cutoff=MATCH_SCORE)
        return best[0] if best else None

    def known_gstin(self, vendor) -> str | None:
        k = self.match(vendor)
        return self.gstin.get(k) if k else None

    def canonical_name(self, vendor) -> str | None:
        """The clerk's most common spelling for this vendor across all its clusters' spellings."""
        k = self.match(vendor)
        return self.names[k].most_common(1)[0][0] if k else None


@lru_cache(maxsize=1)
def default_book() -> VendorBook:
    if not TRAIN_CSV.is_file():
        return VendorBook(pd.DataFrame({"clerk_vendor": [], "clerk_gstin": []}))
    return VendorBook(pd.read_csv(TRAIN_CSV, dtype=str, keep_default_na=False))
