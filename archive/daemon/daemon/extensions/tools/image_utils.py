"""Image MIME detection, base64 encoding, and token-budgeted resize.

Keeps Mustang's image pipeline small: magic-byte sniffing runs
without any third-party dependency, while Pillow is used for the
downsample path (required to get ``(w, h)`` reliably across
formats).  The caller receives a fully-formed :class:`ImageContent`
with base64 + SHA-256 so the image-cache layer can store it
on disk and replace the in-memory payload before JSONL persistence.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
from pathlib import Path
from typing import Literal

from PIL import Image

from daemon.providers.base import ImageContent

logger = logging.getLogger(__name__)


MimeType = Literal["image/png", "image/jpeg", "image/webp", "image/gif"]
"""MIME types accepted by both Mustang and the major multimodal LLMs."""

# Token-cost heuristic: Claude/GPT price an image roughly at
# ``(width * height) / 750`` tokens.  The default budget keeps one
# image under ~1500 tokens (≈ 1060×1060 pixels).
_DEFAULT_TOKEN_BUDGET = 1500
_TOKENS_PER_PIXEL = 1 / 750

# Hard safety cap — refuse to inline images larger than this even
# when the caller skips downsample.
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


def detect_mime(path: Path) -> MimeType | None:
    """Detect image MIME type via magic bytes (no Pillow).

    Returns one of the supported MIME strings, or ``None`` when the
    file is not a recognised image format.  Reads only the first
    16 bytes so this is safe to call even on large files.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return None

    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "image/gif"
    return None


def estimate_image_tokens(width: int, height: int) -> int:
    """Approximate LLM token cost of an image using ``(w*h)/750``.

    Matches the heuristic both Anthropic and OpenAI publish in their
    vision-pricing docs.  Used to decide whether to downsample.
    """
    return max(1, int(width * height * _TOKENS_PER_PIXEL))


def _resize_to_budget(img: Image.Image, max_tokens: int) -> Image.Image:
    """Return *img* (possibly resized) so its token cost fits the budget."""
    current = estimate_image_tokens(img.width, img.height)
    if current <= max_tokens:
        return img

    # Scale factor on each side is sqrt(budget / current).
    scale = (max_tokens / current) ** 0.5
    new_w = max(1, int(img.width * scale))
    new_h = max(1, int(img.height * scale))
    logger.info(
        "Downsampling image %dx%d (~%d tokens) → %dx%d for budget %d",
        img.width,
        img.height,
        current,
        new_w,
        new_h,
        max_tokens,
    )
    return img.resize((new_w, new_h), Image.Resampling.LANCZOS)


def read_image_as_base64(
    path: Path,
    max_tokens: int = _DEFAULT_TOKEN_BUDGET,
    source_path: str | None = None,
) -> ImageContent:
    """Load *path* and return an :class:`ImageContent` for the LLM.

    Flow:
      1. Magic-byte detect MIME.
      2. Open with Pillow; resize if estimated token cost exceeds
         ``max_tokens``.
      3. Re-encode in a format the LLM accepts
         (PNG → PNG, everything else → JPEG for size).
      4. Base64-encode + SHA-256 for cache keying.

    Args:
        path: Absolute path to an image file.
        max_tokens: Target token budget (default ≈ 1500, ~1060² px).
        source_path: Override for :attr:`ImageContent.source_path`.

    Raises:
        ValueError: When *path* is not a recognised image type or is
            larger than the 10 MB safety cap.
        OSError: If reading the file fails.
    """
    mime = detect_mime(path)
    if mime is None:
        raise ValueError(f"unsupported image format: {path}")

    size = path.stat().st_size
    if size > _MAX_BYTES:
        raise ValueError(f"image too large ({size / 1024 / 1024:.1f} MB, cap 10 MB): {path}")

    with Image.open(path) as raw:
        # Pillow is lazy; force decode so GIF/WEBP give us sensible (w,h).
        raw.load()
        # Drop alpha for JPEG fallback but keep it for PNG.
        working = (
            raw.convert("RGB")
            if mime == "image/jpeg"
            else raw.convert("RGBA" if mime == "image/png" else "RGB")
        )
        working = _resize_to_budget(working, max_tokens)

        buf = io.BytesIO()
        # Animated GIFs collapse to the first frame — acceptable: vision
        # models all ignore frame 2+ anyway.
        out_format: str
        out_mime: MimeType
        if mime == "image/png":
            out_format = "PNG"
            out_mime = "image/png"
        elif mime == "image/gif":
            out_format = "GIF"
            out_mime = "image/gif"
        elif mime == "image/webp":
            out_format = "WEBP"
            out_mime = "image/webp"
        else:
            out_format = "JPEG"
            out_mime = "image/jpeg"
        working.save(buf, format=out_format)
        raw_bytes = buf.getvalue()

    sha = hashlib.sha256(raw_bytes).hexdigest()
    data_b64 = base64.b64encode(raw_bytes).decode("ascii")
    return ImageContent(
        media_type=out_mime,
        data_base64=data_b64,
        source_sha256=sha,
        source_path=source_path or str(path),
    )


def extension_for_mime(mime: str) -> str:
    """Return a file extension for a supported image MIME string."""
    return {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
    }.get(mime, "bin")


__all__ = [
    "MimeType",
    "detect_mime",
    "estimate_image_tokens",
    "extension_for_mime",
    "read_image_as_base64",
]
