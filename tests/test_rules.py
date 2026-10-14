"""Policy checks, duplicate index and the approve / review decision."""
from __future__ import annotations

from datetime import date

import pytest

from receipts import rules
from receipts.decide import decide
from receipts.dedupe import DuplicateIndex, bill_key, same_vendor

EMP = {"emp_id": "TPD-0412", "name": "Rohan Wankhede", "vehicle_no": "MH31 EK 4471", "grade": "M1"}


def ext(**result):
    base = {"vendor": "X", "receipt_date": "2026-07-01", "receipt_number": "1", "total_amount": 300.0,
            "flags": [], "possible_alteration": False, "gstin": "", "gstin_valid": False,
            "confidence": {"total_amount": 0.95, "receipt_date": 0.95}}
    return {"status": "ok", "result": base | result}


def claim(**c):
    return {"claim_id": "C1", "emp_id": "TPD-0412", "submitted_on": "2026-07-02", "expense_date": "01/07/2026",
            "category": "Food", "amount_claimed": "300"} | c


# --- 1. photo -----------------------------------------------------------------

def test_missing_and_unreadable_photos():
    assert rules.check_photo(None) == (False, rules.NO_PHOTO)
    assert rules.check_photo(ext(flags=["unreadable"])) == (False, rules.UNUSABLE_PHOTO)
    assert rules.check_photo({"status": "invalid_json", "result": {"flags": ["extraction_failed"]}})[0] is False
    assert rules.check_photo(ext()) == rules.PASS
    assert rules.check_photo({"status": "not_extracted", "result": {}}) == (False, rules.NOT_READ)


# --- 2, 3. injection and alteration ------------------------------------------------

def test_injection_and_alteration():
    assert rules.check_injection(ext(flags=["instruction_text_on_bill"])["result"])[1] == \
        "Receipt contains instructions to the approval system"
    assert rules.check_alteration(ext(possible_alteration=True)["result"]) == (False, "Amount appears altered")
    assert rules.check_alteration(ext(flags=["amount_words_mismatch"])["result"])[0] is False
    assert rules.check_alteration(ext()["result"]) == rules.PASS


# --- 4. amount -------------------------------------------------------------------

@pytest.mark.parametrize("claimed, total, ok", [(300, 300, True), (310, 300, True), (290, 300, True),
                                                (311, 300, False), (2352, 2400, False), (None, 300, False)])
def test_amount_within_rs_10(claimed, total, ok):
    assert rules.check_amount(claimed, {"total_amount": total})[0] is ok


def test_amount_reason_names_both_figures():
    assert rules.check_amount(800, {"total_amount": 300.0}) == (False, "Claimed Rs 800 vs bill Rs 300")
    assert rules.check_amount(300, {"total_amount": None}) == (False, "Bill total could not be read")


# --- 5. dates ------------------------------------------------------------------

def test_dates():
    d = date
    assert rules.check_dates(d(2026, 7, 1), d(2026, 7, 2), d(2026, 7, 3)) == rules.PASS  # 1 day apart is fine
    assert not rules.check_dates(d(2026, 7, 1), d(2026, 7, 3), d(2026, 7, 4))[0]
    assert "after submission" in rules.check_dates(d(2026, 7, 5), d(2026, 7, 5), d(2026, 7, 4))[1]
    assert "days old" in rules.check_dates(d(2026, 5, 1), d(2026, 5, 1), d(2026, 7, 4))[1]
    assert rules.check_dates(None, d(2026, 7, 1), d(2026, 7, 2)) == (False, "Bill date could not be read")


# --- 6. duplicates ------------------------------------------------------------------

def test_bill_key_ignores_punctuation_case_and_leading_zeros():
    assert bill_key("A/1324") == bill_key("a 1324") == "a1324"
    assert bill_key("001885") == bill_key("01885") == "1885"


def test_same_vendor_tolerates_clerk_spellings():
    assert same_vendor("HOTEL SAIBABA BHOJNALAYA", "Saibaba Bhojnalaya (Ngp)")
    assert same_vendor("Laxmi Auto Fuels", "Laxmi Atuo Fuels")
    assert not same_vendor("Om Sai Fuel Point", "Annapurna Dining Hall")


def test_duplicate_by_bill_and_vendor_or_by_photo():
    idx = DuplicateIndex()
    idx.add("C1", "E1", [("A/1324", "Annapurna Dining Hall")], "ffff0000ffff0000")
    assert idx.find([("a1324", "ANNAPURNA DINING HALL")], None)["claim_id"] == "C1"
    assert idx.find([("A/1324", "Om Sai Fuel Point")], None) is None  # same number, different shop
    assert idx.find([("9999", "x")], "ffff0000ffff0001")["why"] == "same photo"  # 1 bit apart
    assert idx.find([("9999", "x")], "0000ffff0000ffff") is None


