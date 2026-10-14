"""What every model provider returns and raises. No SDK imports here."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ProviderError(Exception):
    """A call failed in a way that switching model will not fix by waiting (bad model, bad key...)."""


class NoProviderConfigured(ProviderError):
    """No API key for any provider. The service reports this politely instead of crashing."""


class RateLimited(ProviderError):
    """HTTP 429. `daily` is True when the per-day quota is spent, so retrying today is pointless."""

    def __init__(self, message: str, retry_after: float | None = None, daily: bool = False) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.daily = daily


class Unavailable(ProviderError):
    """HTTP 5xx / overloaded. Usually temporary."""


class AllModelsExhausted(ProviderError):
    """Every model in the chain is out of quota or broken for now; resume later."""


@dataclass
class ModelResponse:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    latency_ms: int = 0


class VisionModel(Protocol):
    provider: str
    model: str
    rpm: float  # requests per minute to stay under

    def generate(self, system: str, prompt: str, image_jpeg: bytes) -> ModelResponse:
        """One call: system instructions, a user prompt and one JPEG. Returns raw JSON text."""
        ...
