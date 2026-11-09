"""Bounded original PNG/JPEG inputs shared by workspace and model views.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import base64
from io import BytesIO
from pathlib import PurePath

from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 1024 * 1024
# A decoded RGBA raster is at most 32 MiB. Check before load(), independent
# of compressed bytes; decoding is sequential and no converted copy is kept.
MAX_IMAGE_PIXELS = 8 * 1024 * 1024
IMAGE_KIND = "pulse.workspace-image.v1"


class ImageInputError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def is_image_path(path: str | PurePath) -> bool:
    return PurePath(path).suffix.lower() in {".png", ".jpg", ".jpeg"}


def image_metadata(data: bytes) -> dict:
    """Verify one still image without changing its original bytes."""
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageInputError("image_too_large", "Choose a PNG or JPEG of at most 1 MiB.")
    try:
        with Image.open(BytesIO(data), formats=("PNG", "JPEG")) as image:
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise ImageInputError("image_pixels_exceeded", "Choose an image of at most 8 megapixels (8,388,608 pixels).")
            if getattr(image, "is_animated", False):
                raise ImageInputError("image_animated", "Choose a still PNG or JPEG image.")
            mime = "image/png" if image.format == "PNG" else "image/jpeg"
            image.verify()
        # verify() checks container integrity; load() also checks compressed
        # pixel data, in particular truncated JPEGs. The size was checked first.
        with Image.open(BytesIO(data), formats=("PNG", "JPEG")) as image:
            image.load()
        return {"mime": mime, "width": width, "height": height}
    except Image.DecompressionBombError as error:
        raise ImageInputError("image_pixels_exceeded", "Choose an image of at most 8 megapixels (8,388,608 pixels).") from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        if isinstance(error, ImageInputError):
            raise
        raise ImageInputError("image_unreadable", "This image could not be read. Choose a valid PNG or JPEG.") from error


def image_data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
