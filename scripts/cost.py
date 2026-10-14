"""Rupees per photo at Gemini's PAID-tier prices, from the tokens measured in cache/extractions/.

Development runs on the free tier; production would not, so the memo prices the measured
tokens at the paid Standard rates. Thinking tokens are billed as output.

Usage: python scripts/cost.py --usd-inr <rupees per dollar, e.g. the day's RBI reference rate>
The exchange rate is an input on purpose: state the rate and its date wherever the number is quoted.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]

# USD per 1M tokens, paid tier (Standard), from https://ai.google.dev/gemini-api/docs/pricing
# (page "Last updated 2026-10-14 UTC", read 2026-10-14). 3.8 Flash rises to $1.50 / $7.50 on 2027-01-01.
PRICES = {
    "gemini-3.8-flash": {"input": 0.75, "output": 3.75},
    "gemini-3.6-flash": {"input": 0.75, "output": 3.75},
    "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50},
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
}
PRICES_SOURCE = "https://ai.google.dev/gemini-api/docs/pricing (last updated 2026-10-14)"


def photo_tokens(records: list[dict]) -> pd.DataFrame:
    """One row per photo: every call it took (retries included), summed."""
    rows = []
    for rec in records:
        passes = rec.get("passes", [])
        rows.append({"file": rec["file"], "model": rec.get("model", ""), "calls": len(passes),
                     "input": sum(p.get("input_tokens", 0) for p in passes),
                     "output": sum(p.get("output_tokens", 0) + p.get("thinking_tokens", 0) for p in passes)})
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--usd-inr", type=float, default=None,
                    help="rupees per US dollar; quote its date. Without it, prices are shown in USD only")
    ap.add_argument("--cache-dir", type=Path, default=REPO / "cache" / "extractions")
    args = ap.parse_args()

    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(args.cache_dir.glob("*.json"))]
    if not records:
        print("No cached extractions yet.")
        return 1
    df = photo_tokens(records)
    print(f"Photos: {len(df)}; calls per photo: mean {df['calls'].mean():.2f}; "
          f"tokens per photo: input {df['input'].mean():.0f}, output incl. thinking {df['output'].mean():.0f}")
    print(f"Models used: {', '.join(f'{m} x{n}' for m, n in df['model'].value_counts().items())}")
    fx = args.usd_inr
    print(f"\nPrice per photo at paid-tier rates ({PRICES_SOURCE})"
          + (f", USD 1 = Rs {fx}:" if fx else "; pass --usd-inr for rupees:"))
    for model, p in PRICES.items():
        usd = (df["input"].mean() * p["input"] + df["output"].mean() * p["output"]) / 1e6
        rupees = f"  = Rs {usd * fx:.4f} per photo (Rs {usd * fx * 2400:,.0f} for 2,400 a month)" if fx else ""
        print(f"  {model:24} ${usd:.6f} per photo (${usd * 2400:,.2f} for 2,400 a month){rupees}")
    print("\nToken counts are the measured mean per photo across all cached extractions, whichever model "
          "produced them; image input tokens are close across Gemini models, thinking tokens are not.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
