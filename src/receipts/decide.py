"""approve | review, with reasons. "approve" only when the claim is under Rs 2,000 AND every check passes.

decide() handles one claim; decide_all() runs every claim (train and test) in submission order so
the duplicate check sees all earlier claims, processed ones included.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import rules
from .dedupe import DuplicateIndex
from .extract import ExtractionCache
from .images import file_hash, phash
from .normalize import is_blank, parse_amount, parse_date, resolve_image


@dataclass
class Decision:
    claim_id: str
    decision: str
    reasons: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)  # {name, passed, detail}; passed None = not run

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


def decide(claim: dict, ext: dict | None, employee: dict | None, duplicate: dict | None) -> Decision:
    claimed = parse_amount(claim.get("amount_claimed"))
    submitted = parse_date(claim.get("submitted_on"))
    expense = parse_date(claim.get("expense_date"), claim.get("submitted_on"))
    category = str(claim.get("category", ""))
    checks: list[dict] = []

    def run(name: str, result: tuple[bool, str] | None) -> None:
        passed, detail = result if result is not None else (None, "not checked: no usable photo")
        checks.append({"name": name, "passed": passed, "detail": detail})

    run("Duplicate bill or photo", rules.check_duplicate(duplicate))
    photo = rules.check_photo(ext)
    run("Usable photo", photo)
    r = ext["result"] if photo[0] else None
    run("No instructions on the receipt", rules.check_injection(r) if r else None)
    run("Amount not altered", rules.check_alteration(r) if r else None)
    run("Claimed amount matches the bill", rules.check_amount(claimed, r) if r else None)
    run("Dates", rules.check_dates(parse_date(r.get("receipt_date")), expense, submitted) if r else None)
    run("Fuel: claimant's vehicle", rules.check_fuel(category, r, employee) if r else None)
    run("Food: within Rs 2,500", rules.check_food(category, r) if r else None)
    run("Lodging: GSTIN, name, nightly cap", rules.check_lodging(category, r, employee) if r else None)
    run("GSTIN readable", rules.check_gstin(r) if r else None)
    run("Confident read", rules.check_confidence(r) if r else None)
    if claimed is None:
        run("Under Rs 2,000", (False, "Claimed amount could not be read"))
    else:
        run("Under Rs 2,000", (True, "") if claimed < rules.APPROVE_LIMIT
            else (False, f"{rules.rs(claimed)} is Rs 2,000 or more — clerk review"))
    reasons = list(dict.fromkeys(c["detail"] for c in checks if c["passed"] is False))
    return Decision(str(claim.get("claim_id")), "review" if reasons else "approve", reasons, checks)


def decide_all(claims: pd.DataFrame, employees: pd.DataFrame, cache: ExtractionCache | None = None,
               images_dir: Path | None = None) -> pd.DataFrame:
    """Every claim in submission order. `claims` needs a `split` column (train | test).

    Uses cached extractions only (no model calls). Train claims are indexed for duplicates under
    the clerk's bill number and vendor as well as the model's."""
    cache = cache or ExtractionCache()
    emp = {r["emp_id"].strip(): r for r in employees.to_dict("records")}
    df = claims.copy()
    df["_sub"] = [parse_date(v) for v in df["submitted_on"]]
    df["_order"] = range(len(df))
    df = df.sort_values(["_sub", "_order"], key=lambda s: s.map(str) if s.name == "_sub" else s)
    index = DuplicateIndex()
    rows = []
    for claim in df.to_dict("records"):
        path = resolve_image(claim["image_file"], images_dir) if images_dir else resolve_image(claim["image_file"])
        ext = cache.get(file_hash(path)) if path else None
        if path and ext is None:
            ext = {"status": "not_extracted", "result": {}}
        r = (ext or {}).get("result", {})
        bills = [(r.get("receipt_number"), r.get("vendor") or claim.get("vendor", ""))]
        if claim.get("split") == "train" and not is_blank(claim.get("clerk_bill_no")):
            bills.insert(0, (claim["clerk_bill_no"], claim.get("clerk_vendor") or claim.get("vendor", "")))
        h = phash(path) if path else None
        hit = index.find(bills, h)
        d = decide(claim, ext, emp.get(str(claim["emp_id"]).strip()), hit)
        index.add(claim["claim_id"], claim["emp_id"], bills, h)
        rows.append({"claim_id": claim["claim_id"], "split": claim.get("split"), "decision": d.decision,
                     "reason": d.reason, "reasons": d.reasons, "duplicate_of": (hit or {}).get("claim_id", ""),
                     "duplicate_why": (hit or {}).get("why", ""), "has_photo": path is not None,
                     "extracted": ext is not None and ext.get("status") != "not_extracted", "result": r})
    out = pd.DataFrame(rows).set_index("claim_id")
    return out.loc[claims["claim_id"]].reset_index()  # back to the input order
