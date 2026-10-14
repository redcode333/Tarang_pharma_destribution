"""reports/photo_review.csv is answers written by a person after looking at the photos.
The pipeline must never read it, or every orientation / readability / stamp number we
report would be measured against itself."""
from __future__ import annotations

import builtins
import io
import json
import pathlib
from pathlib import Path

from PIL import Image

from receipts.extract import ExtractionCache, extract
from receipts.providers import ModelChain, ModelResponse, UsageLog

REPO = Path(__file__).resolve().parents[1]
PIPELINE = sorted((REPO / "src" / "receipts").rglob("*.py")) + [REPO / "scripts" / "run_extract.py"]


def test_no_pipeline_source_mentions_photo_review():
    offenders = [str(p.relative_to(REPO)) for p in PIPELINE if "photo_review" in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_extract_does_not_open_photo_review(tmp_path, monkeypatch):
    opened = []
    real_open, real_path_open = builtins.open, pathlib.Path.open

    def spy_open(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)

    def spy_path_open(self, *a, **k):
        opened.append(str(self))
        return real_path_open(self, *a, **k)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(pathlib.Path, "open", spy_path_open)

    class Model:
        provider, model, rpm = "fake", "fake-1", 0

        def generate(self, system, prompt, image_jpeg):
            reply = {"is_receipt": True, "readable": True, "rotation_needed": 0, "vendor": "X",
                     "total_amount": 10, "confidence": {}}
            return ModelResponse(text=json.dumps(reply), provider="fake", model="fake-1")

    photo = tmp_path / "IMG_1.jpg"
    buf = io.BytesIO()
    Image.new("RGB", (50, 80), "white").save(buf, format="JPEG")
    photo.write_bytes(buf.getvalue())
    extract(photo, chain=ModelChain([Model()], UsageLog(tmp_path / "u.csv")), cache=ExtractionCache(tmp_path / "c"))
    assert opened, "the spy saw nothing, so it is not wired in"
    assert not [p for p in opened if "photo_review" in p]
