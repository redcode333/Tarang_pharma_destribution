"""Extract every claim photo with the model chain, test photos first, resuming from the cache.

Each photo is sent to a model once; results land in cache/extractions/<sha256>.json and every
call is logged to cache/usage.csv. When the free-tier quota runs out the run stops cleanly;
run it again (the next day) and it carries on where it stopped.

Usage:
  python scripts/run_extract.py                        # test, then train
  python scripts/run_extract.py --split train --limit 20
  python scripts/run_extract.py --claims-file reports/sample20_claims.txt
  python scripts/run_extract.py --dry-run              # count what is cached / still to do
  python scripts/run_extract.py --reprocess            # re-apply post-processing to the cache, no calls
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from receipts.extract import USAGE_PATH, ExtractionCache, extract, reprocess  # noqa: E402
from receipts.images import file_hash  # noqa: E402
from receipts.normalize import resolve_image  # noqa: E402
from receipts.providers import AllModelsExhausted, NoProviderConfigured, build_chain  # noqa: E402


def claims_in_order(data_dir: Path, split: str) -> pd.DataFrame:
    """Claims with their photo path, test before train (test is needed for predictions.csv)."""
    parts = []
    for name in (["test"] if split == "test" else ["train"] if split == "train" else ["test", "train"]):
        df = pd.read_csv(data_dir / f"claims_{name}.csv", dtype=str, keep_default_na=False)
        parts.append(df.assign(split=name))
    claims = pd.concat(parts, ignore_index=True)
    claims["path"] = [resolve_image(v, data_dir / "images") for v in claims["image_file"]]
    return claims


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--split", choices=["test", "train", "all"], default="all")
    ap.add_argument("--limit", type=int, default=None, help="at most N photos that need a model call")
    ap.add_argument("--claims-file", type=Path, help="only these claim_ids, one per line (# comments ok)")
    ap.add_argument("--refresh", action="store_true", help="ignore the cache and call the model again")
    ap.add_argument("--reprocess", action="store_true",
                    help="redo post-processing for every cached photo from its stored replies; no model calls")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--data-dir", type=Path, default=REPO / "data")
    args = ap.parse_args()

    if args.reprocess:
        cache = ExtractionCache()
        paths = sorted(cache.root.glob("*.json"))
        for p in paths:
            cache.put(reprocess(json.loads(p.read_text(encoding="utf-8"))))
        print(f"Reprocessed {len(paths)} cached extractions (prompt replies unchanged, no model calls).")
        return 0

    claims = claims_in_order(args.data_dir, args.split)
    if args.claims_file:
        wanted = [l.split("#")[0].strip() for l in args.claims_file.read_text().splitlines()]
        wanted = [w for w in wanted if w]
        claims = claims.set_index("claim_id").loc[wanted].reset_index()
    missing = claims[claims["path"].isna()]
    todo = claims[claims["path"].notna()].drop_duplicates("path")

    cache = ExtractionCache()
    hashes = {p: file_hash(p) for p in todo["path"]}
    cached = [p for p in todo["path"] if cache.get(hashes[p]) is not None]
    need = [p for p in todo["path"] if args.refresh or p not in set(cached)]
    print(f"Claims: {len(claims)} ({joined_counts(claims['split'])}); no photo on disk: {len(missing)}; "
          f"photos: {len(todo)}; cached: {len(cached)}; to extract: {len(need)}")
    if args.limit is not None:
        need = need[:args.limit]
    if args.dry_run or not need:
        return 0

    try:
        chain = build_chain(USAGE_PATH)
    except NoProviderConfigured as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 2
    print(f"Model chain: {chain.describe()}")
    split_of = dict(zip(todo["path"], todo["split"]))
    done = 0
    for i, path in enumerate(need, 1):
        try:
            rec = extract(path, chain=chain, cache=cache, refresh=args.refresh)
        except AllModelsExhausted as exc:
            print(f"\nStopped after {done} photos: {exc}\nRe-run later; cached photos are skipped.", file=sys.stderr)
            return 3
        done += 1
        r = rec["result"]
        print(f"[{i}/{len(need)}] {split_of[path]:5} {path.name:28} {rec['model']:24} "
              f"calls={len(rec['passes'])} rot={r.get('rotation_applied', '-')} "
              f"Rs={r.get('total_amount')} flags={','.join(r.get('flags', [])) or '-'}")
    print(f"\nExtracted {done} photos. Chain now: {chain.describe()}")
    return 0


def joined_counts(s: pd.Series) -> str:
    return ", ".join(f"{n} {k}" for k, n in s.value_counts().items())


if __name__ == "__main__":
    sys.exit(main())