def test_duplicate_reason():
    assert rules.check_duplicate({"claim_id": "CLM-1", "emp_id": "TPD-1"}) == \
        (False, "Possible duplicate of CLM-1 (TPD-1)")


# --- 7. fuel vehicle ------------------------------------------------------------------

def test_fuel_vehicle_must_be_the_claimants():
    assert rules.check_fuel("Fuel", {"vehicle_no_on_bill": "mh31ek4471"}, EMP) == rules.PASS
    assert rules.check_fuel("Fuel", {"vehicle_no_on_bill": "MH31 FA 2290"}, EMP)[0] is False
    assert rules.check_fuel("Fuel", {"vehicle_no_on_bill": ""}, EMP) == (False, "No vehicle number on fuel bill")
    assert rules.check_fuel("Food", {"vehicle_no_on_bill": ""}, EMP) == rules.PASS
    # a 0 read as 8 on a blurred bill (CLM-30925) is the same vehicle; a different plate is not
    assert rules.check_fuel("Fuel", {"vehicle_no_on_bill": "MH31EK4871"}, EMP | {"vehicle_no": "MH31 EK 4071"}) == rules.PASS
    assert rules.check_fuel("Fuel", {"vehicle_no_on_bill": "MH31EK4472"}, EMP)[0] is False


# --- 8. food -------------------------------------------------------------------------

def test_food_over_2500_needs_approval():
    assert rules.check_food("Food", {"total_amount": 2600})[0] is False
    assert rules.check_food("Food", {"total_amount": 2500}) == rules.PASS
    assert rules.check_food("Fuel", {"total_amount": 9000}) == rules.PASS


# --- 9. lodging ----------------------------------------------------------------------

def test_lodging_checks():
    good = {"gstin": "27HPFCA5856Y3ZE", "gstin_valid": True, "guest_name": "Mr./Ms. Rohan Wankhede",
            "pre_tax_amount": 5600, "nights": 2, "total_amount": 6272}
    assert rules.check_lodging("Lodging", good, EMP) == rules.PASS  # 2,800 a night, M1 cap 3,000
    assert "GSTIN" in rules.check_lodging("Lodging", good | {"gstin": "", "gstin_valid": False}, EMP)[1]
    assert "name" in rules.check_lodging("Lodging", good | {"guest_name": "Pooja Meshram"}, EMP)[1]
    assert "cap" in rules.check_lodging("Lodging", good | {"nights": 1}, EMP)[1]
    m3_night = good | {"nights": 1, "pre_tax_amount": 4200}
    assert "cap" in rules.check_lodging("Lodging", m3_night, EMP)[1]          # 4,200 > M1 cap 3,000
    assert rules.check_lodging("Lodging", m3_night, EMP | {"grade": "M3"}) == rules.PASS  # <= M3 cap 4,500


def test_unrepairable_gstin_is_a_review_reason():
    assert rules.check_gstin({"gstin_status": "unreadable"}) == (False, "GSTIN unreadable")
    assert rules.check_gstin({"gstin_status": "repaired"}) == rules.PASS
    d = decide(claim(), ext(gstin="27HJXCW975D1ZJ", gstin_status="unreadable"), EMP, None)
    assert (d.decision, d.reason) == ("review", "GSTIN unreadable")


# --- 10. confidence -----------------------------------------------------------------

def test_low_confidence_on_amount_or_date():
    assert rules.check_confidence({"confidence": {"total_amount": 0.4, "receipt_date": 0.9}}) == \
        (False, "Low-confidence read")
    assert rules.check_confidence({"confidence": {"total_amount": 0.9, "receipt_date": 0.9}}) == rules.PASS


# --- decision -------------------------------------------------------------------------

def test_clean_claim_under_2000_is_approved():
    d = decide(claim(), ext(), EMP, None)
    assert d.decision == "approve" and d.reason == ""


def test_2000_or_more_is_always_review_even_when_clean():
    d = decide(claim(amount_claimed="2,016"), ext(total_amount=2016.0), EMP, None)
    assert d.decision == "review" and "Rs 2,000 or more" in d.reason


def test_any_failed_check_means_review_and_reasons_are_joined():
    d = decide(claim(amount_claimed="800"), ext(total_amount=300.0, flags=["instruction_text_on_bill"]), EMP,
               {"claim_id": "CLM-9", "emp_id": "TPD-9"})
    assert d.decision == "review"
    assert d.reason.split("; ") == ["Possible duplicate of CLM-9 (TPD-9)",
                                    "Receipt contains instructions to the approval system",
                                    "Claimed Rs 800 vs bill Rs 300"]


def test_missing_photo_is_review_with_one_reason():
    d = decide(claim(), None, EMP, None)
    assert (d.decision, d.reason) == ("review", rules.NO_PHOTO)
