"""Normalise raw claim fields: image references, amounts, dates, GSTINs."""
from __future__ import annotations

import math
import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGES_DIR = REPO_ROOT / "data" / "images"

_BLANKS = {"", "nan", "none", "null", "na", "n/a", "nat", "<na>", "-"}


def is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return str(value).strip().lower() in _BLANKS


# --- images -----------------------------------------------------------------

def image_key(name) -> str:
    """Comparison key for an image reference: basename, lower-cased, .jpeg == .jpg."""
    base = re.split(r"[\\/]", str(name).strip().strip("'\""))[-1].strip().lower()
    stem, dot, ext = base.rpartition(".")
    if not dot:
        return base
    return f"{stem}.{'jpg' if ext == 'jpeg' else ext}"


@lru_cache(maxsize=None)
def image_index(images_dir: Path = IMAGES_DIR) -> dict[str, Path]:
    """Map image_key -> file on disk. If two files share a key, the first by name wins."""
    index: dict[str, Path] = {}
    if images_dir.is_dir():
        for path in sorted(images_dir.rglob("*")):
            if path.is_file():
                index.setdefault(image_key(path.name), path)
    return index


def resolve_image(image_file, images_dir: Path = IMAGES_DIR) -> Path | None:
    """Actual path of a claim's image, or None if blank or not on disk."""
    if is_blank(image_file):
        return None
    return image_index(Path(images_dir)).get(image_key(image_file))


# --- amounts ----------------------------------------------------------------

_CURRENCY_WORD = re.compile(r"(?<![a-z])(inr|rupees?|rs)\.?(?![a-z])")
_AMOUNT = re.compile(r"(\d{1,3}(?:,\d{2,3})*,\d{3}|\d+)(\.\d*)?")


def parse_amount(value) -> float | None:
    """Rupee amount from text like 'Rs. 1,250/-', '₹1250.50', '1250 rs'. None if unparseable.

    Commas are accepted only as thousands/lakh separators, so '1250,50' fails
    rather than silently becoming 125050.
    """
    if is_blank(value):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 2) if math.isfinite(value) and value >= 0 else None
    s = str(value).lower().replace("₹", " ")
    s = _CURRENCY_WORD.sub(" ", s)
    s = re.sub(r"\bonly\b\.?", " ", s)
    s = re.sub(r"/\s*[-=]?\s*$", "", s.strip())
    s = re.sub(r"\s+", "", s)
    m = _AMOUNT.fullmatch(s)
    if not m:
        return None
    return round(float(m.group(1).replace(",", "") + (m.group(2) or "")), 2)


_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), start=2)}
_TENS.update(fourty=40, ninty=90, nienty=90)  # spellings seen on handwritten slips
_SCALES = {"thousand": 1_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000,
           "crore": 10_000_000, "crores": 10_000_000}
_FILLER = {"rupees", "rupee", "rs", "inr", "only", "and", "a"}


def _words_to_int(words: list[str]) -> int | None:
    total = current = 0
    seen = False
    for w in words:
        if w in _UNITS:
            current += _UNITS[w]
        elif w in _TENS:
            current += _TENS[w]
        elif w in ("hundred", "hundreds"):
            current = (current or 1) * 100
        elif w in _SCALES:
            total += (current or 1) * _SCALES[w]
            current = 0
        elif w in _FILLER:
            continue
        else:
            return None
        seen = True
    return total + current if seen else None


def words_to_amount(text) -> float | None:
    """Rupee amount written in words on Indian bills, e.g. 'One Thousand Sixty only',
    'Rupees Two Lakh Five Thousand and Fifty Paise'. None if blank or any word is unknown."""
    if is_blank(text):
        return None
    words = re.findall(r"[a-z]+", str(text).lower())
    if "paise" in words or "paisa" in words:
        cut = words.index("paise" if "paise" in words else "paisa")
        head = words[:cut]
        # The paise figure is the number words just before "paise", after the last "and".
        split = len(head) - 1 - head[::-1].index("and") if "and" in head else 0
        rupees = _words_to_int(head[:split]) if split else 0
        paise = _words_to_int(head[split:])
        if rupees is None or paise is None:
            return None
        return round(rupees + paise / 100, 2)
    value = _words_to_int(words)
    return float(value) if value is not None else None


# --- dates ------------------------------------------------------------------

_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]
_TIME_SUFFIX = re.compile(
    r"[t\s]+\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?\s*([ap]m)?\s*(z|[+-]\d{2}:?\d{2})?$", re.I)
_SEP = r"[\s\-/.,]"
_ISO = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
_NUMERIC = re.compile(r"(\d{1,2})[-/.\s](\d{1,2})[-/.\s](\d{4}|\d{2})")
_DAY_MONTHNAME = re.compile(rf"(\d{{1,2}})(?:st|nd|rd|th)?{_SEP}*([a-z]{{3,9}})\.?{_SEP}*(\d{{4}}|\d{{2}})", re.I)
_MONTHNAME_DAY = re.compile(rf"([a-z]{{3,9}})\.?{_SEP}*(\d{{1,2}})(?:st|nd|rd|th)?{_SEP}+(\d{{4}}|\d{{2}})", re.I)


def _year(y: str) -> int:
    return int(y) + 2000 if len(y) == 2 else int(y)


def _month(name: str) -> int | None:
    name = name.lower()
    for i, full in enumerate(_MONTHS, start=1):
        if full.startswith(name):
            return i
    return 9 if name == "sept" else None


def _make(y: int, m: int | None, d: int) -> date | None:
    try:
        return date(y, m, d)
    except (ValueError, TypeError):
        return None


