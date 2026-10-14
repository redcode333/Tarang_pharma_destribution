"""Receipt photo handling: hashing, EXIF transpose, rotation, downscaled JPEG payloads."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image, ImageOps

LONG_SIDE = 1600
JPEG_QUALITY = 85


def file_hash(path: Path) -> str:
    """sha256 of the file bytes; the cache key, so a renamed copy is still a hit."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def phash(path: Path) -> str:
    """Perceptual hash of the photo as displayed (EXIF applied), hex. Same as scripts/audit.py."""
    import imagehash

    return str(imagehash.phash(load_image(path)))


def load_image(path: Path) -> Image.Image:
    """The photo as displayed: EXIF orientation applied, RGB."""
    with Image.open(path) as im:
        return ImageOps.exif_transpose(im).convert("RGB")


def rotate_clockwise(img: Image.Image, degrees: int) -> Image.Image:
    """Turn the image clockwise by 0, 90, 180 or 270 degrees."""
    if degrees % 360 == 0:
        return img
    if degrees % 90:
        raise ValueError(f"rotation must be a multiple of 90, got {degrees}")
    return img.rotate(-degrees, expand=True)  # PIL rotates counter-clockwise


def to_jpeg(img: Image.Image, long_side: int = LONG_SIDE, quality: int = JPEG_QUALITY) -> bytes:
    """JPEG bytes with the long side at most `long_side` pixels (never upscaled)."""
    img = img.copy()
    img.thumbnail((long_side, long_side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()
