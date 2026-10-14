"""Write predictions.csv: one row per test claim, columns and order exactly as data/sample_submission.csv.

Uses cached extractions; a test photo not yet cached is extracted (model call) unless --no-model.
Decisions come from src/receipts/decide.py, run over train + test in submission order so every test
claim is checked for duplicates against all processed claims and the earlier test claims.

Usage: python scripts/make_predictions.py [--no-model]
"""
from __future__ import annotations

import argparse
import string
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from receipts.decide import decide_all  # noqa: E402
from receipts.extract import ExtractionCache, extract  # noqa: E402
from receipts.images import file_hash  # noqa: E402
from receipts.normalize import parse_date, resolve_image  # noqa: E402
from receipts.providers import AllModelsExhausted, NoProviderConfigured  # noqa: E402
from receipts.rules import NO_PHOTO  # noqa: E402

OUT = REPO / "predictions.csv"


def vendor_out(v: str) -> str:
    """Title case: on holdout it matches the clerk's exact spelling 58% of the time, as printed 14%."""
    return string.capwords(str(v or "").strip())


def amount_out(x) -> str:
    if x is None:
        return ""
    x = round(float(x), 2)
    return str(int(x)) if x == int(x) else f"{x:.2f}"


def ensure_extracted(test: pd.DataFrame, allow_model: bool) -> int:
    cache, n = ExtractionCache(), 0
    for f in test["image_file"]:
        p = resolve_image(f)
        if p is None or cache.get(file_hash(p)) is not None:
            continue
        if not allow_model:
            continue
        try:
            extract(p, cache=cache)
            n += 1
        except (NoProviderConfigured, AllModelsExhausted) as exc:
            print(f"Could not extract {p.name}: {exc}", file=sys.stderr)
            break
    return n


def validate(pred: pd.DataFrame, sample: pd.DataFrame) -> list[str]:
    problems = []
    if list(pred.columns) != list(sample.columns):
        problems.append(f"columns {list(pred.columns)} != {list(sample.columns)}")
    if len(pred) != 190 or list(pred["claim_id"]) != list(sample["claim_id"]):
        problems.append("claim_ids differ from sample_submission (count or order)")
    if pred.apply(lambda c: c.astype(str).str.strip().str.lower().isin(["nan", "none", "null"])).any().any():
        problems.append("'nan'/'none' text in a cell")
    if not pred["decision"].isin(["approve", "review"]).all():
        problems.append("decision outside approve/review")
    bad_dates = [d for d in pred["receipt_date"] if d and parse_date(d) is None]
    if bad_dates or any(d and len(d) != 10 for d in pred["receipt_date"]):
        problems.append(f"unparseable dates: {bad_dates[:5]}")
    bad_amt = [a for a in pred["total_amount"] if a and not a.replace(".", "", 1).isdigit()]
    if bad_amt:
        problems.append(f"non-numeric amounts: {bad_amt[:5]}")
    if (pred["decision"].eq("review") & pred["reason"].eq("")).any():
        problems.append("review without a reason")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-model", action="store_true", help="never call a model; uncached photos go to review")
    args = ap.parse_args()

    data = REPO / "data"
    train = pd.read_csv(data / "claims_train.csv", dtype=str, keep_default_na=False).assign(split="train")
    test = pd.read_csv(data / "claims_test.csv", dtype=str, keep_default_na=False).assign(split="test")
    employees = pd.read_csv(data / "employees.csv", dtype=str, keep_default_na=False)
    sample = pd.read_csv(data / "sample_submission.csv", dtype=str, keep_default_na=False)

    called = ensure_extracted(test, allow_model=not args.no_model)
    d = decide_all(pd.concat([train, test], ignore_index=True).fillna(""), employees)
    d = d[d["split"] == "test"].set_index("claim_id")

    rows = []
    for cid in sample["claim_id"]:
        row = d.loc[cid]
        r = row["result"] if row["extracted"] else {}
        has_fields = row["extracted"] and "extraction_failed" not in r.get("flags", [])
        rows.append({
            "claim_id": cid,
            "vendor": vendor_out(r.get("vendor")) if has_fields else "",
            "receipt_date": r.get("receipt_date", "") if has_fields else "",
            "receipt_number": str(r.get("receipt_number", "") or "").strip() if has_fields else "",
            "gstin": r.get("gstin", "") if has_fields else "",
            "total_amount": amount_out(r.get("total_amount")) if has_fields else "",
            "decision": row["decision"],
            "reason": NO_PHOTO if not row["has_photo"] else row["reason"],
        })
    pred = pd.DataFrame(rows, columns=list(sample.columns)).fillna("")
    problems = validate(pred, sample)
    if problems:
        print("NOT WRITTEN, validation failed:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    pred.to_csv(OUT, index=False, encoding="utf-8")
    print(f"Wrote {OUT.name}: {len(pred)} rows; model calls made: {called}")
    print(pred["decision"].value_counts().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
