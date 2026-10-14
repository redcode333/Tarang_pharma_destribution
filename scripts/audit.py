"""Audit the raw claims data and write reports/audit.md. pandas only, no LLM calls.

Usage: python scripts/audit.py [--data-dir data] [--out reports/audit.md] [--cache cache/image_stats.csv]
                               [--review reports/photo_review.csv]
"""
from __future__ import annotations

import argparse
import hashlib
import math
import re
import sys
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from receipts.normalize import (  # noqa: E402
    date_candidates,
    gstin_check_char,
    gstin_problems,
    image_index,
    image_key,
    is_blank,
    normalize_gstin,
    parse_amount,
    parse_date,
    resolve_image,
)

# Column names as given in the task brief. If the CSVs differ, fix them here;
# section 0 of the report prints the real headers.
C_ID = "claim_id"
C_EMP = "emp_id"
C_CAT = "category"
C_AMT = "amount_claimed"
C_EXP = "expense_date"
C_SUB = "submitted_on"
C_VENDOR = "vendor"
C_IMG = "image_file"
C_STATUS = "status"
C_NOTE = "clerk_note"
C_CAMT = "clerk_bill_amount"
C_CVENDOR = "clerk_vendor"
C_CDATE = "clerk_bill_date"
C_CBILL = "clerk_bill_no"
C_CGSTIN = "clerk_gstin"

REQUIRED_FILES = ["claims_train.csv", "claims_test.csv", "employees.csv"]
AUTO_APPROVE_LIMIT = 2000
NEAR_AMOUNT_RS = 10
# On the real photos, pairs come at distance 0 (the same file) and then 6, 8, 10... rising
# smoothly; every pair at 6 that was eyeballed is two different bills shot on the same
# template. 4 keeps re-saved or rescaled copies and drops the template look-alikes.
PHASH_MAX_DISTANCE = 4
PARKING_NAMES = ["Rohan", "Pooja", "Harshal"]

LATE_DAYS = 20          # expense-to-submission gap treated as a late submission
VENDOR_SIMILAR = 0.8    # similarity at or above which two vendor spellings are one vendor

# Written from the 13 distinct clerk notes in the June-July 2026 export. First match wins;
# a note that matches nothing is reported as "(unthemed)".
NOTE_THEMES = {
    "duplicate bill": r"same bill",
    "altered bill": r"overwritten|altered|tamper",
    "amount corrected to bill": r"(amt|amount) corrected",
    "no bill photo": r"not attached",
    "unreadable photo": r"not clear|reupload",
    "GST invoice confirmed": r"\bgst\b",
    "routine ok": r"^(ok|verified)\b",
}


class MissingColumns(Exception):
    pass


def need(df: pd.DataFrame, *cols: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise MissingColumns(", ".join(missing))


# --- report helpers ---------------------------------------------------------

def _cell(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, float):
        v = int(v) if v.is_integer() else round(v, 2)
    s = str(v).replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= 90 else s[:87] + "..."


def md_table(df: pd.DataFrame, max_rows: int | None = 15) -> str:
    if df.empty:
        return "_none_"
    shown = df if max_rows is None else df.head(max_rows)
    lines = ["| " + " | ".join(str(c) for c in shown.columns) + " |",
             "|" + "---|" * len(shown.columns)]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in shown.itertuples(index=False)]
    if max_rows is not None and len(df) > max_rows:
        lines += ["", f"_...{len(df) - max_rows} more rows not shown_"]
    return "\n".join(lines)


class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def h(self, level: int, text: str) -> None:
        self.lines += ["", "#" * level + " " + text, ""]

    def p(self, text: str) -> None:
        self.lines += [text, ""]

    def bullets(self, items: list[str]) -> None:
        self.lines += [f"- {i}" for i in items] + [""]

    def table(self, df: pd.DataFrame, max_rows: int | None = 15) -> None:
        self.lines += [md_table(df, max_rows), ""]


def pct(n: int, total: int) -> str:
    return f"{n} ({n / total:.0%})" if total else f"{n}"


def cols(df: pd.DataFrame, *names: str) -> pd.DataFrame:
    """The named columns that exist, in order."""
    return df[[c for c in names if c in df.columns]]


def joined(values) -> str:
    return ", ".join(dict.fromkeys(
        f"{v:g}" if isinstance(v, float) else str(v) for v in values if not is_blank(v)))


def alnum(v) -> str:
    return re.sub(r"[^a-z0-9]", "", str(v).lower())


def vendor_tokens(v) -> list[str]:
    return sorted(re.findall(r"[a-z0-9]+", str(v).lower()))


def vendor_similarity(a, b) -> float:
    """0-1 likeness of two vendor spellings, ignoring case, punctuation and word order."""
    ta, tb = vendor_tokens(a), vendor_tokens(b)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    shorter, longer = sorted((set(ta), set(tb)), key=len)
    if len(shorter) >= 2 and shorter <= longer:  # "Krishna Comforts" vs "Hotel Krishna Comforts"
        return 0.95
    return max(SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio(),
               SequenceMatcher(None, alnum(a), alnum(b)).ratio())


