"""Policy checks. Each returns (passed, reason); the reason is empty when the check passes.

Thresholds are constants here and each one is explained in DECISIONS.md (2026-10-15 entry).
"""
from __future__ import annotations

import re
from datetime import date

from rapidfuzz import fuzz

from .normalize import GSTIN_CONFUSIONS

Check = tuple[bool, str]
PASS: Check = (True, "")

APPROVE_LIMIT = 2_000          # brief: auto-approve only below this; clerks see Rs 2,000 and above
AMOUNT_TOLERANCE = 10          # policy: rounding to the nearest Rs 10 is accepted
DATE_TOLERANCE_DAYS = 1        # bill vs expense date; a late-night bill or a slip in typing
MAX_AGE_DAYS = 60              # bill older than this at submission
FOOD_LIMIT = 2_500             # policy: meals up to Rs 2,500 per bill without prior approval
LODGING_CAP = {"M1": 3_000, "M2": 3_000, "M3": 4_500}  # per night, before GST
LOW_CONFIDENCE = 0.6           # model's own confidence on amount / date
GUEST_NAME_MATCH = 80          # rapidfuzz token_set_ratio, guest name vs employee name

NO_PHOTO = "Photo not received — query to rep"
UNUSABLE_PHOTO = "No usable photo — query to rep"
NOT_READ = "Photo not read yet — run extraction"


def rs(x: float) -> str:
    return f"Rs {x:,.2f}".replace(".00", "") if x is not None else "Rs ?"


def _flags(r: dict) -> set[str]:
    return set(r.get("flags", []))


def check_photo(ext: dict | None) -> Check:
    """1. A photo that was never uploaded, cannot be read, or is not a bill."""
    if ext is None:
        return False, NO_PHOTO
    if ext.get("status") == "not_extracted":  # photo on disk, model not run on it yet (quota)
        return False, NOT_READ
    r = ext.get("result", {})
    if ext.get("status") != "ok" or _flags(r) & {"unreadable", "not_a_receipt", "extraction_failed"}:
        return False, UNUSABLE_PHOTO
    return PASS


def check_injection(r: dict) -> Check:
    """2. Text on the bill addressed to the approval system."""
    if "instruction_text_on_bill" in _flags(r):
        return False, "Receipt contains instructions to the approval system"
    return PASS


def check_alteration(r: dict) -> Check:
    """3. The model saw an altered figure, or the amount in words disagrees with the total."""
    if r.get("possible_alteration") or "amount_words_mismatch" in _flags(r):
        return False, "Amount appears altered"
    return PASS


def check_amount(claimed: float | None, r: dict) -> Check:
    """4. Claimed amount within Rs 10 of the bill total (and so never more than Rs 10 above it)."""
    total = r.get("total_amount")
    if total is None:
        return False, "Bill total could not be read"
    if claimed is None:
        return False, "Claimed amount could not be read"
    if abs(claimed - total) > AMOUNT_TOLERANCE:
        return False, f"Claimed {rs(claimed)} vs bill {rs(total)}"
    return PASS


def check_dates(bill: date | None, expense: date | None, submitted: date | None) -> Check:
    """5. Bill date matches the expense date (±1 day), is not after submission, not over 60 days old."""
    if bill is None:
        return False, "Bill date could not be read"
    problems = []
    if expense is not None and abs((bill - expense).days) > DATE_TOLERANCE_DAYS:
        problems.append(f"Bill dated {bill:%d %b} but expense dated {expense:%d %b}")
    if submitted is not None and bill > submitted:
        problems.append("Bill dated after submission")
    if submitted is not None and (submitted - bill).days > MAX_AGE_DAYS:
        problems.append(f"Bill is {(submitted - bill).days} days old")
    return (False, "; ".join(problems)) if problems else PASS


def check_duplicate(hit: dict | None) -> Check:
    """6. Same bill (number + vendor) or the same photo on an earlier claim."""
    if hit:
        return False, f"Possible duplicate of {hit['claim_id']} ({hit['emp_id']})"
    return PASS


def vehicle_key(v) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(v or "").upper())


def same_vehicle(a: str, b: str) -> bool:
    """Equal, or one character apart where the pair is a known misread (0/8, 5/S, 1/I...)."""
    if a == b:
        return True
    if len(a) != len(b):
        return False
    diff = [(x, y) for x, y in zip(a, b) if x != y]
    return len(diff) == 1 and any(set(diff[0]) == {p, q} for p, q in GSTIN_CONFUSIONS)


def check_fuel(category: str, r: dict, employee: dict | None) -> Check:
    """7. A fuel bill must carry the claimant's own vehicle number."""
    if category.lower() != "fuel":
        return PASS
    on_bill = vehicle_key(r.get("vehicle_no_on_bill"))
    if not on_bill:
        return False, "No vehicle number on fuel bill"
    own = vehicle_key((employee or {}).get("vehicle_no"))
    if not same_vehicle(on_bill, own):
        return False, f"Fuel bill vehicle {r.get('vehicle_no_on_bill')} is not the claimant's"
    return PASS


def check_food(category: str, r: dict) -> Check:
    """8. Meals over Rs 2,500 per bill need prior approval."""
    total = r.get("total_amount")
    if category.lower() == "food" and total is not None and total > FOOD_LIMIT:
        return False, "Food bill over Rs 2,500 — needs prior approval"
    return PASS


def _name(s) -> str:
    return re.sub(r"\b(mr|mrs|ms|dr|shri|smt)\b\.?|[^a-z ]", " ", str(s or "").lower()).strip()


def check_lodging(category: str, r: dict, employee: dict | None) -> Check:
    """9. Hotel bills: valid GSTIN, in the employee's name, room within the grade's nightly cap."""
    if category.lower() != "lodging":
        return PASS
    problems = []
    if not r.get("gstin") or not r.get("gstin_valid"):
        problems.append("Hotel bill has no valid GSTIN")
    emp = employee or {}
    if fuzz.token_set_ratio(_name(r.get("guest_name")), _name(emp.get("name"))) < GUEST_NAME_MATCH:
        problems.append("Hotel bill not in the claimant's name")
    cap = LODGING_CAP.get(str(emp.get("grade", "")).strip().upper())
    pre_tax = r.get("pre_tax_amount") or r.get("total_amount")
    nights = r.get("nights") or 1
    if cap is None:
        problems.append("Grade unknown — cannot check the room cap")
    elif pre_tax is not None and pre_tax / nights > cap:
        problems.append(f"Room {rs(pre_tax / nights)}/night over the {emp.get('grade')} cap of {rs(cap)}")
    return (False, "; ".join(problems)) if problems else PASS


def check_gstin(r: dict) -> Check:
    """A GSTIN was read but fails the checksum even after single-character repair."""
    if r.get("gstin_status") == "unreadable":
        return False, "GSTIN unreadable"
    return PASS


def check_confidence(r: dict) -> Check:
    """10. The model was unsure of the amount or the date."""
    conf = r.get("confidence", {})
    if min(conf.get("total_amount", 0), conf.get("receipt_date", 0)) < LOW_CONFIDENCE:
        return False, "Low-confidence read"
    return PASS
