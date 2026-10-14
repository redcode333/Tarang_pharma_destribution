"""Score cached extractions against the clerk's typed values on train; write reports/evaluation.md
and reports/error_gallery.md. No model calls.

Dev = the first 80 train rows (prompt changes may only be tuned on these), holdout = the rest.
Accuracy is reported twice: against the clerk as typed, and after adjudicating mismatches where a
person looked at the photo and found the clerk wrong (reports/adjudications.csv, plus automatic
GSTIN rules below). Evaluation may read reports/photo_review.csv; the pipeline may not.

Usage: python scripts/evaluate.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from rapidfuzz import fuzz

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from receipts.decide import decide_all  # noqa: E402
from receipts.extract import ExtractionCache  # noqa: E402
from receipts.rules import APPROVE_LIMIT  # noqa: E402
from receipts.images import file_hash  # noqa: E402
from receipts.normalize import fix_gstin, gstin_problems, is_blank, parse_amount, parse_date, resolve_image  # noqa: E402

DEV_ROWS = 80
VENDOR_FUZZY = 90
FIELDS = ["vendor", "receipt_date", "receipt_number", "gstin", "total_amount"]
# The headline metric per field; the looser / stricter variants are reported beside it.
HEADLINE = {"vendor": "vendor_fuzzy", "receipt_date": "date_exact", "receipt_number": "number_exact",
            "gstin": "gstin_exact", "total_amount": "amount_exact"}
REVIEW = REPO / "reports" / "photo_review.csv"
ADJUDICATIONS = REPO / "reports" / "adjudications.csv"
SAMPLE20 = REPO / "reports" / "sample20_claims.txt"
OUT = REPO / "reports" / "evaluation.md"
GALLERY = REPO / "reports" / "error_gallery.md"
TRAIN_DECISIONS = REPO / "reports" / "train_decisions.csv"
CLAIMS_PER_MONTH = 2_400
ADJ_COLUMNS = ["claim_id", "field", "verdict", "correct_value", "diagnosis"]
VERDICTS = {"clerk_error", "model_error", "both_wrong", "ambiguous"}


# --- field comparison --------------------------------------------------------

def norm_text(s) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(s).lower())).strip()


def compare(field: str, model, clerk) -> dict[str, bool]:
    """Every metric for one field of one claim. Keys are metric names, values True if correct."""
    if field == "vendor":
        a, b = norm_text(model), norm_text(clerk)
        return {"vendor_exact": a == b, "vendor_fuzzy": bool(a) and fuzz.token_sort_ratio(a, b) >= VENDOR_FUZZY}
    if field == "receipt_date":
        return {"date_exact": bool(model) and model == clerk}
    if field == "receipt_number":
        a, b = re.sub(r"\s+", "", str(model)), re.sub(r"\s+", "", str(clerk))
        return {"number_exact": bool(a) and a == b, "number_loose": bool(a) and a.lower() == b.lower()}
    if field == "gstin":
        return {"gstin_exact": str(model) == str(clerk)}
    if field == "total_amount":
        if model is None or clerk is None:
            return {"amount_exact": False, "amount_within_1": False, "amount_within_10": False}
        d = abs(float(model) - float(clerk))
        return {"amount_exact": d < 0.005, "amount_within_1": d <= 1, "amount_within_10": d <= 10}
    raise ValueError(field)


def clerk_values(row) -> dict:
    sub = row["submitted_on"]
    d = parse_date(row["clerk_bill_date"], sub)
    return {"vendor": row["clerk_vendor"].strip(), "receipt_date": d.isoformat() if d else "",
            "receipt_number": row["clerk_bill_no"].strip(), "gstin": fix_gstin(row["clerk_gstin"])[0]
            if not is_blank(row["clerk_gstin"]) else "", "total_amount": parse_amount(row["clerk_bill_amount"])}


def scorable(field: str, clerk: dict) -> bool:
    """A field is scored only where the clerk typed something; GSTIN blank==blank also counts,
    but only on claims where the clerk typed the other fields (otherwise blank means 'not typed')."""
    if field == "gstin":
        return bool(clerk["vendor"] or clerk["receipt_number"])
    v = clerk[field]
    return v is not None and v != ""


# --- adjudication ------------------------------------------------------------

def load_adjudications() -> pd.DataFrame:
    if not ADJUDICATIONS.is_file():
        return pd.DataFrame(columns=ADJ_COLUMNS)
    adj = pd.read_csv(ADJUDICATIONS, dtype=str, keep_default_na=False)
    bad = set(adj["verdict"]) - VERDICTS
    if bad:
        raise SystemExit(f"{ADJUDICATIONS.name}: unknown verdicts {bad}")
    return adj


def auto_gstin_adjudications(rows: pd.DataFrame, train: pd.DataFrame) -> list[dict]:
    """GSTIN mismatches settled without looking: the clerk's value fails the checksum, or the clerk
    left it blank, while the model's value is valid and is the GSTIN the clerk typed for the same
    vendor on other train claims (all of train, extracted or not)."""
    by_vendor: dict[str, Counter] = {}
    for vendor, raw in zip(train["clerk_vendor"], train["clerk_gstin"]):
        g = fix_gstin(raw)[0] if not is_blank(raw) else ""
        if g and not gstin_problems(g):
            by_vendor.setdefault(norm_text(vendor), Counter())[g] += 1
    out = []
    for _, r in rows.iterrows():
        m, c = r["model"].get("gstin", ""), r["clerk"]["gstin"]
        if m == c or not m or gstin_problems(m):
            continue
        known = by_vendor.get(norm_text(r["clerk"]["vendor"]), Counter())
        if m in known and (not c or gstin_problems(c)):
            why = "clerk left it blank" if not c else f"clerk's {c} fails the checksum"
            out.append({"claim_id": r["claim_id"], "field": "gstin", "verdict": "clerk_error", "correct_value": m,
                        "diagnosis": f"auto: {why}; model's GSTIN is valid and typed by the clerk "
                                     f"for this vendor on {known[m]} other claims"})
    return out


# --- loading -----------------------------------------------------------------

def load_rows() -> tuple[pd.DataFrame, int]:
    train = pd.read_csv(REPO / "data" / "claims_train.csv", dtype=str, keep_default_na=False)
    train["split"] = np.where(np.arange(len(train)) < DEV_ROWS, "dev", "holdout")
    review = pd.read_csv(REVIEW, dtype=str, keep_default_na=False)
    condition = dict(zip(review["file"], review["finding"]))
    stats = pd.read_csv(REPO / "cache" / "image_stats.csv", dtype=str, keep_default_na=False)
    dup_files = set(stats.loc[stats["md5"].duplicated(keep=False), "file"])
    sample20 = {l.split("#")[0].strip() for l in SAMPLE20.read_text().splitlines() if l.strip() and not l.startswith("#")}
    cache = ExtractionCache()
    rows, missing_photo = [], 0
    for _, c in train.iterrows():
        path = resolve_image(c["image_file"])
        if path is None:
            missing_photo += 1
            continue
        rec = cache.get(file_hash(path))
        if rec is None:
            continue
        res = rec.get("result", {})
        cond = condition.get(path.name, "")
        rows.append({
            "claim_id": c["claim_id"], "split": c["split"], "category": c["category"], "file": path.name,
            "condition": {"turned 90": "rotated", "upside down": "rotated", "finance-desk stamp": "stamped",
                          "unreadable": "unreadable"}.get(cond, "normal"),
            "duplicate photo": "yes" if path.name in dup_files else "no",
            "handwriting": "yes" if res.get("handwritten_marks") else "no",
            "model_name": rec.get("model", ""), "in_sample20": c["claim_id"] in sample20,
            "model": res, "clerk": clerk_values(c), "status": c["status"], "flags": res.get("flags", []),
        })
    return pd.DataFrame(rows), missing_photo


def score(rows: pd.DataFrame, adj: pd.DataFrame) -> pd.DataFrame:
    """Long table: claim x field x metric, correct vs clerk and after adjudication."""
    adj_map = {(a["claim_id"], a["field"]): a for a in adj.to_dict("records")}
    out = []
    for _, r in rows.iterrows():
        for f in FIELDS:
            if not scorable(f, r["clerk"]):
                continue
            mv = r["model"].get(f)
            mv = "" if mv is None and f != "total_amount" else mv
            vs_clerk = compare(f, mv, r["clerk"][f])
            a = adj_map.get((r["claim_id"], f))
            if a and a["verdict"] == "clerk_error":
                truth = parse_amount(a["correct_value"]) if f == "total_amount" else a["correct_value"]
                adjudicated = compare(f, mv, truth)
            elif a and a["verdict"] == "ambiguous":
                adjudicated = None  # dropped from the adjudicated denominator
            else:
                adjudicated = vs_clerk
            for metric, ok in vs_clerk.items():
                out.append({"claim_id": r["claim_id"], "split": r["split"], "field": f, "metric": metric,
                            "vs_clerk": ok, "adjudicated": None if adjudicated is None else adjudicated[metric],
                            "category": r["category"], "condition": r["condition"],
                            "duplicate photo": r["duplicate photo"], "handwriting": r["handwriting"],
                            "model_name": r["model_name"], "in_sample20": r["in_sample20"],
                            "model_value": mv, "clerk_value": r["clerk"][f]})
    return pd.DataFrame(out)


# --- report ------------------------------------------------------------------

def pct(s: pd.Series) -> str:
    s = s.dropna()
    return f"{s.mean():.1%} ({int(s.sum())}/{len(s)})" if len(s) else "-"


def md(df: pd.DataFrame) -> list[str]:
    lines = ["| " + " | ".join(map(str, df.columns)) + " |", "|" + "---|" * len(df.columns)]
    lines += ["| " + " | ".join(str(v).replace("|", "\\|") for v in row) + " |" for row in df.itertuples(index=False)]
    return lines + [""]


def field_table(s: pd.DataFrame, splits: list[tuple[str, pd.Series]]) -> pd.DataFrame:
    rows = []
    for metric in s["metric"].unique():
        m = s[s["metric"] == metric]
        row = {"field": m["field"].iloc[0], "metric": metric + (" ★" if metric in HEADLINE.values() else "")}
        for name, mask in splits:
            sel = m[mask.reindex(m.index, fill_value=False)]
            row[f"{name} vs clerk"] = pct(sel["vs_clerk"])
            row[f"{name} adjudicated"] = pct(sel["adjudicated"])
        rows.append(row)
    return pd.DataFrame(rows)


def overall(s: pd.DataFrame, col: str) -> str:
    h = s[s["metric"].isin(HEADLINE.values())]
    return pct(h[col])


def breakdown(s: pd.DataFrame, by: str) -> pd.DataFrame:
    h = s[s["metric"].isin(HEADLINE.values())]
    rows = []
    for key, g in h.groupby(by):
        row = {by: key, "claims": g["claim_id"].nunique()}
        for f in FIELDS:
            row[f] = pct(g.loc[g["field"] == f, "adjudicated"])
        row["all fields"] = pct(g["adjudicated"])
        rows.append(row)
    return pd.DataFrame(rows)


def write_gallery(rows: pd.DataFrame, s: pd.DataFrame, adj: pd.DataFrame) -> None:
    files = dict(zip(rows["claim_id"], rows["file"]))
    manual = adj[~adj["diagnosis"].str.startswith("auto:")]
    lines = ["# Error gallery", "",
             "Mismatches between the model and the clerk, each checked against the photo by a person. "
             "Verdicts: **clerk_error** (the photo agrees with the model), **model_error**, **both_wrong**, "
             "**ambiguous** (the photo does not settle it). Source: `reports/adjudications.csv`.", ""]
    counts = manual["verdict"].value_counts()
    lines += [f"Reviewed: {len(manual)}. " + ", ".join(f"{v}: {counts.get(v, 0)}" for v in sorted(VERDICTS)), ""]
    head = s[s["metric"].isin(HEADLINE.values())].set_index(["claim_id", "field"])
    for a in manual.to_dict("records"):
        key = (a["claim_id"], a["field"])
        mv = head.loc[key, "model_value"] if key in head.index else ""
        cv = head.loc[key, "clerk_value"] if key in head.index else ""
        lines += [f"### {a['claim_id']} · {a['field']} · {a['verdict']}",
                  f"`data/images/{files.get(a['claim_id'], '?')}`  ",
                  f"model: `{mv}` · clerk: `{cv}`" + (f" · photo says: `{a['correct_value']}`" if a["correct_value"] else "") + "  ",
                  a["diagnosis"], ""]
    auto = adj[adj["diagnosis"].str.startswith("auto:")]
    if len(auto):
        lines += ["## Settled automatically", ""] + md(auto[["claim_id", "field", "verdict", "correct_value", "diagnosis"]])
    GALLERY.write_text("\n".join(lines), encoding="utf-8")


# --- decisions vs the clerk ----------------------------------------------------

LEAK_REASONS = ("Possible duplicate", "Amount appears altered", "Receipt contains instructions", "Claimed Rs")


def classify(row) -> str:
    """Why we and the clerk disagree, in one of three buckets."""
    if row["ours"] == "approve":
        return "our miss"
    reasons = row["reasons"]
    over = any(r.startswith("Claimed Rs") and _claimed_over(r) for r in reasons)
    if any(r.startswith(LEAK_REASONS[:3]) for r in reasons) or over:
        return "clerk miss"
    if any(r.startswith("Fuel bill vehicle") or r.startswith("Hotel bill not in") for r in reasons):
        return "clerk miss (policy)"
    return "acceptable caution"


def _claimed_over(reason: str) -> bool:
    m = re.match(r"Claimed Rs ([\d,.]+) vs bill Rs ([\d,.]+)", reason)
    return bool(m) and float(m[1].replace(",", "")) > float(m[2].replace(",", ""))


def at_risk(row) -> tuple[float, str]:
    """Rupees a plain auto-approve rule would have paid wrongly on this claim, and how we know."""
    claimed, r = row["claimed"], row["result"]
    if claimed is None or claimed >= APPROVE_LIMIT:
        return 0.0, ""
    reasons = row["reasons"]
    total = r.get("total_amount")
    if any(x.startswith("Possible duplicate") for x in reasons):
        return claimed, "repeat of an earlier bill (whole amount)"
    if any(x.startswith("Amount appears altered") for x in reasons):
        words = r.get("amount_in_words_value")
        if words is not None and claimed > words:
            return claimed - words, "altered: amount in words"
        if total is not None and claimed > total + 10:
            return claimed - total, "altered: printed amount"
        if total is not None and 1000 <= total < 2000:
            return 1000.0, "altered: leading 1 added (estimate Rs 1,000)"
        return 0.0, "altered, original not known"
    if total is not None and claimed > total + 10:
        return claimed - total, "claimed above the bill"
    return 0.0, ""


def decision_section(train: pd.DataFrame, employees: pd.DataFrame) -> list[str]:
    d = decide_all(train.assign(split="train"), employees)
    d["status"] = train.set_index("claim_id").loc[d["claim_id"], "status"].to_numpy()
    d["clerk"] = np.where(d["status"] == "approved", "approve", "review")
    d["ours"] = d["decision"]
    d["claimed"] = [parse_amount(v) for v in train.set_index("claim_id").loc[d["claim_id"], "amount_claimed"]]
    d["kind"] = [classify(r) if r["ours"] != r["clerk"] else "" for _, r in d.iterrows()]
    risk = [at_risk(r) for _, r in d.iterrows()]
    d["at_risk_rs"] = [x for x, _ in risk]
    d["at_risk_why"] = [w for _, w in risk]
    d[["claim_id", "status", "clerk", "ours", "kind", "reason", "claimed", "at_risk_rs", "at_risk_why"]] \
        .to_csv(TRAIN_DECISIONS, index=False)

    under = d["claimed"].map(lambda v: v is not None and v < APPROVE_LIMIT)
    cm = pd.crosstab(d["ours"], d["status"], margins=True, margins_name="all").reset_index()
    cm = cm.rename(columns={"ours": "our decision \ clerk status"})
    disagree = d[d["kind"] != ""].copy()
    caught = d[d["at_risk_rs"] > 0]
    paid = caught[caught["status"] == "approved"]
    per_claim = caught["at_risk_rs"].sum() / len(d)
    lines = ["## Decisions vs the clerk (train)", "",
             "`decision` from `src/receipts/decide.py` on every train claim, in submission order, from cached "
             "extractions. Clerk `approved` = approve; `queried` / `rejected` = review. "
             f"Per-claim detail: `{TRAIN_DECISIONS.relative_to(REPO).as_posix()}`.", ""]
    lines += md(cm)
    lines += [f"- Auto-approved: **{(d['ours'] == 'approve').sum()} of {len(d)}** "
              f"({(d['ours'] == 'approve').mean():.0%}); among claims under Rs 2,000: "
              f"**{(d.loc[under, 'ours'] == 'approve').sum()} of {under.sum()}** ({(d.loc[under, 'ours'] == 'approve').mean():.0%}).",
              f"- Agreement with the clerk: {(d['ours'] == d['clerk']).sum()} of {len(d)}. Disagreements by kind: "
              + ", ".join(f"{k} {n}" for k, n in disagree["kind"].value_counts().items()) + ".", "",
              "### Every disagreement", ""]
    disagree["claimed"] = disagree["claimed"].map(lambda v: f"{v:,.2f}".replace(".00", "") if v is not None else "")
    lines += md(disagree.sort_values(["kind", "claim_id"])[["claim_id", "status", "ours", "kind", "claimed", "reason"]])
    lines += ["### Rupees a plain \"auto-approve everything under Rs 2,000\" rule would pay wrongly", "",
              "Claims under Rs 2,000 that our checks send to review for a repeat bill, an altered amount or a claim "
              "above the bill. A repeat counts in full; an altered or over-claimed bill counts the excess over the "
              "real amount (from the amount in words or the printed figure; a leading \"1\" added to a "
              "three-digit bill counts Rs 1,000).", ""]
    by = caught.groupby("at_risk_why")["at_risk_rs"].agg(claims="count", rupees="sum").reset_index()
    by["rupees"] = by["rupees"].map(lambda v: f"Rs {v:,.0f}")
    lines += md(by.rename(columns={"at_risk_why": "what"}))
    lines += [f"- **Total: Rs {caught['at_risk_rs'].sum():,.0f} on {len(caught)} claims** out of {len(d)} "
              f"(Rs {d['claimed'].map(lambda v: v or 0).sum():,.0f} claimed). The clerk paid "
              f"{len(paid)} of them anyway (Rs {paid['at_risk_rs'].sum():,.0f}).",
              f"- Scaled to {CLAIMS_PER_MONTH:,} claims a month company-wide at the same rate "
              f"(Rs {per_claim:,.2f} per claim): **Rs {per_claim * CLAIMS_PER_MONTH:,.0f} a month**; "
              f"Nagpur division alone ({CLAIMS_PER_MONTH // 10} a month): Rs {per_claim * CLAIMS_PER_MONTH / 10:,.0f}. "
              "This assumes the other divisions look like Nagpur's June-July 2026 claims.", ""]
    return lines


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # the Windows console defaults to cp1252
    rows, missing_photo = load_rows()
    train = pd.read_csv(REPO / "data" / "claims_train.csv", dtype=str, keep_default_na=False)
    train_n = len(train)
    if rows.empty:
        print("No train extractions cached yet.")
        return 1
    manual = load_adjudications()
    auto = pd.DataFrame(auto_gstin_adjudications(rows, train), columns=ADJ_COLUMNS)
    adj = pd.concat([manual, auto[~auto.set_index(["claim_id", "field"]).index.isin(
        manual.set_index(["claim_id", "field"]).index)]], ignore_index=True)
    s = score(rows, adj)

    splits = [("dev", s["split"] == "dev"), ("holdout", s["split"] == "holdout")]
    clean = (s["split"] == "holdout") & ~s["in_sample20"]
    lines = ["# Evaluation: extraction vs the clerk (train)", "",
             "Generated by `scripts/evaluate.py` from `cache/extractions/`. No model calls.", "",
             "## Coverage", "",
             f"- Train claims: {train_n}; photo missing: {missing_photo}; extracted and scored: {len(rows)} "
             f"(dev {int((rows['split'] == 'dev').sum())} of the first {DEV_ROWS}, holdout {int((rows['split'] == 'holdout').sum())}).",
             f"- Models: {', '.join(f'{k} x{v}' for k, v in rows['model_name'].value_counts().items())}.",
             f"- Adjudicated mismatches: {len(manual)} checked by eye, {len(auto)} settled automatically (GSTIN).", "",
             "## Rules", "",
             "- **Scored only where the clerk typed the field.** GSTIN blank = blank counts as correct, on claims "
             "where the clerk typed the other fields. The 6 queried claims with nothing typed are not scored.",
             "- **total_amount** ★ exact to the paisa; also within Rs 1 and within Rs 10 (the policy's rounding).",
             "- **receipt_date** ★ exact date. The clerk's date is parsed day-first, MM/DD only if DD/MM is impossible "
             "or after submission.",
             "- **receipt_number** ★ exact after removing spaces; also case-insensitive.",
             "- **gstin** ★ exact after case/space clean-up and O/0, I/1 fixes on both sides.",
             f"- **vendor** ★ fuzzy: rapidfuzz token_sort_ratio >= {VENDOR_FUZZY} on lower-cased text without punctuation "
             "(the clerk alone has ~117 spellings of ~70 vendors); also exact after that normalisation.",
             "- ★ = headline metric. **Overall** = all ★ field checks pooled.",
             "- **Adjudicated**: where the photo shows the clerk was wrong, the photo's value is the truth; "
             "'ambiguous' mismatches are dropped. The vs-clerk numbers are never changed.", "",
             "## Per-field accuracy", ""]
    lines += md(field_table(s, splits + [("all", s["split"].notna())]))
    lines += [f"**Overall (★ fields pooled)**: dev {overall(s[s['split'] == 'dev'], 'vs_clerk')} vs clerk, "
              f"{overall(s[s['split'] == 'dev'], 'adjudicated')} adjudicated; "
              f"holdout {overall(s[s['split'] == 'holdout'], 'vs_clerk')} vs clerk, "
              f"{overall(s[s['split'] == 'holdout'], 'adjudicated')} adjudicated.", "",
              f"The vendor prompt fix (dealer, not brand) came from the 20-claim sample, which includes "
              f"{int(rows.loc[rows['split'] == 'holdout', 'in_sample20'].sum())} holdout claims. Holdout without them: "
              f"{overall(s[clean], 'vs_clerk')} vs clerk, {overall(s[clean], 'adjudicated')} adjudicated.", ""]
    for by, title in (("category", "By category"), ("condition", "By photo condition (from the by-eye review)"),
                      ("duplicate photo", "Photo is byte-identical to another claim's"),
                      ("handwriting", "Model saw pen marks on the bill"), ("model_name", "By model")):
        lines += [f"## {title} (adjudicated, ★ metrics, dev + holdout)", ""] + md(breakdown(s, by))
    employees = pd.read_csv(REPO / "data" / "employees.csv", dtype=str, keep_default_na=False)
    lines += decision_section(train, employees)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    write_gallery(rows, s, adj)

    mism = s[s["metric"].isin(HEADLINE.values()) & ~s["vs_clerk"]]
    todo = mism[~mism.set_index(["claim_id", "field"]).index.isin(adj.set_index(["claim_id", "field"]).index)]
    todo_path = REPO / "cache" / "mismatches_to_review.csv"
    todo[["claim_id", "split", "field", "model_value", "clerk_value", "category", "condition"]].to_csv(todo_path, index=False)
    print(f"Wrote {OUT.relative_to(REPO)} and {GALLERY.relative_to(REPO)}")
    print(f"Mismatches on ★ metrics: {len(mism)}; not yet adjudicated: {len(todo)} -> {todo_path.relative_to(REPO)}")
    print("\n".join(md(field_table(s, splits))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
