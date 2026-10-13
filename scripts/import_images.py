"""Move receipt photos downloaded from the task portal into data/images/.

The browser saves each photo as "<uuid>-<original name>", for example
"95627cb0-361c-4599-93f9-79473d6d1a35-IMG-20260610-WA0001.jpg". This strips the
UUID, skips repeats, "(1)" copies and .crdownload partials, then checks the
result against the image_file column of the claims CSVs.

Usage: python scripts/import_images.py [--downloads DIR] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from receipts.normalize import image_key, is_blank  # noqa: E402

EXPECTED_COUNT = 467
COMMIT_SIZE_LIMIT_MB = 100

_UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
# Two original styles: IMG-YYYYMMDD-WAnnnn (WhatsApp) and IMG_YYYYMMDD_HHMMSS (camera).
_NAME = r"IMG[-_]\d{8}[-_](?:WA\d{4}|\d{6})"
PHOTO = re.compile(rf"^{_UUID}-({_NAME}\.jpe?g)$", re.I)
COPY = re.compile(rf"^{_UUID}-{_NAME}.*\(\d+\)\.jpe?g$", re.I)
PARTIAL = re.compile(rf"{_NAME}.*\.crdownload$", re.I)


def default_downloads() -> Path:
    """The user's Downloads folder; on Windows it can be redirected, so ask the registry first."""
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                value, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C925E467CC}")
            if value:
                return Path(os.path.expandvars(value))
        except OSError:
            pass
    return Path.home() / "Downloads"


def same_bytes(a: Path, b: Path) -> bool:
    return a.stat().st_size == b.stat().st_size and a.read_bytes() == b.read_bytes()


def show(title: str, items: list[str], limit: int = 25) -> None:
    print(f"{title}: {len(items)}")
    for item in items[:limit]:
        print(f"    {item}")
    if len(items) > limit:
        print(f"    ... {len(items) - limit} more")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--downloads", type=Path, default=None, help="folder the browser downloaded into")
    ap.add_argument("--images-dir", type=Path, default=REPO / "data" / "images")
    ap.add_argument("--data-dir", type=Path, default=REPO / "data")
    ap.add_argument("--dry-run", action="store_true", help="report what would move, move nothing")
    args = ap.parse_args()

    downloads = (args.downloads or default_downloads()).resolve()
    if not downloads.is_dir():
        print(f"Downloads folder not found: {downloads}. Pass --downloads DIR.", file=sys.stderr)
        return 2
    images_dir = args.images_dir.resolve()
    images_dir.mkdir(parents=True, exist_ok=True)

    have = {image_key(p.name): p for p in images_dir.iterdir() if p.is_file()}
    moved, repeats, copies, partials = [], [], [], []
    for src in sorted(p for p in downloads.iterdir() if p.is_file()):
        if PARTIAL.search(src.name):
            partials.append(src.name)
        elif COPY.match(src.name):
            copies.append(src.name)
        elif m := PHOTO.match(src.name):
            key = image_key(m.group(1))
            if key in have:
                note = "identical to kept copy" if same_bytes(src, have[key]) else "DIFFERENT content from kept copy"
                repeats.append(f"{src.name}  ({note})")
                continue
            dest = images_dir / m.group(1)
            if not args.dry_run:
                shutil.move(str(src), str(dest))
            have[key] = src if args.dry_run else dest
            moved.append(m.group(1))

    verb = "would move" if args.dry_run else "moved"
    print(f"Downloads folder: {downloads}")
    print(f"Files {verb}: {len(moved)}")
    show("Repeats of a name already imported (left in Downloads)", repeats)
    show('"(n)" browser copies ignored (left in Downloads)', copies)
    show(".crdownload partials ignored (left in Downloads)", partials)

    on_disk = sorted(p for p in images_dir.iterdir() if p.is_file())
    count = len(have) if args.dry_run else len(on_disk)
    status = "OK" if count == EXPECTED_COUNT else f"MISMATCH, {EXPECTED_COUNT - count:+d} to go"
    print(f"\nFinal count in {images_dir.name}/: {count} (expected {EXPECTED_COUNT}) -> {status}")

    referenced: dict[str, str] = {}
    for name in ("claims_train.csv", "claims_test.csv"):
        path = args.data_dir / name
        if not path.is_file():
            print(f"{name}: not found in {args.data_dir}, skipping the cross-check")
            continue
        column = pd.read_csv(path, dtype=str, keep_default_na=False)["image_file"]
        blank = int(column.map(is_blank).sum())
        print(f"{name}: {len(column)} claims, {blank} with a blank image_file")
        for value in column[~column.map(is_blank)]:
            referenced.setdefault(image_key(value), re.split(r"[\\/]", value.strip())[-1])
    if referenced:
        missing = sorted(base for key, base in referenced.items() if key not in have)
        unused = sorted(p.name for key, p in have.items() if key not in referenced)
        show(f"\nDistinct image_file names in the claims: {len(referenced)}. With no matching file", missing)
        show("Images on disk that no claim references", unused)

    size_mb = sum(p.stat().st_size for p in on_disk) / 1e6
    advice = "small enough to commit" if size_mb < COMMIT_SIZE_LIMIT_MB else "too large to commit without asking"
    print(f"\nTotal size of {images_dir.name}/: {size_mb:.1f} MB ({advice}; limit ~{COMMIT_SIZE_LIMIT_MB} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
