"""Google Gemini via the google-genai SDK. The SDK is imported on first use, not at import."""
from __future__ import annotations

import re
import time

from .base import ModelResponse, ProviderError, RateLimited, Unavailable


def _retry_after(text: str) -> float | None:
    # Gemini puts google.rpc.RetryInfo in the error body: "retryDelay": "37s"
    m = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", text)
    return float(m.group(1)) if m else None


class GeminiModel:
    provider = "gemini"

    def __init__(self, api_key: str, model: str, rpm: float, thinking_level: str | None = "low") -> None:
        from google import genai  # lazy: a missing SDK must not break importing the package

        self.model, self.rpm, self.thinking_level = model, rpm, thinking_level
        self._key = api_key
        self._client = genai.Client(api_key=api_key)

    def _redact(self, text: str) -> str:
        return text.replace(self._key, "<GEMINI_API_KEY>")

    def generate(self, system: str, prompt: str, image_jpeg: bytes) -> ModelResponse:
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=system, temperature=0, response_mime_type="application/json")
        # Flash-Lite models do not think by default; for the others keep thinking short (it is billed as output).
        if self.thinking_level and "lite" not in self.model:
            config.thinking_config = types.ThinkingConfig(thinking_level=self.thinking_level)
        start = time.monotonic()
        try:
            r = self._client.models.generate_content(
                model=self.model, config=config,
                contents=[types.Part.from_bytes(data=image_jpeg, mime_type="image/jpeg"), prompt])
        except errors.APIError as exc:
            text = self._redact(str(exc))
            if exc.code == 429:
                raise RateLimited(text, _retry_after(text), daily=bool(re.search(r"PerDay|per day", text, re.I))) from None
            if exc.code is not None and exc.code >= 500:
                raise Unavailable(text) from None
            raise ProviderError(text) from None
        except Exception as exc:  # network errors and the like: treat as temporary
            raise Unavailable(self._redact(f"{type(exc).__name__}: {exc}")) from None
        u = r.usage_metadata
        return ModelResponse(
            text=r.text or "", provider=self.provider, model=self.model,
            input_tokens=(u.prompt_token_count or 0) if u else 0,
            output_tokens=(u.candidates_token_count or 0) if u else 0,
            thinking_tokens=(u.thoughts_token_count or 0) if u else 0,
            latency_ms=int((time.monotonic() - start) * 1000))
