"""Call an ordered list of models, staying under each one's per-minute limit and moving down the
list on 429 / 5xx. Every attempt, good or bad, is appended to cache/usage.csv."""
from __future__ import annotations

import csv
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from .base import AllModelsExhausted, ModelResponse, ProviderError, RateLimited, Unavailable, VisionModel

USAGE_COLUMNS = ["ts_utc", "file", "sha256", "step", "provider", "model", "outcome",
                 "input_tokens", "output_tokens", "thinking_tokens", "latency_ms", "detail"]
DEFAULT_COOLDOWN_S = 60      # 429 without a retry delay
UNAVAILABLE_COOLDOWN_S = 30  # 503 overloaded


class UsageLog:
    def __init__(self, path: Path | None) -> None:
        self.path = Path(path) if path else None

    def write(self, **row) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        with self.path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=USAGE_COLUMNS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow({"ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), **row})


class ModelChain:
    def __init__(self, models: Sequence[VisionModel], usage: UsageLog | None = None,
                 max_wait_s: float = 900, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if not models:
            raise ValueError("empty model chain")
        self.models = list(models)
        self.usage = usage or UsageLog(None)
        self.max_wait_s = max_wait_s
        self._sleep, self._clock = sleep, clock
        self._last_call: dict[str, float] = {}
        self._cool_until: dict[str, float] = {}
        self._dead: dict[str, str] = {}  # model -> why it is out for this run

    @staticmethod
    def key(m: VisionModel) -> str:
        return f"{m.provider}:{m.model}"

    def describe(self) -> str:
        return " -> ".join(self.key(m) + (" (out)" if self.key(m) in self._dead else "") for m in self.models)

    def _throttle(self, m: VisionModel) -> None:
        gap = 60.0 / m.rpm if m.rpm > 0 else 0
        wait = self._last_call.get(self.key(m), -1e9) + gap - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last_call[self.key(m)] = self._clock()

    def generate(self, system: str, prompt: str, image_jpeg: bytes, *, log: dict | None = None) -> ModelResponse:
        """First model that answers. Raises AllModelsExhausted when none can, now or within max_wait_s."""
        log = log or {}
        waited = 0.0
        while True:
            alive = [m for m in self.models if self.key(m) not in self._dead]
            if not alive:
                raise AllModelsExhausted("; ".join(f"{k}: {why}" for k, why in self._dead.items()))
            now = self._clock()
            ready = [m for m in alive if self._cool_until.get(self.key(m), 0) <= now]
            if not ready:
                pause = min(self._cool_until[self.key(m)] for m in alive) - now
                if waited + pause > self.max_wait_s:
                    raise AllModelsExhausted(f"every model is rate-limited; waited {waited:.0f}s")
                self._sleep(pause)
                waited += pause
                continue
            m = ready[0]
            self._throttle(m)
            base = dict(log, provider=m.provider, model=m.model)
            try:
                r = m.generate(system, prompt, image_jpeg)
            except RateLimited as exc:
                self.usage.write(**base, outcome="rate_limited_day" if exc.daily else "rate_limited",
                                 detail=str(exc)[:200])
                if exc.daily:
                    self._dead[self.key(m)] = "daily quota spent"
                else:
                    self._cool_until[self.key(m)] = self._clock() + (exc.retry_after or DEFAULT_COOLDOWN_S)
                continue
            except Unavailable as exc:
                self.usage.write(**base, outcome="unavailable", detail=str(exc)[:200])
                self._cool_until[self.key(m)] = self._clock() + UNAVAILABLE_COOLDOWN_S
                continue
            except ProviderError as exc:
                self.usage.write(**base, outcome="error", detail=str(exc)[:200])
                self._dead[self.key(m)] = str(exc)[:120]
                continue
            self.usage.write(**base, outcome="ok", input_tokens=r.input_tokens, output_tokens=r.output_tokens,
                             thinking_tokens=r.thinking_tokens, latency_ms=r.latency_ms)
            return r
