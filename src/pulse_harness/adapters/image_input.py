"""PNG/JPEG inputs shared by workspace and model views.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The original file is kept and verified as it is. A model receives the original
when it is already small; otherwise it receives a scaled copy, and is told so.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath
from typing import Mapping

from PIL import Image, ImageOps, UnidentifiedImageError

# Originals: the same byte allowance as PDFs, and enough pixels for 48 MP phone
# photos and 6K screenshots. Pixels are checked before any decoding.
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000
# What one image may add to a model request. Larger originals are scaled to fit;
# providers downscale beyond this edge anyway, and five such images stay well
# inside the default request bound.
MODEL_IMAGE_EDGE = 2048
MODEL_IMAGE_BYTES = 1024 * 1024
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
        raise ImageInputError("image_too_large", "Choose a PNG or JPEG of at most 32 MiB.")
    try:
        with Image.open(BytesIO(data), formats=("PNG", "JPEG")) as image:
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise ImageInputError("image_pixels_exceeded", "Choose an image of at most 50 megapixels.")
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
        raise ImageInputError("image_pixels_exceeded", "Choose an image of at most 50 megapixels.") from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        if isinstance(error, ImageInputError):
            raise
        raise ImageInputError("image_unreadable", "This image could not be read. Choose a valid PNG or JPEG.") from error


@dataclass(frozen=True)
class ModelImage:
    """The bytes a model or preview receives for one verified original."""

    data: bytes
    mime: str
    width: int
    height: int
    scaled: bool

    def note(self, original: Mapping) -> str:
        if not self.scaled:
            return ""
        return (f" These pixels are a {self.width}x{self.height} {self.mime} copy scaled from the "
                f"{original['width']}x{original['height']} original; the file itself is unchanged.")


def model_image(data: bytes, metadata: Mapping) -> ModelImage:
    """The original when it fits a model request, otherwise a scaled copy.

    ``metadata`` comes from :func:`image_metadata` for the same bytes. The copy
    is upright (EXIF orientation applied), carries no file metadata, and is the
    same for the same original, so repeated requests send identical bytes.
    """
    width, height = metadata["width"], metadata["height"]
    if len(data) <= MODEL_IMAGE_BYTES and max(width, height) <= MODEL_IMAGE_EDGE:
        return ModelImage(data, metadata["mime"], width, height, False)
    box = (MODEL_IMAGE_EDGE, MODEL_IMAGE_EDGE)
    with Image.open(BytesIO(data), formats=("PNG", "JPEG")) as source:
        if source.format == "JPEG":
            source.thumbnail(box, Image.Resampling.LANCZOS)  # decodes at a reduced scale
            image = ImageOps.exif_transpose(source)
        else:
            # Palette and 1-bit images would otherwise resize by nearest pixel.
            transparent = source.mode in {"RGBA", "LA", "PA"} or "transparency" in source.info
            image = ImageOps.exif_transpose(source).convert("RGBA" if transparent else "RGB")
            image.thumbnail(box, Image.Resampling.LANCZOS)
    if metadata["mime"] == "image/png":
        encoded = _encode(image, "PNG")
        if len(encoded) <= MODEL_IMAGE_BYTES:
            return ModelImage(encoded, "image/png", image.width, image.height, True)
    if image.mode != "RGB":
        rgba = image.convert("RGBA")
        image = Image.new("RGB", rgba.size, (255, 255, 255))
        image.paste(rgba, mask=rgba.getchannel("A"))
    # Lower quality before lowering resolution; stop at a size any model reads.
    for edge in (MODEL_IMAGE_EDGE, 1536, 1024, 768):
        if max(image.size) > edge:
            image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        for quality in (85, 70, 55):
            encoded = _encode(image, "JPEG", quality=quality)
            if len(encoded) <= MODEL_IMAGE_BYTES:
                return ModelImage(encoded, "image/jpeg", image.width, image.height, True)
    return ModelImage(encoded, "image/jpeg", image.width, image.height, True)


def _encode(image: Image.Image, format: str, **options) -> bytes:
    output = BytesIO()
    image.save(output, format=format, **options)
    return output.getvalue()


def image_data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
