"""Audit the raw claims data and write reports/audit.md. pandas only, no LLM calls.

Usage: python scripts/audit.py [--data-dir data] [--out reports/audit.md] [--cache cache/image_stats.csv]
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
PHASH_MAX_DISTANCE = 6
PARKING_NAMES = ["Rohan", "Pooja", "Harshal"]

# First-pass note themes; a note can match several. Unmatched notes are listed in the report.
NOTE_THEMES = {
    "amount mismatch": r"mismatch|does ?n[o']?t match|not match|differ|claimed (more|less|extra)|"
                       r"amount.*(more|less|higher|lower|wrong)|bill (is for|shows|amount)|inflat|overstat",
    "duplicate": r"duplicate|dupl\b|already (claimed|paid|submitted)|same bill|repeat|twice|resubmi",
    "no vehicle no": r"vehicle|veh\.? ?no|reg(istration)?\.? ?no|number plate",
    "not in employee name": r"\bname\b|someone else|other person|not (his|her|the employee)",
    "unreadable photo": r"unreadable|not readable|illegible|blur|unclear|not clear|can'?t read|cannot read|"
                        r"too dark|cropped|cut off|faded|torn|not visible",
    "over limit": r"limit|\bcap\b|exceed|above (the )?(limit|cap|rs)|max(imum)?\b|per day|ceiling",
    "wrong category": r"categor|wrong head|should be (under|claimed)|misclass|not (a )?(fuel|food|travel|parking|hotel)",
    "date problem": r"\bdate|old bill|\blate\b|stale|expired|future|days? (old|after)",
    "no proper bill": r"no bill|no receipt|missing bill|bill missing|handwritten|hand written|kaccha|"
                      r"not a bill|estimate|quotation",
    "gst / vendor": r"\bgst|gstin|vendor|\bshop\b",
    "not reimbursable": r"personal|alcohol|liquor|not allowed|not permitted|not reimbursable|cigarette",
    "approval": r"approval|approved by|pre-?approv|manager",
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
                "no submitted_on" if s is None
                else "closest on or before submitted_on" if any(c <= s for c in date_candidates(v))
                else "both after submitted_on, defaulted to day-first"
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


def note_themes(note) -> list[str]:
    return [t for t, pattern in NOTE_THEMES.items() if re.search(pattern, str(note), re.I)]


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
    all_notes["themes"] = all_notes[C_NOTE].map(lambda n: ", ".join(note_themes(n)) or "(unthemed)")
    rep.p("All raw notes:")
    rep.table(all_notes, None)

    rep.h(3, "Note themes")
    noted["_themes"] = noted[C_NOTE].map(note_themes)
    theme_rows = []
    for theme in NOTE_THEMES:
        g = noted[noted["_themes"].map(lambda t: theme in t)]
        if len(g):
            theme_rows.append({
                "theme": theme, "rows": len(g), "distinct notes": g[C_NOTE].nunique(),
                "statuses": joined(f"{n} {s}" for s, n in g[C_STATUS].value_counts().items()),
                "median Rs": g["_amt"].median(), "example": g[C_NOTE].value_counts().index[0]})
    rep.table(pd.DataFrame(theme_rows).sort_values("rows", ascending=False) if theme_rows else pd.DataFrame(), None)
    unthemed = noted[noted["_themes"].map(len) == 0]
    rep.p(f"Notes matching no theme: {len(unthemed)} rows, {unthemed[C_NOTE].nunique()} distinct. "
          "A note can match more than one theme; themes are keyword rules, not a model.")


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
        na, nb = alnum(a), alnum(b)
        if na == nb:
            return "same ignoring case/punctuation"
        if na in nb or nb in na or SequenceMatcher(None, na, nb).ratio() >= 0.8:
            return "similar"
        return "different"

    tr["vendor_match"] = [vendor_match(a, b) for a, b in zip(tr[C_VENDOR], tr[C_CVENDOR])]
    rep.table(tr["vendor_match"].value_counts().rename_axis("vendor agreement").reset_index(name="rows"))
    different = tr[tr["vendor_match"] == "different"]
    if len(different):
        rep.table(cols(different, C_ID, C_EMP, C_VENDOR, C_CVENDOR, C_STATUS, C_NOTE), 10)

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
    off = tr[tr["date_match"].isin(["different", "day and month swapped", "within 1 day"])]
    if len(off):
        rep.table(cols(off, C_ID, C_EMP, C_EXP, C_CDATE, "date_match", C_STATUS, C_NOTE), 10)


def _group_rows(g: pd.DataFrame) -> dict:
    return {"n": len(g), "claims": joined(g.get(C_ID, [])), "splits": joined(g["split"]),
            "employees": joined(g["employee"]), "dates": joined(g["_exp"]), "amounts": joined(g["_amt"]),
            "statuses": joined(g.get(C_STATUS, []))}


def sec_duplicates(ctx, rep: Report) -> None:
    rep.h(2, "5. Duplicate scan (train + test)")
    df = ctx.claims.copy()
    df["_vendor"] = [alnum(c if not is_blank(c) else v) for c, v in zip(raw(df, C_CVENDOR), raw(df, C_VENDOR))]

    rep.h(3, f"Same `{C_CBILL}` + vendor")
    if C_CBILL in df.columns:
        df["_bill"] = df[C_CBILL].map(lambda v: "" if is_blank(v) else alnum(v))
        has = df[df["_bill"] != ""]
        rep.p(f"Rows with a bill number: {len(has)} ({joined(f'{n} {s}' for s, n in has['split'].value_counts().items())}).")
        groups = [{"bill_no": g[C_CBILL].iloc[0], "vendor": g[C_VENDOR].iloc[0] if C_VENDOR in g else "", **_group_rows(g)}
                  for _, g in has.groupby(["_bill", "_vendor"]) if len(g) > 1]
        rep.p(f"Repeated bill number + vendor groups: **{len(groups)}**.")
        rep.table(pd.DataFrame(groups).sort_values("n", ascending=False) if groups else pd.DataFrame(), 25)
    else:
        rep.p(f"Skipped: no `{C_CBILL}` column.")

    rep.h(3, "Same image file referenced by more than one claim")
    has_img = df[df["_img_key"] != ""]
    groups = [{"image": key, **_group_rows(g)} for key, g in has_img.groupby("_img_key") if len(g) > 1]
    rep.p(f"Image files referenced more than once: **{len(groups)}**.")
    rep.table(pd.DataFrame(groups).sort_values("n", ascending=False) if groups else pd.DataFrame(), 25)

    rep.h(3, "Same vendor + date with near amounts")
    dated = df[(df["_vendor"] != "") & df["_exp"].notna() & df["_amt"].notna()]
    pairs = []
    for _, g in dated.groupby(["_vendor", "_exp"]):
        for a, b in combinations(g.to_dict("records"), 2):
            if abs(a["_amt"] - b["_amt"]) <= max(NEAR_AMOUNT_RS, 0.02 * max(a["_amt"], b["_amt"])):
                pairs.append({"vendor": a.get(C_VENDOR, ""), "date": a["_exp"],
                              "claim_a": a.get(C_ID), "claim_b": b.get(C_ID),
                              "amount_a": a["_amt"], "amount_b": b["_amt"],
                              "who_a": a["employee"], "who_b": b["employee"],
                              "same_employee": a.get(C_EMP) == b.get(C_EMP),
                              "splits": f"{a['split']}/{b['split']}",
                              "statuses": joined([a.get(C_STATUS), b.get(C_STATUS)])})
    pairs = pd.DataFrame(pairs)
    rep.p(f"Pairs within Rs {NEAR_AMOUNT_RS} or 2%: **{len(pairs)}**"
          + (f" ({int(pairs['same_employee'].sum())} by the same employee)." if len(pairs) else "."))
    rep.table(pairs, 25)

    rep.h(3, "Hotel Shubham")
    mask = raw(df, C_VENDOR).str.contains("shubham", case=False, regex=False) | \
        raw(df, C_CVENDOR).str.contains("shubham", case=False, regex=False)
    shubham = df[mask]
    rep.p(f"Claims naming a vendor containing \"Shubham\": **{len(shubham)}** "
          f"from {shubham[C_EMP].nunique() if C_EMP in shubham else '?'} employees.")
    if len(shubham):
        rep.table(shubham.groupby("employee").agg(
            claims=("_amt", "size"), rupees=("_amt", "sum"), dates=("_exp", joined),
            statuses=(C_STATUS, joined) if C_STATUS in shubham else ("split", joined)).reset_index(), None)
        rep.table(cols(shubham.sort_values("_exp", key=lambda s: s.astype(str)),
                       C_ID, "split", "employee", C_VENDOR, C_CVENDOR, C_EXP, C_AMT, C_CAMT, C_CBILL,
                       C_IMG, C_STATUS, C_NOTE), None)


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
                   "who_a", "who_b", "same_employee"), 40)

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
        f"Wider than tall after applying EXIF: **{int(landscape.sum())}** of {len(ok)} - "
        "likely sideways if the receipt is a portrait slip, but a landscape bill also lands here",
        f"Stored pixels wider than tall, before EXIF: {int((ok['width'] > ok['height']).sum())}",
    ])
    side = ok[landscape].copy()
    if len(side):
        side["ratio"] = (side["shown_w"] / side["shown_h"]).round(2)
        side["claims"] = side["file"].map(ctx.claims_by_file).fillna("")
        rep.table(side.sort_values("ratio", ascending=False)[
            ["file", "width", "height", "exif_orientation", "ratio", "claims"]], 20)


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
    if invalid.empty:
        return
    reasons = invalid["problems"].str.split(", ").explode().value_counts()
    rep.table(reasons.rename_axis("failed check").reset_index(name="rows"))
    invalid["expected check char"] = [
        gstin_check_char(s[:14]) if len(s) == 15 and re.fullmatch(r"[0-9A-Z]{15}", s) else ""
        for s in invalid[C_CGSTIN].map(normalize_gstin)]
    rep.table(cols(invalid, C_ID, "split", C_VENDOR, C_CVENDOR, C_CGSTIN, "problems", "expected check char", C_STATUS), 20)
    if C_STATUS in has.columns:
        rep.p("Status by GSTIN validity:")
        rep.table(pd.crosstab(np.where(has["problems"] == "", "valid", "invalid"), has[C_STATUS])
                  .rename_axis("gstin").reset_index())


SECTIONS = [sec_schema, sec_images, sec_parsers, sec_labels, sec_agreement,
            sec_duplicates, sec_parking, sec_image_hashes, sec_gstin]


class Context:
    pass


def build_context(data_dir: Path, cache_path: Path) -> Context:
    ctx = Context()
    ctx.images_dir = data_dir / "images"
    ctx.train = read_csv(data_dir / "claims_train.csv")
    ctx.test = read_csv(data_dir / "claims_test.csv")
    ctx.employees = read_csv(data_dir / "employees.csv")
    claims = pd.concat([ctx.train.assign(split="train"), ctx.test.assign(split="test")], ignore_index=True)
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
    ctx.pairs = pairs
    return ctx


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", type=Path, default=REPO / "data")
    ap.add_argument("--out", type=Path, default=REPO / "reports" / "audit.md")
    ap.add_argument("--cache", type=Path, default=REPO / "cache" / "image_stats.csv")
    args = ap.parse_args()

    data_dir = args.data_dir.resolve()
    missing = [f for f in REQUIRED_FILES if not (data_dir / f).is_file()]
    if missing:
        print(f"Cannot run the audit: missing in {data_dir}: {', '.join(missing)}", file=sys.stderr)
        return 2

    ctx = build_context(data_dir, args.cache.resolve())
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
