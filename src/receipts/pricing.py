"""Paid-tier Gemini prices and the exchange rate used for every rupee cost figure."""
from __future__ import annotations

# USD per 1M tokens, paid tier (Standard), from https://ai.google.dev/gemini-api/docs/pricing
# (page "Last updated 2026-10-14 UTC", read 2026-10-14). 3.8 Flash rises to $1.50 / $7.50 on 2027-01-01.
PRICES = {
    "gemini-3.8-flash": {"input": 0.75, "output": 3.75},
    "gemini-3.6-flash": {"input": 0.75, "output": 3.75},
    "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50},
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
}
PRICES_SOURCE = "https://ai.google.dev/gemini-api/docs/pricing (last updated 2026-10-14)"
USD_INR = 96.8  # rate on 10 Oct 2026, banklive.net/en/convert/usd-to-inr


def passes_cost_inr(passes: list[dict]) -> float:
    """Rupees for the model calls recorded in an extraction record; unknown models count as 0."""
    usd = 0.0
    for p in passes:
        price = PRICES.get(p.get("model", ""))
        if price:
            out = p.get("output_tokens", 0) + p.get("thinking_tokens", 0)
            usd += (p.get("input_tokens", 0) * price["input"] + out * price["output"]) / 1e6
    return round(usd * USD_INR, 4)
