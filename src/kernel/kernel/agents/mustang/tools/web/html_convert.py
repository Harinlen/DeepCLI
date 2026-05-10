"""HTML to Markdown conversion with security sanitisation.

Uses ``html2text`` for conversion, then strips base64 images and
inline scripts that may have leaked through.
"""

from __future__ import annotations

import re

import html2text

# Matches base64 data URIs (images, fonts, etc.)
_BASE64_RE = re.compile(
    r"!\[[^\]]*\]\(data:[^)]+\)",  # Markdown image with data: URI
    re.IGNORECASE,
)

# Fallback: raw data: URLs that aren't wrapped in markdown image syntax
_RAW_DATA_URI_RE = re.compile(
    r"data:(?:image|font|application)/[^\s\"')]+",
    re.IGNORECASE,
)

# Inline <script> blocks that may survive html2text
_SCRIPT_RE = re.compile(
    r"<script\b[^>]*>.*?</script>",
    re.IGNORECASE | re.DOTALL,
)

# Converter singleton (thread-safe — html2text is stateless per call)
_converter: html2text.HTML2Text | None = None


def _get_converter() -> html2text.HTML2Text:
    global _converter  # noqa: PLW0603
    if _converter is None:
        h = html2text.HTML2Text()
        h.body_width = 0  # Don't wrap lines
        h.ignore_images = False  # Preserve alt text
        h.ignore_links = False
        h.ignore_emphasis = False
        h.single_line_break = True
        h.protect_links = True
        h.unicode_snob = True
        _converter = h
    return _converter


def html_to_markdown(html: str, max_chars: int = 50_000) -> str:
    """Convert *html* to Markdown, sanitise, and truncate.

    1. Strip ``<script>`` tags before conversion.
    2. Convert with ``html2text``.
    3. Remove base64 data URIs (image exfiltration / token waste).
    4. Truncate to *max_chars*.
    """
    # Pre-strip scripts
    cleaned = _SCRIPT_RE.sub("", html)

    # Convert
    md = _get_converter().handle(cleaned)

    # Post-strip base64
    md = _BASE64_RE.sub("[image removed]", md)
    md = _RAW_DATA_URI_RE.sub("[data-uri removed]", md)

    # Truncate
    if len(md) > max_chars:
        md = md[:max_chars]

    return md


__all__ = ["html_to_markdown"]
