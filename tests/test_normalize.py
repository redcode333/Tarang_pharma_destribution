from datetime import date

import pytest

from receipts.normalize import (
    date_candidates,
    gstin_check_char,
    gstin_problems,
    image_index,
    parse_amount,
    parse_date,
    resolve_image,
)


@pytest.mark.parametrize("raw, expected", [
    ("1250", 1250.0),
    ("1250.50", 1250.5),
    ("Rs 1250", 1250.0),
    ("Rs.1250", 1250.0),
    ("rs. 1,250/-", 1250.0),
    ("RS 1,250.00 /-", 1250.0),
    ("₹1,250", 1250.0),
    ("₹ 1250.5", 1250.5),
    ("1250 Rs", 1250.0),
    ("1250rs.", 1250.0),
    ("INR 1,25,000", 125000.0),
    ("1,250,000", 1250000.0),
    ("Rs 1 250", 1250.0),
    ("  450/-  ", 450.0),
    ("Rs. 450/- only", 450.0),
    ("Rupees 450", 450.0),
    ("450.", 450.0),
    (1250, 1250.0),
    (1250.5, 1250.5),
])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", [
    "", None, float("nan"), "abc", "1250,50", "12,50", "-450", "450-500", "Rs", "two thousand", "1.2.3",
])
def test_parse_amount_rejects(raw):
    assert parse_amount(raw) is None


@pytest.mark.parametrize("raw, expected", [
    ("2026-03-14", date(2026, 3, 14)),
    ("2026/03/14", date(2026, 3, 14)),
    ("2026-03-14 10:22:05", date(2026, 3, 14)),
    ("2026-03-14T10:22:05Z", date(2026, 3, 14)),
    ("20260314", date(2026, 3, 14)),
    ("14/03/2026", date(2026, 3, 14)),
    ("14-03-2026", date(2026, 3, 14)),
    ("14.03.2026", date(2026, 3, 14)),
    ("14/3/26", date(2026, 3, 14)),
    ("03/14/2026", date(2026, 3, 14)),
    ("14 Mar 2026", date(2026, 3, 14)),
    ("14-Mar-26", date(2026, 3, 14)),
    ("14th March, 2026", date(2026, 3, 14)),
    ("Mar 14, 2026", date(2026, 3, 14)),
    ("March 14 2026", date(2026, 3, 14)),
    ("14 Sept 2026", date(2026, 9, 14)),
])
def test_parse_date_unambiguous(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "yesterday", "32/01/2026", "14/13/2026", "2026-02-30", "14 Foo 2026"])
def test_parse_date_rejects(raw):
    assert parse_date(raw) is None


def test_ambiguous_date_defaults_to_day_first():
    assert date_candidates("04/03/2026") == [date(2026, 3, 4), date(2026, 4, 3)]
    assert parse_date("04/03/2026") == date(2026, 3, 4)


def test_ambiguous_date_is_day_first_unless_that_is_after_submission():
    # Day-first is on or before the submission date: keep it.
    assert parse_date("04/03/2026", "2026-03-10") == date(2026, 3, 4)
    assert parse_date("05/02/2026", "2026-02-10") == date(2026, 2, 5)
    assert parse_date("12/01/2026", "2026-01-20") == date(2026, 1, 12)
    # Both readings are on or before: still day-first, even though month-first is closer.
    assert parse_date("04/03/2026", "2026-06-01") == date(2026, 3, 4)
    # Day-first is on the submission date itself: keep it.
    assert parse_date("04/03/2026", "2026-03-04") == date(2026, 3, 4)
    # Day-first lands after submission and month-first does not: month-first.
    assert parse_date("01/12/2026", "2026-01-20") == date(2026, 1, 12)
    assert parse_date("07/06/26", "2026-06-08") == date(2026, 6, 7)
    # Both readings land after submission: stay day-first.
    assert parse_date("04/03/2026", "2026-01-01") == date(2026, 3, 4)
    # Unparseable or missing reference: day-first.
    assert parse_date("04/03/2026", "soon") == date(2026, 3, 4)
    assert parse_date("04/03/2026", None) == date(2026, 3, 4)


def test_month_first_when_day_first_is_not_a_real_date():
    assert parse_date("03/14/2026") == date(2026, 3, 14)
    assert parse_date("03/14/2026", "2026-01-01") == date(2026, 3, 14)


def test_same_day_and_month_is_not_ambiguous():
    assert date_candidates("05/05/2026") == [date(2026, 5, 5)]


def test_resolve_image(tmp_path):
    (tmp_path / "IMG_001.JPG").write_bytes(b"a")
    (tmp_path / "bill_2.jpeg").write_bytes(b"b")
    (tmp_path / "Scan3.png").write_bytes(b"c")
    image_index.cache_clear()
    assert resolve_image("img_001.jpg", tmp_path).name == "IMG_001.JPG"
    assert resolve_image("IMG_001.jpeg", tmp_path).name == "IMG_001.JPG"
    assert resolve_image("C:\\Users\\rep\\Pictures\\BILL_2.JPG", tmp_path).name == "bill_2.jpeg"
    assert resolve_image("/sdcard/DCIM/bill_2.jpg", tmp_path).name == "bill_2.jpeg"
    assert resolve_image("  images/scan3.PNG ", tmp_path).name == "Scan3.png"
    assert resolve_image("scan3.jpg", tmp_path) is None
    assert resolve_image("missing.jpg", tmp_path) is None
    assert resolve_image("", tmp_path) is None
    assert resolve_image(None, tmp_path) is None
    assert resolve_image(float("nan"), tmp_path) is None


def test_gstin_valid():
    assert gstin_check_char("27AAPFU0939F1Z") == "V"
    assert gstin_problems("27AAPFU0939F1ZV") == []
    assert gstin_problems(" 27aapfu0939f1zv ") == []


@pytest.mark.parametrize("raw, problems", [
    ("27AAPFU0939F1Z", ["length"]),
    ("27AAPFU0939F1ZVX", ["length"]),
    ("27AAPFU0939F1ZX", ["checksum"]),
    ("00AAPFU0939F1ZV", ["state_code", "checksum"]),
    ("27AAPF10939F1ZV", ["pan", "checksum"]),
    ("27AAPFU0939F1Z*", ["checksum"]),
])
def test_gstin_invalid(raw, problems):
    assert gstin_problems(raw) == problems