def date_candidates(value) -> list[date]:
    """Every valid reading of a raw date, day-first reading first. Empty if unparseable.

    Two entries means the value is DD/MM vs MM/DD ambiguous.
    """
    if is_blank(value):
        return []
    if isinstance(value, datetime):
        return [value.date()]
    if isinstance(value, date):
        return [value]
    s = _TIME_SUFFIX.sub("", str(value).strip())
    if m := _ISO.fullmatch(s):
        cands = [_make(int(m[1]), int(m[2]), int(m[3]))]
    elif m := _NUMERIC.fullmatch(s):
        a, b, y = int(m[1]), int(m[2]), _year(m[3])
        cands = [_make(y, b, a), _make(y, a, b)]
    elif m := re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s):
        cands = [_make(int(m[1]), int(m[2]), int(m[3]))]
    elif m := _DAY_MONTHNAME.fullmatch(s):
        cands = [_make(_year(m[3]), _month(m[2]), int(m[1]))]
    elif m := _MONTHNAME_DAY.fullmatch(s):
        cands = [_make(_year(m[3]), _month(m[1]), int(m[2]))]
    else:
        cands = []
    out: list[date] = []
    for c in cands:
        if c is not None and c not in out:
            out.append(c)
    return out


def parse_date(value, submitted_on=None) -> date | None:
    """Parse a date, reading DD/MM by default. MM/DD is used only when DD/MM is not a
    real date, or when DD/MM lands after submitted_on and MM/DD does not."""
    cands = date_candidates(value)
    if not cands:
        return None
    if len(cands) > 1:
        ref = parse_date(submitted_on)
        if ref is not None and cands[0] > ref >= cands[1]:
            return cands[1]
    return cands[0]


# --- GSTIN ------------------------------------------------------------------

_GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# 01-38 are states/UTs; 97 = other territory, 99 = centre jurisdiction.
_GST_STATE_CODES = {f"{i:02d}" for i in range(1, 39)} | {"97", "99"}


def normalize_gstin(value) -> str:
    return re.sub(r"\s+", "", str(value)).upper()


def gstin_check_char(first14: str) -> str:
    """Mod-36 check character for the first 14 characters of a GSTIN."""
    total = 0
    for i, ch in enumerate(first14):
        product = _GSTIN_CHARS.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _GSTIN_CHARS[(36 - total % 36) % 36]


def gstin_problems(value) -> list[str]:
    """Why a GSTIN is invalid: any of 'length', 'state_code', 'pan', 'checksum'. Empty if valid."""
    s = normalize_gstin(value)
    if len(s) != 15:
        return ["length"]
    problems = []
    if s[:2] not in _GST_STATE_CODES:
        problems.append("state_code")
    if not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", s[2:12]):
        problems.append("pan")
    if any(ch not in _GSTIN_CHARS for ch in s) or gstin_check_char(s[:14]) != s[14]:
        problems.append("checksum")
    return problems


# Positions that must be digits (state code, the 4 PAN digits, entity number) or letters (PAN letters).
_GSTIN_DIGIT_POS = (0, 1, 7, 8, 9, 10)
_GSTIN_LETTER_POS = (2, 3, 4, 5, 6, 11)
_TO_DIGIT = str.maketrans("OIL", "011")
_TO_LETTER = str.maketrans("01", "OI")


def fix_gstin(value) -> tuple[str, bool]:
    """(GSTIN, valid). Cleans case and spaces, then swaps O/0 and I/1 where the GSTIN layout
    says the other is required. If it is still invalid, the cleaned raw value comes back
    with valid=False, never a guess."""
    s = normalize_gstin(value)
    if is_blank(s):
        return "", False
    if not gstin_problems(s):
        return s, True
    if len(s) == 15:
        chars = list(s)
        for i in _GSTIN_DIGIT_POS:
            chars[i] = chars[i].translate(_TO_DIGIT)
        for i in _GSTIN_LETTER_POS:
            chars[i] = chars[i].translate(_TO_LETTER)
        fixed = "".join(chars)
        if not gstin_problems(fixed):
            return fixed, True
    return s, False


# Characters a camera or a model confuses in small print; each pair is tried both ways.
GSTIN_CONFUSIONS = [("0", "8"), ("0", "O"), ("5", "S"), ("1", "I"), ("2", "Z"), ("8", "B")]


def gstin_swap_candidates(value: str) -> set[str]:
    """Every valid GSTIN one confusion-set swap away from `value`."""
    s = normalize_gstin(value)
    out = set()
    for i, ch in enumerate(s):
        for a, b in GSTIN_CONFUSIONS:
            for x, y in ((a, b), (b, a)):
                if ch == x:
                    cand = s[:i] + y + s[i + 1:]
                    if not gstin_problems(cand):
                        out.add(cand)
    return out


def repair_gstin(value, known: str | None = None) -> tuple[str, str]:
    """(GSTIN, status). status: "" (none read), "valid", "repaired" or "unreadable".

    A read that fails the checksum after the layout fixes is repaired only when exactly one
    single swap from GSTIN_CONFUSIONS makes it valid, or when a swap gives the GSTIN already
    known for this vendor. Otherwise the raw read comes back as "unreadable"; never a guess."""
    s = normalize_gstin(value)
    if is_blank(s):
        return "", ""
    fixed, ok = fix_gstin(s)
    if ok:
        return fixed, "valid"
    cands = gstin_swap_candidates(s) | (gstin_swap_candidates(fixed) if fixed != s else set())
    if known and known in cands:
        return known, "repaired"
    if len(cands) == 1:
        return cands.pop(), "repaired"
    return s, "unreadable"
