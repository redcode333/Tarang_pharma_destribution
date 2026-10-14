"""Pick a fixed 20-claim train sample and compare its extractions with the clerk's values.

This is evaluation tooling: it reads reports/photo_review.csv to choose the hard cases and to
say what each one should trigger. The pipeline itself never reads that file.

  python scripts/sample_compare.py --select     # writes reports/sample20_claims.txt
  python scripts/run_extract.py --claims-file reports/sample20_claims.txt
  python scripts/sample_compare.py              # writes reports/sample20.md from the cache
"""
from __future__ import annotations

import argparse
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from receipts.extract import ExtractionCache  # noqa: E402
from receipts.images import file_hash  # noqa: E402
from receipts.normalize import fix_gstin, parse_amount, parse_date, resolve_image  # noqa: E402

SAMPLE_FILE = REPO / "reports" / "sample20_claims.txt"
REPORT = REPO / "reports" / "sample20.md"
REVIEW = REPO / "reports" / "photo_review.csv"
SEED = 7
# What each hard case must trigger in the extraction flags.
EXPECT = {"finance-desk stamp": "instruction_text_on_bill", "altered": "possible_alteration",
          "turned 90": "rotation", "upside down": "rotation", "unreadable": "unreadable"}


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    train = pd.read_csv(REPO / "data" / "claims_train.csv", dtype=str, keep_default_na=False)
    train["file"] = [p.name if (p := resolve_image(v)) else "" for v in train["image_file"]]
    review = pd.read_csv(REVIEW, dtype=str, keep_default_na=False)
    return train, review


def select() -> None:
    train, review = load()
    label = dict(zip(review["file"], review["finding"]))
    train["label"] = train["file"].map(label).fillna("")
    train.loc[train["clerk_note"].str.contains("overwritten|altered", case=False) & (train["label"] == ""), "label"] = "altered"
    typed = train["clerk_bill_amount"] != ""
    picks = []

    def take(rows: pd.DataFrame, n: int, why: str) -> None:
        rows = rows[~rows["claim_id"].isin([p[0] for p in picks])]
        for cid in rows.sample(min(n, len(rows)), random_state=SEED)["claim_id"]:
            picks.append((cid, why))

    take(train[train["label"] == "finance-desk stamp"], 3, "stamped")
    take(train[train["label"] == "altered"], 1, "altered, clerk rejected")
    take(train[(train["label"] == "turned 90") & typed], 2, "turned 90")
    take(train[(train["label"] == "upside down") & typed], 1, "upside down")
    take(train[(train["label"] == "unreadable") & train["clerk_note"].str.contains("not clear")], 1, "unreadable")
    normal = train[(train["label"] == "") & typed & (train["file"] != "")]
    for cat in sorted(normal["category"].unique()):
        take(normal[normal["category"] == cat], 2, "normal")
    SAMPLE_FILE.write_text("# 20 train claims for the first extraction run; see scripts/sample_compare.py\n"
                           + "\n".join(f"{cid}  # {why}" for cid, why in picks) + "\n")
    print(f"Wrote {SAMPLE_FILE.relative_to(REPO)}: {len(picks)} claims")