def shape(v) -> str:
    """Format skeleton of a raw value: digit runs -> 9, month names -> Mon."""
    s = re.sub(r"\d+", "9", str(v).strip())
    return re.sub(r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", "Mon", s)


# --- loading ----------------------------------------------------------------

def read_csv(path: Path) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return pd.read_csv(path, dtype=str, keep_default_na=False, encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"Cannot decode {path}")


def raw(df: pd.DataFrame, name: str) -> pd.Series:
    return df[name] if name in df.columns else pd.Series("", index=df.index, dtype=object)


def prepare(claims: pd.DataFrame, employees: pd.DataFrame, images_dir: Path) -> pd.DataFrame:
    """Add parsed helper columns (prefixed _) to the combined train+test claims."""
    df = claims.copy()
    idx = df.index
    df["_img"] = pd.Series([resolve_image(v, images_dir) for v in raw(df, C_IMG)], index=idx, dtype=object)
    df["_img_key"] = ["" if is_blank(v) else image_key(v) for v in raw(df, C_IMG)]
    df["_img_rel"] = ["" if p is None else p.relative_to(images_dir).as_posix() for p in df["_img"]]
    df["_amt"] = pd.Series([parse_amount(v) for v in raw(df, C_AMT)], index=idx, dtype="float64")
    df["_sub"] = pd.Series([parse_date(v) for v in raw(df, C_SUB)], index=idx, dtype=object)
    df["_exp"] = pd.Series([parse_date(v, s) for v, s in zip(raw(df, C_EXP), df["_sub"])], index=idx, dtype=object)
    df["_camt"] = pd.Series([parse_amount(v) for v in raw(df, C_CAMT)], index=idx, dtype="float64")
    df["_cdate"] = pd.Series([parse_date(v, s) for v, s in zip(raw(df, C_CDATE), df["_sub"])], index=idx, dtype=object)

    name_col = next((c for c in employees.columns if "name" in c.lower()), None)
    names = {}
    if name_col and C_EMP in employees.columns:
        names = dict(zip(employees[C_EMP].str.strip(), employees[name_col].str.strip()))
    df["employee"] = [f"{e} {names.get(str(e).strip(), '')}".strip() for e in raw(df, C_EMP)]
    return df


# --- images -----------------------------------------------------------------

IMAGE_STAT_COLS = ["file", "size", "md5", "phash", "width", "height", "exif_orientation", "error"]


def list_images(images_dir: Path) -> list[Path]:
    return sorted(p for p in images_dir.rglob("*") if p.is_file()) if images_dir.is_dir() else []


def image_stats(images_dir: Path, cache_path: Path) -> pd.DataFrame:
    """md5, perceptual hash, size and EXIF orientation of every image; cached by file + size."""
    import imagehash
    from PIL import Image, ImageOps

    cached = {}
    if cache_path.exists():
        old = pd.read_csv(cache_path, dtype=str, keep_default_na=False)
        cached = {(r["file"], r["size"]): r for r in old.to_dict("records")}
    rows = []
    for path in list_images(images_dir):
        rel, size = path.relative_to(images_dir).as_posix(), str(path.stat().st_size)
        row = cached.get((rel, size))
        if row is None:
            row = dict.fromkeys(IMAGE_STAT_COLS, "") | {
                "file": rel, "size": size, "md5": hashlib.md5(path.read_bytes()).hexdigest()}
            try:
                with Image.open(path) as im:
                    row["width"], row["height"] = im.size
                    row["exif_orientation"] = im.getexif().get(274, "")
                    # Hash the image as displayed, so a rotated copy still matches.
                    row["phash"] = str(imagehash.phash(ImageOps.exif_transpose(im)))
            except Exception as exc:  # not an image, truncated, or unsupported format
                row["error"] = type(exc).__name__
        rows.append(row)
    stats = pd.DataFrame(rows, columns=IMAGE_STAT_COLS).astype(str)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    stats.to_csv(cache_path, index=False)
    for c in ("size", "width", "height", "exif_orientation"):
        stats[c] = pd.to_numeric(stats[c], errors="coerce")
    return stats


def near_duplicates(stats: pd.DataFrame, max_distance: int = PHASH_MAX_DISTANCE) -> pd.DataFrame:
    """Pairs of images whose perceptual hashes differ by at most max_distance bits."""
    import imagehash

    ok = stats[stats["phash"] != ""].reset_index(drop=True)
    out_cols = ["file_a", "file_b", "distance", "same_bytes"]
    if len(ok) < 2:
        return pd.DataFrame(columns=out_cols)
    bits = np.array([imagehash.hex_to_hash(h).hash.ravel() for h in ok["phash"]], dtype=np.float32)
    dist = bits @ (1 - bits).T
    dist = dist + dist.T
    i, j = np.where(np.triu(dist <= max_distance, k=1))
    files, md5 = ok["file"].to_numpy(), ok["md5"].to_numpy()
    pairs = pd.DataFrame({"file_a": files[i], "file_b": files[j],
                          "distance": dist[i, j].astype(int), "same_bytes": md5[i] == md5[j]})
    return pairs.sort_values(["distance", "file_a", "file_b"]).reset_index(drop=True)


# --- sections ---------------------------------------------------------------

def sec_schema(ctx, rep: Report) -> None:
    rep.h(2, "0. Inputs")
    rep.bullets([
        f"`{name}`: {len(df)} rows; columns: {', '.join(df.columns)}"
        for name, df in [("claims_train.csv", ctx.train), ("claims_test.csv", ctx.test), ("employees.csv", ctx.employees)]
    ])
    rep.p(f"Images on disk: **{len(ctx.files)}** in `{ctx.images_dir.name}/` "
          f"({joined(f'{n} x {e}' for e, n in pd.Series([p.suffix for p in ctx.files]).value_counts().items()) or 'none'}).")
    if C_ID in ctx.claims.columns:
        dup_ids = ctx.claims[ctx.claims[C_ID].duplicated(keep=False)]
        rep.p(f"Repeated `{C_ID}` values across train+test: **{dup_ids[C_ID].nunique()}**.")
        if len(dup_ids):
            rep.table(cols(dup_ids.sort_values(C_ID), C_ID, "split", C_EMP, C_AMT, C_EXP))


def sec_images(ctx, rep: Report) -> None:
    rep.h(2, "1. Image resolution")
    df = ctx.claims
    need(df, C_IMG)
    blank = df[C_IMG].map(is_blank)
    found = df["_img"].notna()
    rows = []
    for split, g in df.groupby("split", sort=False):
        b, f = blank[g.index], found[g.index]
        rows.append({"split": split, "rows": len(g), "resolved": pct(int(f.sum()), len(g)),
                     "blank image_file": int(b.sum()), "named but not on disk": int((~b & ~f).sum())})
    rep.table(pd.DataFrame(rows))

    base = df[C_IMG].map(lambda v: re.split(r"[\\/]", str(v).strip())[-1])
    renamed = found & (base != df["_img"].map(lambda p: p.name if p is not None else ""))
    with_path = int(df[C_IMG].str.contains(r"[\\/]", regex=True).sum())
    rep.bullets([
        f"Values carrying a folder path (`/` or `\\`): {with_path}",
        f"Resolved only after case / `.jpeg`=`.jpg` normalisation: {int(renamed.sum())}",
        "Raw extensions in the CSVs: " + (joined(
            f"{n} x {e or '(none)'}" for e, n in
            df.loc[~blank, C_IMG].map(lambda v: Path(str(v).strip()).suffix).value_counts().items()) or "none"),
    ])
    if found.mean() < 0.9:
        rep.p(f"**Only {int(found.sum())} of {len(df)} claims have their photo on disk. Sections 1 and 7 "
              "describe an incomplete image set and must be re-run once the photos are imported.**")
    photo_day = pd.to_datetime(df[C_IMG].str.extract(r"IMG[-_](\d{8})")[0], format="%Y%m%d", errors="coerce").dt.date
    lag = pd.Series([(s - p).days if p == p and p is not None and s is not None else np.nan
                     for p, s in zip(photo_day, df["_sub"])], index=df.index).dropna()
    if len(lag):
        rep.p(f"The date inside the photo name is {joined(f'{d:g} day(s) before submission on {n} claims' for d, n in lag.value_counts().sort_index().items())}"
              ". It records when the photo was uploaded, not when the bill was issued.")
    missing = df[~blank & ~found]
    if len(missing):
        rep.p("Named but not on disk:")
        rep.table(cols(missing, C_ID, "split", C_EMP, C_CAT, C_IMG), 20)

    used = set(df.loc[found, "_img_rel"])
    unused = [p.relative_to(ctx.images_dir).as_posix() for p in ctx.files
              if p.relative_to(ctx.images_dir).as_posix() not in used]
    rep.p(f"Images on disk not referenced by any claim: **{len(unused)}** of {len(ctx.files)}.")
    if unused:
        rep.table(pd.DataFrame({"file": unused}), 20)
    keys = pd.Series([image_key(p.name) for p in ctx.files])
    clashes = keys[keys.duplicated(keep=False)].unique()
    if len(clashes):
        rep.p(f"Files on disk that collide after normalisation (first by name wins): {', '.join(clashes)}")


def _parse_failures(df: pd.DataFrame, column: str, parsed: str) -> pd.DataFrame:
    if column not in df.columns:
        return pd.DataFrame()
    bad = df[~df[column].map(is_blank) & df[parsed].isna()]
    return cols(bad, C_ID, "split", C_EMP, column)


def sec_parsers(ctx, rep: Report) -> None:
    rep.h(2, "2. Parsers")
    df = ctx.claims
    fields = [(C_AMT, "_amt"), (C_EXP, "_exp"), (C_SUB, "_sub"), (C_CAMT, "_camt"), (C_CDATE, "_cdate")]
    present = [(c, p) for c, p in fields if c in df.columns]
    rep.table(pd.DataFrame([{
        "column": c, "blank": int(df[c].map(is_blank).sum()), "parsed": int(df[p].notna().sum()),
        "failed to parse": len(_parse_failures(df, c, p))} for c, p in present]))
    for c, p in present:
        fails = _parse_failures(df, c, p)
        if len(fails):
            rep.p(f"Every `{c}` value that failed to parse ({len(fails)}):")
            rep.table(fails, None)

    for c in (C_AMT, C_EXP):
        if c in df.columns:
            shapes = df.loc[~df[c].map(is_blank), c].map(shape).value_counts().reset_index()
            shapes.columns = ["format (digits shown as 9)", "rows"]
            rep.p(f"Formats seen in `{c}`:")
            rep.table(shapes, 25)

    if C_EXP in df.columns:
        n_cands = df[C_EXP].map(lambda v: len(date_candidates(v)))
        amb = df[n_cands > 1].copy()
        rep.h(3, "Ambiguous DD/MM vs MM/DD dates")
        rep.p(f"`{C_EXP}` values with two valid readings: **{len(amb)}**.")
        if len(amb):
            day_first = amb[C_EXP].map(lambda v: date_candidates(v)[0])
            amb["reading"] = np.where(amb["_exp"] == day_first, "day-first", "month-first")
            amb["basis"] = [
                "no submitted_on, default" if s is None
                else "day-first is on or before submitted_on" if date_candidates(v)[0] <= s
                else "day-first is after submitted_on, month-first is not" if date_candidates(v)[1] <= s
                else "both readings after submitted_on, default"
                for v, s in zip(amb[C_EXP], amb["_sub"])]
            summary = amb.groupby(["reading", "basis"]).size().reset_index(name="rows")
            rep.table(summary)
            rep.table(cols(amb.rename(columns={"_exp": "parsed"}), C_ID, "split", C_EXP, C_SUB, "parsed", "reading"), 10)
        both = df[df["_exp"].notna() & df["_sub"].notna()]
        if len(both):
            gap = pd.Series([(s - e).days for e, s in zip(both["_exp"], both["_sub"])], index=both.index)
            rep.p(f"Days from expense to submission: median {gap.median():.0f}, 90th pct {gap.quantile(0.9):.0f}, "
                  f"max {gap.max()}. Expense dated after submission: **{int((gap < 0).sum())}**.")
            late = both[gap < 0]
            if len(late):
                rep.table(cols(late, C_ID, "split", C_EMP, C_EXP, C_SUB), 10)


def note_theme(note) -> str:
    return next((t for t, pattern in NOTE_THEMES.items() if re.search(pattern, str(note).strip(), re.I)),
                "(unthemed)")


def sec_labels(ctx, rep: Report) -> None:
    rep.h(2, "3. Labels (train)")
    tr = ctx.claims[ctx.claims["split"] == "train"].copy()
    need(tr, C_STATUS)
    counts = tr[C_STATUS].value_counts().reset_index()
    counts.columns = [C_STATUS, "rows"]
    counts["share"] = (counts["rows"] / len(tr)).map("{:.1%}".format)
    rep.table(counts)

    tr["band"] = np.where(tr["_amt"].isna(), "unparsed",
                          np.where(tr["_amt"] < AUTO_APPROVE_LIMIT, f"<{AUTO_APPROVE_LIMIT}", f">={AUTO_APPROVE_LIMIT}"))
    if C_CAT in tr.columns:
        rep.h(3, "Amount claimed by category")
        dist = tr.groupby(C_CAT)["_amt"].agg(
            n="count", min="min", median="median", mean="mean",
            p90=lambda s: s.quantile(0.9), max="max", total="sum").round(0).reset_index()
        dist[f"share >={AUTO_APPROVE_LIMIT}"] = [
            f"{(g >= AUTO_APPROVE_LIMIT).mean():.0%}" for _, g in tr.groupby(C_CAT)["_amt"]]
        rep.table(dist, None)
        rep.h(3, "Status by category")
        rep.table(pd.crosstab(tr[C_CAT], tr[C_STATUS], margins=True, margins_name="all").reset_index(), None)
        rep.p("Row percentages:")
        rep.table(pd.crosstab(tr[C_CAT], tr[C_STATUS], normalize="index").mul(100).round(1).reset_index(), None)
    rep.h(3, f"Status by amount band (Rs {AUTO_APPROVE_LIMIT} auto-approve line)")
    rep.table(pd.crosstab(tr["band"], tr[C_STATUS], margins=True, margins_name="all").reset_index(), None)
    rep.p("Row percentages:")
    rep.table(pd.crosstab(tr["band"], tr[C_STATUS], normalize="index").mul(100).round(1).reset_index(), None)
    by_band = tr.groupby("band")["_amt"].agg(claims="count", rupees="sum").reset_index()
    rep.table(by_band)

    need(tr, C_NOTE)
    noted = tr[~tr[C_NOTE].map(is_blank)].copy()
    rep.h(3, "Clerk notes")
    rep.p(f"Rows with a clerk note: {pct(len(noted), len(tr))}; distinct notes: {noted[C_NOTE].nunique()}.")
    rep.p("Status of rows with and without a note:")
    rep.table(pd.crosstab(np.where(tr[C_NOTE].map(is_blank), "no note", "has note"), tr[C_STATUS])
              .rename_axis("note").reset_index())
    all_notes = noted.groupby(C_NOTE).agg(rows=(C_NOTE, "size"), statuses=(C_STATUS, joined)).reset_index()
    all_notes = all_notes.sort_values(["rows", C_NOTE], ascending=[False, True])
    all_notes["theme"] = all_notes[C_NOTE].map(note_theme)
    rep.p("Every distinct note:")
    rep.table(all_notes, None)

    rep.h(3, "Note themes")
    noted["_theme"] = noted[C_NOTE].map(note_theme)
    theme_rows = [{
        "theme": theme, "rows": len(g), "distinct notes": g[C_NOTE].nunique(),
        "statuses": joined(f"{n} {s}" for s, n in g[C_STATUS].value_counts().items()),
        "median Rs": g["_amt"].median(), "total Rs": g["_amt"].sum(),
        "notes": joined(g[C_NOTE].value_counts().index)} for theme, g in noted.groupby("_theme")]
    rep.table(pd.DataFrame(theme_rows).sort_values("rows", ascending=False) if theme_rows else pd.DataFrame(), None)
    rep.p(f"Notes matching no theme: {int((noted['_theme'] == '(unthemed)').sum())} rows. "
          "Themes are keyword rules written from the notes above; the first matching rule wins.")

    not_approved = tr[tr[C_STATUS] != "approved"]
    rep.h(3, "Every claim that was not approved")
    rep.table(cols(not_approved, C_ID, "employee", C_CAT, C_AMT, C_CAMT, C_CBILL, C_STATUS, C_NOTE), None)
    clerk_cols = [c for c in (C_CVENDOR, C_CDATE, C_CBILL, C_CAMT) if c in tr.columns]
    if clerk_cols:
        untyped = tr[(tr[clerk_cols] == "").all(axis=1)]
        rep.p(f"Rows where the clerk typed nothing from the photo: **{len(untyped)}** "
              f"({joined(f'{n} {s}' for s, n in untyped[C_STATUS].value_counts().items())}). "
              "These have no field labels to score extraction against.")


def sec_agreement(ctx, rep: Report) -> None:
    rep.h(2, "4. Rep vs clerk agreement (train)")
    tr = ctx.claims[ctx.claims["split"] == "train"].copy()

    rep.h(3, f"`{C_AMT}` vs `{C_CAMT}`")
    need(tr, C_AMT, C_CAMT)
    both = tr[tr["_amt"].notna() & tr["_camt"].notna()].copy()
    diff = both["_amt"] - both["_camt"]
    both["diff"] = diff.round(2)
    both["agreement"] = np.select(
        [diff.abs() < 0.005, diff.abs() <= NEAR_AMOUNT_RS, diff > 0],
        ["exact", f"within Rs {NEAR_AMOUNT_RS}", f"claimed larger by > Rs {NEAR_AMOUNT_RS}"],
        f"claimed smaller by > Rs {NEAR_AMOUNT_RS}")
    rep.p(f"Rows with both amounts parsed: {len(both)} of {len(tr)} (claimed amount missing on "
          f"{int(tr['_amt'].isna().sum())}, clerk amount missing on {int(tr['_camt'].isna().sum())}).")
    agg = both.groupby("agreement").agg(rows=("diff", "size"), total_diff_rs=("diff", "sum")).reset_index()
    agg["share"] = (agg["rows"] / max(len(both), 1)).map("{:.1%}".format)
    rep.table(agg)
    if C_STATUS in both.columns and len(both):
        rep.table(pd.crosstab(both["agreement"], both[C_STATUS]).reset_index())
    larger = both[diff > NEAR_AMOUNT_RS].sort_values("diff", ascending=False)
    if len(larger):
        rep.p("Largest over-claims:")
        rep.table(cols(larger, C_ID, C_EMP, C_CAT, C_AMT, C_CAMT, "diff", C_STATUS, C_NOTE), 10)

    rep.h(3, f"`{C_VENDOR}` vs `{C_CVENDOR}`")
    need(tr, C_VENDOR, C_CVENDOR)

    def vendor_match(a, b) -> str:
        if is_blank(b):
            return "clerk blank"
        if is_blank(a):
            return "rep blank"
        if str(a) == str(b):
            return "identical"
        if alnum(a) == alnum(b):
            return "same ignoring case/punctuation"
        if vendor_tokens(a) == vendor_tokens(b):
            return "same words, different order"
        if vendor_similarity(a, b) >= VENDOR_SIMILAR:
            return "similar (typo, abbreviation or extra word)"
        return "different"

    tr["vendor_match"] = [vendor_match(a, b) for a, b in zip(tr[C_VENDOR], tr[C_CVENDOR])]
    rep.table(tr["vendor_match"].value_counts().rename_axis("vendor agreement").reset_index(name="rows"))
    for bucket in ("similar (typo, abbreviation or extra word)", "different"):
        rows = tr[tr["vendor_match"] == bucket]
        if len(rows):
            rep.p(f"Examples, {bucket}:")
            rep.table(cols(rows, C_ID, C_EMP, C_VENDOR, C_CVENDOR, C_STATUS), 12)
    typed = tr.loc[tr[C_CVENDOR] != "", C_CVENDOR]
    rep.p(f"The clerk's own spellings vary too: {typed.nunique()} distinct `{C_CVENDOR}` strings, "
          f"{typed.str.lower().nunique()} ignoring case, {typed.map(lambda v: ' '.join(vendor_tokens(v))).nunique()} "
          "ignoring case and word order. Exact string match against the clerk under-counts correct reads.")

    rep.h(3, f"`{C_EXP}` vs `{C_CDATE}`")
    need(tr, C_EXP, C_CDATE)

    def date_match(e, c) -> str:
        if c is None:
            return "clerk blank/unparsed"
        if e is None:
            return "rep blank/unparsed"
        if e == c:
            return "same"
        if (e.year, e.month, e.day) == (c.year, c.day, c.month):
            return "day and month swapped"
        return "within 1 day" if abs((e - c).days) == 1 else "different"

    tr["date_match"] = [date_match(e, c) for e, c in zip(tr["_exp"], tr["_cdate"])]
    rep.table(tr["date_match"].value_counts().rename_axis("date agreement").reset_index(name="rows"))
    off = tr[tr["date_match"].isin(["different", "day and month swapped", "within 1 day"])].copy()
    if len(off):
        later = set()
        if ctx.repeats is not None:
            later = set(ctx.repeats.loc[ctx.repeats["_same_vendor"] & ctx.repeats["_repeat"], C_ID])
        off["later claim of a repeated bill"] = np.where(off[C_ID].isin(later), "yes", "no")
        rep.p(f"Of the {len(off)} claims where the rep's date is not the bill date, "
              f"**{int(off[C_ID].isin(later).sum())}** are a later claim of a bill number already claimed (section 5).")
        rep.table(cols(off, C_ID, C_EMP, C_EXP, C_CDATE, "date_match", C_STATUS, "later claim of a repeated bill"), None)


def _group_rows(g: pd.DataFrame) -> dict:
    return {"n": len(g), "claims": joined(g.get(C_ID, [])), "splits": joined(g["split"]),
            "employees": joined(g["employee"]), "dates": joined(g["_exp"]), "amounts": joined(g["_amt"]),
            "statuses": joined(g.get(C_STATUS, []))}


def find_repeated_bills(claims: pd.DataFrame) -> pd.DataFrame | None:
    """Claims whose clerk bill number also appears on another claim.

    Adds _bill (normalised number), _same_vendor (the claims sharing it name one vendor,
    so it is one bill rather than two shops reusing a number) and _repeat (True for every
    claim after the first one submitted). None if the clerk columns are absent.
    """
    if not {C_CBILL, C_CVENDOR, C_ID}.issubset(claims.columns):
        return None
    has = claims[claims[C_CBILL] != ""].copy()
    has["_bill"] = has[C_CBILL].map(alnum)
    has = has[has["_bill"].duplicated(keep=False)].sort_values(["_bill", "_sub", C_ID])
    has["_same_vendor"] = has.groupby("_bill")[C_CVENDOR].transform(
        lambda v: min(vendor_similarity(a, b) for a, b in combinations(v, 2)) >= VENDOR_SIMILAR)
    has["_repeat"] = has.groupby("_bill").cumcount() > 0
    return has


def _near(a: dict, b: dict) -> bool:
    """Any claimed or bill amount of a is within Rs 10 or 2% of one of b's."""
    xs = [v for v in (a["_amt"], a["_camt"]) if v == v]
    ys = [v for v in (b["_amt"], b["_camt"]) if v == v]
    return any(abs(x - y) <= max(NEAR_AMOUNT_RS, 0.02 * max(x, y)) for x in xs for y in ys)


def sec_duplicates(ctx, rep: Report) -> None:
    rep.h(2, "5. Duplicate scan (train + test)")
    df = ctx.claims.copy()
    gap = pd.Series([(s - e).days if e is not None and s is not None else np.nan
                     for e, s in zip(df["_exp"], df["_sub"])], index=df.index)

    rep.h(3, f"Same `{C_CBILL}` on more than one claim (train only; test has no clerk columns)")
    rb = ctx.repeats
    later_ids: set[str] = set()
    if rb is None:
        rep.p(f"Skipped: no `{C_CBILL}` / `{C_CVENDOR}` columns.")
    else:
        real = rb[rb["_same_vendor"]]
        later = real[real["_repeat"]]
        later_ids = set(later[C_ID])
        paid_again = later[later[C_STATUS] == "approved"]
        exact = sum(1 for _, g in real.groupby("_bill") if g[C_CVENDOR].map(alnum).nunique() == 1)
        same_emp = sum(1 for _, g in real.groupby("_bill") if g[C_EMP].nunique() == 1)
        n_groups = real["_bill"].nunique()
        first = real[~real["_repeat"]].set_index("_bill")
        changed_amt = int((later["_amt"] - later["_bill"].map(first["_amt"])).abs().gt(0.005).sum())
        rep.bullets([
            f"Claims with a bill number: {int((raw(df, C_CBILL) != '').sum())}",
            f"Bill numbers typed on more than one claim: {rb['_bill'].nunique()}; same vendor on every claim "
            f"(one bill claimed again): **{n_groups}**; different vendors (coincidence): "
            f"{rb['_bill'].nunique() - n_groups}",
            f"Later claims of an already-claimed bill: **{len(later)}**; approved: **{len(paid_again)}** "
            f"(Rs {paid_again['_amt'].sum():,.2f}); stopped by the clerk: {len(later) - len(paid_again)}",
            f"Claimed again by the same employee: {same_emp} bills; by a different employee: {n_groups - same_emp} bills",
            f"On the later claim the rep typed a date different from the bill date: "
            f"{int((later['_exp'] != later['_cdate']).sum())} of {len(later)}; "
            f"a different amount from the first claim: {changed_amt} of {len(later)}",
            f"Days from bill date to submission, later claims: median "
            f"{pd.Series([(s - c).days for c, s in zip(later['_cdate'], later['_sub'])]).median():.0f} "
            f"(all claims, from the rep's expense date: {gap.median():.0f})",
            f"Grouping on the clerk's exact vendor spelling would find only {exact} of the {n_groups}",
        ])
        rows = [{"bill_no": g[C_CBILL].iloc[0], "clerk vendor": g[C_CVENDOR].iloc[0],
                 "claims, first submitted first": ", ".join(g[C_ID]), "employees": joined(g["employee"]),
                 "bill date": joined(g["_cdate"]), "rep dates": joined(g["_exp"]),
                 "bill Rs": joined(g["_camt"]), "claimed Rs": ", ".join(f"{v:g}" for v in g["_amt"]),
                 "statuses": ", ".join(g[C_STATUS]),
                 "one vendor": "yes" if g["_same_vendor"].iloc[0] else "NO"}
                for _, g in rb.groupby("_bill")]
        rep.table(pd.DataFrame(rows), None)

    rep.h(3, "Same image file referenced by more than one claim")
    has_img = df[df["_img_key"] != ""]
    groups = [{"image": key, **_group_rows(g)} for key, g in has_img.groupby("_img_key") if len(g) > 1]
    rep.p(f"Image files referenced more than once: **{len(groups)}** "
          f"({has_img['_img_key'].nunique()} distinct names across {len(has_img)} claims).")
    rep.table(pd.DataFrame(groups).sort_values("n", ascending=False) if groups else pd.DataFrame(), 25)

    rep.h(3, "Same vendor + same date + near amount")
    rep.p("Vendor and date are the clerk's where typed, otherwise the rep's. Vendors match on similarity "
          f">= {VENDOR_SIMILAR}; amounts within Rs {NEAR_AMOUNT_RS} or 2%. A test claim only has the rep's date, "
          "so a repeat whose rep typed a different date is not caught here; that needs the date read off the photo.")
    df["_bvendor"] = [c if not is_blank(c) else v for c, v in zip(raw(df, C_CVENDOR), raw(df, C_VENDOR))]
    df["_bdate"] = [c if c is not None else e for c, e in zip(df["_cdate"], df["_exp"])]
    df["_billkey"] = raw(df, C_CBILL).map(lambda v: "" if is_blank(v) else alnum(v))
    pairs = []
    for _, g in df[df["_bdate"].notna() & df["_amt"].notna()].groupby("_bdate"):
        for a, b in combinations(g.to_dict("records"), 2):
            if vendor_similarity(a["_bvendor"], b["_bvendor"]) < VENDOR_SIMILAR or not _near(a, b):
                continue
            if a["_billkey"] and b["_billkey"]:
                bills = "same bill no" if a["_billkey"] == b["_billkey"] else "different bill nos"
            else:
                bills = "bill no not known"
            pairs.append({"vendor": a["_bvendor"], "date": a["_bdate"], "claim_a": a.get(C_ID), "claim_b": b.get(C_ID),
                          "claimed_a": a["_amt"], "claimed_b": b["_amt"], "who_a": a["employee"], "who_b": b["employee"],
                          "bill nos": bills, "same employee": "yes" if a.get(C_EMP) == b.get(C_EMP) else "no",
                          "splits": f"{a['split']}/{b['split']}", "category": a.get(C_CAT, ""),
                          "statuses": ", ".join(str(x.get(C_STATUS, "") or "-") for x in (a, b))})
    pairs = pd.DataFrame(pairs)
    rep.p(f"Pairs found: **{len(pairs)}**.")
    if len(pairs):
        rep.table(pairs.groupby(["bill nos", "same employee"]).size().reset_index(name="pairs"))
        unknown = pairs[pairs["bill nos"] == "bill no not known"]
        rep.p("Pairs where a bill number cannot settle it (a test claim, or a claim the clerk queried):")
        rep.table(unknown.drop(columns=["bill nos"]), None)
        distinct = pairs[pairs["bill nos"] == "different bill nos"]
        rep.p(f"Pairs the bill numbers show to be two separate bills: {len(distinct)}, by category: "
              f"{joined(f'{n} {c}' for c, n in distinct['category'].value_counts().items()) or 'none'}. "
              "Vendor + date + amount alone would wrongly flag these.")

    rep.h(3, f"Late submissions (more than {LATE_DAYS} days after the expense date)")
    late = df[gap > LATE_DAYS].copy()
    late["days late"] = gap[late.index].astype(int)
    late["repeat of an earlier bill"] = [
        "not known (test)" if s == "test" else "yes" if c in later_ids else "no"
        for s, c in zip(late["split"], late.get(C_ID, late.index))]
    n_train = int((late["split"] == "train").sum())
    rep.p(f"Late claims: {n_train} of {int((df['split'] == 'train').sum())} in train, "
          f"{len(late) - n_train} of {int((df['split'] == 'test').sum())} in test. "
          f"In train, **{int((late['repeat of an earlier bill'] == 'yes').sum())} of {n_train}** are a later claim "
          "of a bill number already claimed.")
    rep.table(cols(late, C_ID, "split", "employee", C_CAT, C_VENDOR, C_EXP, C_SUB, "days late", C_AMT,
                   C_STATUS, "repeat of an earlier bill"), None)

    rep.h(3, "Hotel Shubham")
    mask = raw(df, C_VENDOR).str.contains("shubham", case=False, regex=False) | \
        raw(df, C_CVENDOR).str.contains("shubham", case=False, regex=False)
    typo = pd.Series([max(vendor_similarity(v, "Hotel Shubham Residency"),
                          vendor_similarity(c, "Hotel Shubham Residency")) >= VENDOR_SIMILAR
                      for v, c in zip(raw(df, C_VENDOR), raw(df, C_CVENDOR))], index=df.index)
    shubham = df[mask | typo]
    rep.p(f"Claims for a vendor named like \"Hotel Shubham Residency\": **{len(shubham)}** "
          f"from {shubham[C_EMP].nunique() if C_EMP in shubham else '?'} employees "
          f"({int((~mask & typo).sum())} found only through a misspelt name).")
    if len(shubham):
        rep.table(cols(shubham.sort_values("_exp", key=lambda s: s.astype(str)),
                       C_ID, "split", "employee", C_VENDOR, C_EXP, C_SUB, C_AMT, C_CAMT, C_CBILL,
                       C_STATUS, C_NOTE), None)


def sec_parking(ctx, rep: Report) -> None:
    rep.h(2, "6. Parking claims: " + ", ".join(PARKING_NAMES))
    emp, df = ctx.employees, ctx.claims
    name_col = next((c for c in emp.columns if "name" in c.lower()), None)
    if name_col is None:
        raise MissingColumns("a name column in employees.csv")
    need(emp, C_EMP)
    need(df, C_EMP, C_CAT)
    pattern = r"\b(?:" + "|".join(PARKING_NAMES) + r")\b"
    people = emp[emp[name_col].str.contains(pattern, case=False, regex=True)]
    rep.p("Matching employees:")
    rep.table(people, None)

    parking = df[df[C_CAT].str.contains("park", case=False, regex=False)].copy()
    rep.p(f"Parking claims in train+test: {len(parking)} from {parking[C_EMP].nunique()} employees.")
    if parking.empty:
        return
    parking["_bill"] = raw(parking, C_CBILL).map(lambda v: "" if is_blank(v) else alnum(v))

    def summarise(g: pd.DataFrame) -> dict:
        dates = [d for d in g["_exp"] if d is not None]
        months = len({(d.year, d.month) for d in dates}) or 1
        top_amt = g["_amt"].value_counts()
        bills = g.loc[g["_bill"] != "", "_bill"]
        imgs = g.loc[g["_img_key"] != "", "_img_key"]
        return {"claims": len(g), "per active month": round(len(g) / months, 1),
                "first": min(dates, default=""), "last": max(dates, default=""),
                "min Rs": g["_amt"].min(), "median Rs": g["_amt"].median(), "max Rs": g["_amt"].max(),
                "total Rs": g["_amt"].sum(), "distinct amounts": g["_amt"].nunique(),
                "most common amount": f"{top_amt.index[0]:g} x{top_amt.iloc[0]}" if len(top_amt) else "",
                "repeated bill nos": int(bills.duplicated(keep=False).sum()),
                "repeated images": int(imgs.duplicated(keep=False).sum()),
                "statuses": joined(f"{n} {s}" for s, n in g[C_STATUS].value_counts().items() if s) if C_STATUS in g else ""}

    ids = set(people[C_EMP].str.strip())
    theirs = parking[parking[C_EMP].str.strip().isin(ids)]
    rows = [{"who": who, **summarise(g)} for who, g in theirs.groupby("employee")]
    others = parking[~parking[C_EMP].str.strip().isin(ids)]
    if len(others):
        per_emp = others.groupby(C_EMP).size()
        rows.append({"who": f"all other employees ({len(per_emp)}; median {per_emp.median():g} claims each)",
                     **summarise(others)})
    rep.table(pd.DataFrame(rows), None)

    if len(theirs):
        rep.p("Their parking claims:")
        rep.table(cols(theirs.sort_values(["employee", "_exp"], key=lambda s: s.astype(str)),
                       C_ID, "split", "employee", C_EXP, C_AMT, C_VENDOR, C_CBILL, C_IMG, C_STATUS, C_NOTE), 60)
        for label, key in (("bill numbers", "_bill"), ("image files", "_img_key")):
            rep_groups = [{label: k, **_group_rows(g)} for k, g in theirs[theirs[key] != ""].groupby(key) if len(g) > 1]
            rep.p(f"Repeated {label} among their parking claims: **{len(rep_groups)}**.")
            rep.table(pd.DataFrame(rep_groups), 20)
        files = set(theirs.loc[theirs["_img_rel"] != "", "_img_rel"])
        near = ctx.pairs[ctx.pairs["file_a"].isin(files) | ctx.pairs["file_b"].isin(files)]
        rep.p(f"Near-duplicate image pairs (section 7) involving their parking receipts: **{len(near)}**.")
        rep.table(cols(near, "file_a", "file_b", "distance", "claims_a", "claims_b", "who_a", "who_b"), 20)


def sec_image_hashes(ctx, rep: Report) -> None:
    rep.h(2, "7. Images: near-duplicates and orientation")
    stats, pairs = ctx.stats, ctx.pairs
    unreadable = stats[stats["error"] != ""]
    rep.p(f"Images hashed: {int((stats['phash'] != '').sum())} of {len(stats)}; unreadable: **{len(unreadable)}**.")
    if len(unreadable):
        rep.table(unreadable[["file", "size", "error"]], 20)

    same = stats[stats["md5"].duplicated(keep=False)]
    rep.p(f"Byte-identical files under different names: {same['md5'].nunique()} groups, {len(same)} files.")

    rep.h(3, f"Near-duplicate pairs (pHash distance <= {PHASH_MAX_DISTANCE})")
    if len(pairs):
        cross_emp = pairs[pairs["same_employee"] == "no"]
        rep.bullets([
            f"Pairs: **{len(pairs)}** ({int(pairs['same_bytes'].sum())} byte-identical, "
            f"{int((pairs['distance'] == 0).sum())} at distance 0)",
            f"Pairs across different employees: **{len(cross_emp)}**",
            f"Pairs where both images are claimed: {int(((pairs['claims_a'] != '') & (pairs['claims_b'] != '')).sum())}",
        ])
    else:
        rep.p("No pairs found.")
    rep.table(cols(pairs, "file_a", "file_b", "distance", "same_bytes", "claims_a", "claims_b",
                   "who_a", "who_b", "same_employee", "clerk bill nos"), 40)
    if len(pairs):
        rep.p("`clerk bill nos` shows whether the bill-number check (section 5) could catch the pair: "
              "\"same\" it does, \"differ\" means the clerk typed two numbers for one photo, "
              "\"not typed\" means at least one claim is unprocessed.")

    rep.h(3, "Sideways images")
    ok = stats[stats["error"] == ""].copy()
    orient = ok["exif_orientation"].fillna(0).astype(int)
    rotated = orient.isin([5, 6, 7, 8])
    ok["shown_w"] = np.where(rotated, ok["height"], ok["width"])
    ok["shown_h"] = np.where(rotated, ok["width"], ok["height"])
    landscape = ok["shown_w"] > ok["shown_h"]
    counts = orient.map(lambda o: "none" if o == 0 else str(o)).value_counts().rename_axis("EXIF orientation").reset_index(name="files")
    rep.table(counts)
    rep.bullets([
        f"EXIF says rotate 90/270 (orientation 5-8): **{int(rotated.sum())}** - fixed by applying EXIF before reading",
        f"Wider than tall after applying EXIF: {int(landscape.sum())} of {len(ok)}. This is not a rotation "
        "signal here: most photos are framed wider than the slip, and many slips are landscape. "
        "Turned photos are counted by eye in the visual review below.",
    ])

    rep.h(3, "Visual review")
    review = ctx.review
    if review is None:
        rep.p("No `reports/photo_review.csv`; nothing reviewed by eye.")
        return
    rep.p(f"From `reports/photo_review.csv`: every photo looked at by eye on contact sheets (EXIF applied), "
          f"turned, unreadable and stamped photos written down. {len(review)} rows.")
    review = review.assign(claims=review["file"].map(ctx.claims_by_file).fillna(""))
    by_claim = ctx.claims.set_index(C_ID) if C_ID in ctx.claims.columns else None
    summary = []
    for finding, g in review.groupby("finding", sort=False):
        ids = [i for c in g["claims"] for i in c.split(", ") if i]
        rows = by_claim.loc[ids] if by_claim is not None and ids else pd.DataFrame()
        summary.append({"finding": finding, "photos": len(g), "claims": len(ids),
                        "splits": joined(f"{n} {s}" for s, n in rows["split"].value_counts().items()) if len(rows) else "",
                        "train statuses": joined(f"{n} {s}" for s, n in rows.loc[rows["split"] == "train", C_STATUS]
                                                 .value_counts().items()) if len(rows) and C_STATUS in rows else ""})
    rep.table(pd.DataFrame(summary), None)
    detail = review[review["detail"] != ""].copy()
    if len(detail) and by_claim is not None:
        first = detail["claims"].str.split(", ").str[0]
        for c in (C_EMP, C_AMT, C_CAMT, C_STATUS, C_NOTE):
            if c in by_claim.columns:
                detail[c] = first.map(by_claim[c]).fillna("")
        rep.table(cols(detail, "file", "finding", "detail", "claims", C_EMP, C_AMT, C_CAMT, C_STATUS, C_NOTE), None)


def sec_gstin(ctx, rep: Report) -> None:
    rep.h(2, "8. GSTIN checks (`clerk_gstin`)")
    df = ctx.claims
    need(df, C_CGSTIN)
    has = df[~df[C_CGSTIN].map(is_blank)].copy()
    has["problems"] = has[C_CGSTIN].map(lambda v: ", ".join(gstin_problems(v)))
    invalid = has[has["problems"] != ""].copy()
    untidy = has[has[C_CGSTIN] != has[C_CGSTIN].map(normalize_gstin)]
    rep.p(f"Rows with a clerk GSTIN: {len(has)} ({joined(f'{n} {s}' for s, n in has['split'].value_counts().items())}; "
          f"{has[C_CGSTIN].map(normalize_gstin).nunique()} distinct values). "
          f"Invalid: **{pct(len(invalid), len(has))}** rows, {invalid[C_CGSTIN].map(normalize_gstin).nunique()} distinct values. "
          f"Needed case/space clean-up before checking: {len(untidy)}.")
    if len(invalid):
        reasons = invalid["problems"].str.split(", ").explode().value_counts()
        rep.table(reasons.rename_axis("failed check").reset_index(name="rows"))
        # Is each invalid value a typing slip of a valid GSTIN seen elsewhere in the data?
        valid = has.loc[has["problems"] == "", C_CGSTIN].map(normalize_gstin).value_counts()

        def nearest(value: str) -> tuple[str, int]:
            s = normalize_gstin(value)
            same_len = [(sum(a != b for a, b in zip(s, v)), v) for v in valid.index if len(v) == len(s)]
            return min(same_len)[::-1] if same_len else ("", -1)

        near = invalid[C_CGSTIN].map(nearest)
        invalid["nearest valid GSTIN in the data"] = near.map(lambda t: t[0])
        invalid["characters different"] = near.map(lambda t: t[1])
        invalid["times the valid one appears"] = invalid["nearest valid GSTIN in the data"].map(valid).fillna(0).astype(int)
        invalid["expected check char"] = [
            gstin_check_char(s[:14]) if re.fullmatch(r"[0-9A-Z]{15}", s) else ""
            for s in invalid[C_CGSTIN].map(normalize_gstin)]
        slips = int(invalid["characters different"].between(1, 2).sum())
        rep.p(f"**{slips} of {len(invalid)}** invalid values are one or two characters away from a valid GSTIN "
              "typed on other claims, which looks like typing slips rather than bad bills. "
              "Confirm against the photos once they are imported.")
        rep.table(cols(invalid, C_ID, C_CVENDOR, C_CGSTIN, "problems", "nearest valid GSTIN in the data",
                       "characters different", "times the valid one appears", C_STATUS), 20)
    if C_CAT in has.columns:
        tr = df[df["split"] == "train"]
        rep.p("Clerk typed a GSTIN, by category (train):")
        rep.table(pd.crosstab(tr[C_CAT], np.where(tr[C_CGSTIN].map(is_blank), "no GSTIN", "GSTIN typed"))
                  .reset_index(), None)
        lodging = tr[tr[C_CAT].str.contains("lodg", case=False) & tr[C_CGSTIN].map(is_blank)]
        if len(lodging):
            rep.p("Lodging claims with no GSTIN typed (policy: hotel bills must be GST tax invoices):")
            rep.table(cols(lodging, C_ID, "employee", C_VENDOR, C_AMT, C_STATUS, C_NOTE), None)


SECTIONS = [sec_schema, sec_images, sec_parsers, sec_labels, sec_agreement,
            sec_duplicates, sec_parking, sec_image_hashes, sec_gstin]


class Context:
    pass


def build_context(data_dir: Path, cache_path: Path, review_path: Path) -> Context:
    ctx = Context()
    ctx.images_dir = data_dir / "images"
    ctx.train = read_csv(data_dir / "claims_train.csv")
    ctx.test = read_csv(data_dir / "claims_test.csv")
    ctx.employees = read_csv(data_dir / "employees.csv")
    # Test has no clerk columns; blank them rather than leave NaN so "" always means "nothing typed".
    claims = pd.concat([ctx.train.assign(split="train"), ctx.test.assign(split="test")], ignore_index=True).fillna("")
    image_index.cache_clear()
    ctx.claims = prepare(claims, ctx.employees, ctx.images_dir)
    ctx.files = list_images(ctx.images_dir)
    ctx.stats = image_stats(ctx.images_dir, cache_path)

    claimed = ctx.claims[ctx.claims["_img_rel"] != ""]
    by_file = claimed.groupby("_img_rel")
    ctx.claims_by_file = by_file[C_ID].agg(joined) if C_ID in claimed.columns else pd.Series(dtype=object)
    who_by_file = by_file["employee"].agg(joined)
    pairs = near_duplicates(ctx.stats)
    for side in ("a", "b"):
        pairs[f"claims_{side}"] = pairs[f"file_{side}"].map(ctx.claims_by_file).fillna("")
        pairs[f"who_{side}"] = pairs[f"file_{side}"].map(who_by_file).fillna("")
    pairs["same_employee"] = np.where((pairs["who_a"] == "") | (pairs["who_b"] == ""), "unclaimed",
                                      np.where(pairs["who_a"] == pairs["who_b"], "yes", "no"))
    bill_by_file = by_file[C_CBILL].agg(lambda v: alnum(v.iloc[0])) if C_CBILL in claimed.columns else pd.Series(dtype=object)
    ba, bb = (pairs[f"file_{s}"].map(bill_by_file).fillna("") for s in ("a", "b"))
    pairs["clerk bill nos"] = np.where((ba == "") | (bb == ""), "not typed", np.where(ba == bb, "same", "differ"))
    ctx.pairs = pairs
    ctx.review = None
    if review_path.is_file():
        ctx.review = pd.read_csv(review_path, dtype=str, keep_default_na=False)
    ctx.repeats = find_repeated_bills(ctx.claims[ctx.claims["split"] == "train"])
    return ctx


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", type=Path, default=REPO / "data")
    ap.add_argument("--out", type=Path, default=REPO / "reports" / "audit.md")
    ap.add_argument("--cache", type=Path, default=REPO / "cache" / "image_stats.csv")
    ap.add_argument("--review", type=Path, default=REPO / "reports" / "photo_review.csv",
                    help="photos reviewed by eye: file, finding, detail")
    args = ap.parse_args()

    data_dir = args.data_dir.resolve()
    missing = [f for f in REQUIRED_FILES if not (data_dir / f).is_file()]
    if missing:
        print(f"Cannot run the audit: missing in {data_dir}: {', '.join(missing)}", file=sys.stderr)
        return 2

    ctx = build_context(data_dir, args.cache.resolve(), args.review.resolve())
    rep = Report()
    rep.h(1, "Data audit")
    rep.p("Generated by `scripts/audit.py` from the raw CSVs and images. No LLM calls.")
    for section in SECTIONS:
        try:
            section(ctx, rep)
        except MissingColumns as exc:
            rep.p(f"**Skipped: column(s) not found: {exc}.** See section 0 for the real headers.")
            print(f"{section.__name__}: skipped, missing {exc}", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(rep.lines).strip() + "\n", encoding="utf-8")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
