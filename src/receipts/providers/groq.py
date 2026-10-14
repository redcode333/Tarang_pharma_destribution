"""Groq's OpenAI-compatible chat API with a vision model, over plain httpx (no extra SDK)."""
from __future__ import annotations

import base64
import time

from .base import ModelResponse, ProviderError, RateLimited, Unavailable

URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"


class GroqModel:
    provider = "groq"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, rpm: float = 15) -> None:
        self.model, self.rpm = model, rpm
        self._key = api_key

    def generate(self, system: str, prompt: str, image_jpeg: bytes) -> ModelResponse:
        import httpx

        data_url = "data:image/jpeg;base64," + base64.b64encode(image_jpeg).decode()
        body = {
            "model": self.model, "temperature": 0, "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": [{"type": "text", "text": prompt},
                                             {"type": "image_url", "image_url": {"url": data_url}}]},
            ],
        }
        start = time.monotonic()
        try:
            r = httpx.post(URL, json=body, timeout=90, headers={"Authorization": f"Bearer {self._key}"})
        except httpx.HTTPError as exc:
            raise Unavailable(f"{type(exc).__name__}: {exc}") from None
        text = r.text.replace(self._key, "<GROQ_API_KEY>")[:500]
        if r.status_code == 429:
            after = r.headers.get("retry-after")
            # Groq names the exhausted limit: "requests per day (RPD)" / "tokens per day (TPD)".
            raise RateLimited(text, float(after) if after else None, daily="per day" in text.lower())
        if r.status_code >= 500:
            raise Unavailable(text)
        if r.status_code != 200:
            raise ProviderError(text)
        j = r.json()
        u = j.get("usage") or {}
        return ModelResponse(
            text=j["choices"][0]["message"]["content"] or "", provider=self.provider, model=self.model,
            input_tokens=u.get("prompt_tokens", 0), output_tokens=u.get("completion_tokens", 0),
            latency_ms=int((time.monotonic() - start) * 1000))