def alnum(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def vendor_mark(model: str, clerk: str) -> str:
    if not clerk:
        return "-"
    a, b = alnum(model), alnum(clerk)
    if a == b or sorted(re.findall(r"[a-z0-9]+", model.lower())) == sorted(re.findall(r"[a-z0-9]+", clerk.lower())):
        return "✓"
    return "≈" if a and SequenceMatcher(None, a, b).ratio() >= 0.8 else "✗"


def pair(model, clerk, mark: str) -> str:
    return f"{model or '∅'} / {clerk or '∅'} {mark}"


def compare() -> None:
    train, review = load()
    label = dict(zip(review["file"], review["finding"]))
    detail = dict(zip(review["file"], review["detail"]))
    lines = [l.split("#") for l in SAMPLE_FILE.read_text().splitlines() if l.strip() and not l.startswith("#")]
    sample = [(c.strip(), why.strip()) for c, why in lines]
    by_id = train.set_index("claim_id")
    cache = ExtractionCache()
    rows, done = [], 0
    for cid, why in sample:
        c = by_id.loc[cid]
        path = resolve_image(c["image_file"])
        rec = cache.get(file_hash(path)) if path else None
        r = rec["result"] if rec else {}
        done += rec is not None
        flags = set(r.get("flags", []))
        lab = label.get(c["file"], "altered" if "altered" in why else "")
        expect = EXPECT.get(lab)
        if expect == "rotation":  # detected = the model said the photo was turned
            caught = bool((rec or {}).get("rotation_detected"))
        elif expect:
            caught = expect in flags
        else:
            caught = None
        cdate = parse_date(c["clerk_bill_date"])
        camt = parse_amount(c["clerk_bill_amount"])
        mamt = r.get("total_amount")
        cg = fix_gstin(c["clerk_gstin"])[0]
        rows.append({
            "claim": cid, "case": why, "model": (rec or {}).get("model", "not run").removeprefix("gemini-"),
            "calls": len((rec or {}).get("passes", [])), "rot": r.get("rotation_applied", ""),
            "vendor (model / clerk)": pair(r.get("vendor", ""), c["clerk_vendor"], vendor_mark(r.get("vendor", ""), c["clerk_vendor"])),
            "date": pair(r.get("receipt_date", ""), cdate.isoformat() if cdate else "",
                         "-" if not cdate else "✓" if r.get("receipt_date") == cdate.isoformat() else "✗"),
            "bill no": pair(r.get("receipt_number", ""), c["clerk_bill_no"],
                            "-" if not c["clerk_bill_no"] else "✓" if alnum(r.get("receipt_number")) == alnum(c["clerk_bill_no"]) else "✗"),
            "GSTIN": pair(r.get("gstin", ""), cg, "✓" if r.get("gstin", "") == cg else "✗") if (cg or r.get("gstin")) else "∅ / ∅ ✓",
            "amount": pair(f"{mamt:g}" if mamt is not None else "", f"{camt:g}" if camt is not None else "",
                           "-" if camt is None else "✓" if mamt is not None and abs(mamt - camt) <= 1 else "✗"),
            "flags": ", ".join(sorted(flags)) or "-",
            "expected": (f"{lab}: {detail.get(c['file'], '')}".strip(": ") if lab else "-"),
            "caught": "-" if caught is None else "✓" if caught else "✗ MISSED",
        })
    df = pd.DataFrame(rows)

    def score(col: str) -> str:
        marks = df[col].str[-1]
        scored = marks[marks.isin(["✓", "✗", "≈"])]
        return f"{(scored == '✓').sum()}/{len(scored)}" + (f" (+{(scored == '≈').sum()} close)" if (scored == "≈").any() else "")

    hard = df[df["caught"] != "-"]
    out = ["# Extraction on a 20-claim train sample", "",
           f"Generated by `scripts/sample_compare.py` from `cache/extractions/`. Extracted: {done} of {len(df)}. "
           "Each cell is *model / clerk* then ✓ match, ≈ close (vendor spelling), ✗ differs, - clerk typed nothing. "
           "Clerk values are not ground truth (DECISIONS.md finding 6), and on altered bills the clerk typed the altered figure.", "",
           f"**Field agreement with the clerk:** vendor {score('vendor (model / clerk)')}, date {score('date')}, "
           f"bill no {score('bill no')}, GSTIN {score('GSTIN')}, amount {score('amount')}. "
           f"**Hard cases caught:** {(hard['caught'] == '✓').sum()}/{len(hard)}.", ""]
    cols = list(df.columns)
    out += ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(v).replace("|", "\\|") for v in row) + " |" for row in df.itertuples(index=False)]
    REPORT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out[4:6]))
    print(f"Wrote {REPORT.relative_to(REPO)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--select", action="store_true")
    (select if ap.parse_args().select else compare)()
