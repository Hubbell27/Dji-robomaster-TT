"""Server-side normalization of uploaded insurance card photos.

Every upload is decoded and re-encoded as a fresh JPEG. That rejects
non-images and polyglot files, and strips all metadata (EXIF GPS, device ids)
before anything is stored.
"""

from __future__ import annotations

import io

from PIL import Image, ImageOps, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = 50_000_000  # decompression-bomb guard
_ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
_MAX_EDGE = 2400


class InvalidImage(ValueError):
    pass


def normalize_card_photo(data: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format not in _ALLOWED_FORMATS:
                raise InvalidImage("unsupported image type; please upload a JPEG or PNG photo")
            img.load()
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            img.thumbnail((_MAX_EDGE, _MAX_EDGE))
            if min(img.size) < 200:
                raise InvalidImage("image is too small to read; please retake the photo")
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=85, optimize=True)
            return out.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as e:
        raise InvalidImage("could not read image; please upload a JPEG or PNG photo") from e
