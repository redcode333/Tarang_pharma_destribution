"""Model providers. `build_chain()` reads the keys from the environment / .env.

GEMINI_API_KEY  primary. Models and per-minute limits from GEMINI_MODELS, default below.
GROQ_API_KEY    backup, after every Gemini model. Model from GROQ_MODEL.
Neither set     NoProviderConfigured.
"""
from __future__ import annotations

import os
from pathlib import Path

from .base import (AllModelsExhausted, ModelResponse, NoProviderConfigured, ProviderError,
                   RateLimited, Unavailable, VisionModel)
from .chain import ModelChain, UsageLog

# "model:requests-per-minute", first is primary. Flash-Lite leads because 3.8 Flash answered 503
# "high demand" all day on 2026-10-14 and Flash-Lite read the 20-claim sample well. The free
# tier's RPM is not published per model (AI Studio shows it per key); these are conservative.
# 3.1 Flash-Lite is last: it has its own free daily quota, so live uploads still work when the
# first two are out (2026-10-15).
DEFAULT_GEMINI_MODELS = "gemini-3.5-flash-lite:10,gemini-3.8-flash:5,gemini-3.1-flash-lite:10"
REPO_ROOT = Path(__file__).resolve().parents[3]

__all__ = ["AllModelsExhausted", "ModelChain", "ModelResponse", "NoProviderConfigured", "ProviderError",
           "RateLimited", "Unavailable", "UsageLog", "VisionModel", "build_chain"]


def _parse_models(spec: str) -> list[tuple[str, float]]:
    out = []
    for item in filter(None, (s.strip() for s in spec.split(","))):
        name, _, rpm = item.partition(":")
        out.append((name.strip(), float(rpm) if rpm else 5.0))
    return out


def build_chain(usage_path: Path | None = None, load_env: bool = True) -> ModelChain:
    if load_env:
        try:
            from dotenv import load_dotenv
            load_dotenv(REPO_ROOT / ".env")
        except ImportError:
            pass
    models: list[VisionModel] = []
    if key := os.environ.get("GEMINI_API_KEY", "").strip():
        from .gemini import GeminiModel
        thinking = os.environ.get("GEMINI_THINKING", "low").strip() or None
        for name, rpm in _parse_models(os.environ.get("GEMINI_MODELS", DEFAULT_GEMINI_MODELS)):
            models.append(GeminiModel(key, name, rpm, thinking))
    if key := os.environ.get("GROQ_API_KEY", "").strip():
        from .groq import DEFAULT_MODEL, GroqModel
        models.append(GroqModel(key, os.environ.get("GROQ_MODEL", DEFAULT_MODEL).strip(),
                                float(os.environ.get("GROQ_RPM", "15"))))
    if not models:
        raise NoProviderConfigured(
            "No model provider is configured. Put GEMINI_API_KEY (free at https://aistudio.google.com/apikey) "
            "or GROQ_API_KEY in .env. Photos already in cache/extractions/ still work without a key.")
    return ModelChain(models, UsageLog(usage_path))
