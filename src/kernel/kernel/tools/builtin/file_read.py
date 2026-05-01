"""FileRead — read files and cache (mtime, hash) for later edits.

Supports three file categories:

- **Text files** (default): UTF-8 read with line-range slicing.
- **Images** (PNG/JPEG/WebP/GIF): base64-encoded ``ImageContent`` so the
  multimodal LLM can "see" the image directly.
- **PDF documents**: each page rendered to a PNG image via PyMuPDF, then
  returned as ``ImageContent`` blocks.  Requires the optional ``pymupdf``
  package; gracefully errors when it is missing.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import AsyncGenerator
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from kernel.llm.types import ImageContent, TextContent
from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tools.context import ToolContext
from kernel.tools.tool import RiskContext, Tool
from kernel.tools.types import (
    FileDisplay,
    PermissionSuggestion,
    ToolCallProgress,
    ToolCallResult,
    ToolInputError,
)

logger = logging.getLogger(__name__)


_MAX_LINES_DEFAULT = 2000
_MAX_CHARS = 2_000_000

# Image extensions recognised by the tool.
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})

# MIME types keyed by lowercase extension.
_IMAGE_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

# Hard limits for PDF reading.
_PDF_MAX_PAGES_PER_REQUEST = 20
_PDF_AUTO_LIMIT = 10  # pages; above this the caller must specify `pages`
_PDF_DPI = 150

# Maximum raw image file size (bytes) we will base64-encode.
_IMAGE_MAX_BYTES = 20 * 1024 * 1024  # 20 MB


class FileReadTool(Tool[dict[str, Any], str]):
    """Read a file's contents, optionally restricted to a line range.

    Handles text files, images (PNG/JPEG/WebP/GIF), and PDF documents.
    Images and PDF pages are returned as ``ImageContent`` blocks so the
    LLM can process them visually.
    """

    name = "FileRead"
    description_key = "tools/file_read"
    description = "Read a file from the local filesystem."
    kind = ToolKind.read

    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or cwd-relative path.",
            },
            "start_line": {
                "type": "integer",
                "description": "1-indexed start line; defaults to 1.  Text files only.",
            },
            "limit": {
                "type": "integer",
                "description": "Number of lines to read; defaults to 2000.  Text files only.",
            },
            "pages": {
                "type": "string",
                "description": (
                    'Page range for PDF files (e.g. "1-5", "3", "10-20").  '
                    "Required for PDFs with more than 10 pages.  "
                    f"Max {_PDF_MAX_PAGES_PER_REQUEST} pages per request."
                ),
            },
        },
        "required": ["path"],
    }

    # -- Risk / permission ------------------------------------------------

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        """Reads are generally safe."""
        return PermissionSuggestion(
            risk="low",
            default_decision="allow",
            reason="file read within cwd is low-risk",
        )

    def prepare_permission_matcher(self, input: dict[str, Any]):  # noqa: ANN201
        path = str(input.get("path", ""))
        return lambda pattern: fnmatch(path, pattern)

    def is_destructive(self, _input: dict[str, Any]) -> bool:
        return False

    # -- Validation -------------------------------------------------------

    async def validate_input(self, input: dict[str, Any], ctx: RiskContext) -> None:
        raw = input.get("path")
        if not isinstance(raw, str) or not raw:
            raise ToolInputError("path must be a non-empty string")

    # -- Main dispatch ----------------------------------------------------

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        path = _resolve(Path(input["path"]), ctx.cwd)

        # Common pre-checks.
        if not path.exists():
            yield _error("file not found", path)
            return
        if not path.is_file():
            yield _error("not a regular file", path)
            return

        ext = path.suffix.lower()

        if ext in _IMAGE_EXTS:
            async for event in self._read_image(path):
                yield event
        elif ext == ".pdf":
            async for event in self._read_pdf(path, input.get("pages")):
                yield event
        else:
            async for event in self._read_text(path, input, ctx):
                yield event

    # -- Text reader (original logic) -------------------------------------

    async def _read_text(
        self,
        path: Path,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallResult, None]:
        """Read a text file with line-range slicing and state caching."""
        start_line = max(1, int(input.get("start_line") or 1))
        limit = int(input.get("limit") or _MAX_LINES_DEFAULT)

        try:
            raw_text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            yield _error(f"read failed: {exc}", path)
            return

        # Record full-file state for edit-verification before any slicing.
        ctx.file_state.record(path, raw_text)

        lines = raw_text.splitlines(keepends=True)
        end_line = min(len(lines), start_line - 1 + limit)
        sliced = "".join(lines[start_line - 1 : end_line])

        truncated = (len(lines) > end_line) or (len(sliced) > _MAX_CHARS)
        if len(sliced) > _MAX_CHARS:
            sliced = sliced[:_MAX_CHARS]

        header = f"// {path} (lines {start_line}\u2013{end_line} of {len(lines)})\n"
        body = header + sliced

        yield ToolCallResult(
            data={
                "path": str(path),
                "content": sliced,
                "start_line": start_line,
                "end_line": end_line,
                "total_lines": len(lines),
                "truncated": truncated,
            },
            llm_content=[TextBlock(type="text", text=body)],
            display=FileDisplay(path=str(path), content=sliced, truncated=truncated),
        )

    # -- Image reader -----------------------------------------------------

    async def _read_image(
        self,
        path: Path,
    ) -> AsyncGenerator[ToolCallResult, None]:
        """Read an image file and return it as a base64-encoded ImageContent."""
        ext = path.suffix.lower()
        mime = _IMAGE_MIME.get(ext, "image/png")

        try:
            raw = path.read_bytes()
        except OSError as exc:
            yield _error(f"read failed: {exc}", path)
            return

        if len(raw) > _IMAGE_MAX_BYTES:
            yield _error(
                f"image too large: {len(raw) / (1024 * 1024):.1f} MB "
                f"(limit {_IMAGE_MAX_BYTES // (1024 * 1024)} MB)",
                path,
            )
            return

        encoded = base64.b64encode(raw).decode("ascii")
        summary = f"[image: {path.name}, {len(raw) / 1024:.0f} KB]"

        yield ToolCallResult(
            data={"path": str(path), "type": "image", "size_bytes": len(raw)},
            llm_content=[
                ImageContent(media_type=mime, data_base64=encoded),  # type: ignore[arg-type, list-item]
            ],
            display=FileDisplay(path=str(path), content=summary),
        )

    # -- PDF reader -------------------------------------------------------

    async def _read_pdf(
        self,
        path: Path,
        pages_spec: str | None,
    ) -> AsyncGenerator[ToolCallResult, None]:
        """Render PDF pages to images via PyMuPDF.

        Args:
            path: Absolute path to the PDF file.
            pages_spec: Human-readable page range, e.g. ``"1-5"`` or ``"3"``.
                Required when the PDF exceeds ``_PDF_AUTO_LIMIT`` pages.

        Yields:
            A single ``ToolCallResult`` containing one ``ImageContent``
            per rendered page, preceded by a ``TextContent`` header.
        """
        try:
            import fitz  # type: ignore[import-not-found,import-untyped]  # PyMuPDF — optional
        except ImportError:
            yield _error(
                "PDF reading requires the 'pymupdf' package.  Install with: pip install pymupdf",
                path,
            )
            return

        try:
            doc = fitz.open(str(path))
        except Exception as exc:
            yield _error(f"failed to open PDF: {exc}", path)
            return

        total_pages = len(doc)
        if total_pages == 0:
            doc.close()
            yield _error("PDF has no pages", path)
            return

        # Resolve page range.
        try:
            page_indices = _parse_pages(pages_spec, total_pages)
        except ValueError as exc:
            doc.close()
            yield _error(str(exc), path)
            return

        # Render each page to PNG.
        llm_blocks: list[TextContent | ImageContent] = [
            TextContent(
                text=(
                    f"PDF: {path.name} — {total_pages} page(s), "
                    f"showing page(s) {_format_page_list(page_indices)}"
                ),
            ),
        ]

        for idx in page_indices:
            page = doc[idx]
            pix = page.get_pixmap(dpi=_PDF_DPI)
            png_bytes = pix.tobytes("png")
            encoded = base64.b64encode(png_bytes).decode("ascii")
            llm_blocks.append(
                ImageContent(media_type="image/png", data_base64=encoded),  # type: ignore[arg-type]
            )

        doc.close()

        summary = f"[PDF: {path.name}, {total_pages} page(s), rendered {len(page_indices)} page(s)]"

        yield ToolCallResult(
            data={
                "path": str(path),
                "type": "pdf",
                "total_pages": total_pages,
                "rendered_pages": [i + 1 for i in page_indices],
            },
            llm_content=llm_blocks,  # type: ignore[arg-type]
            display=FileDisplay(path=str(path), content=summary),
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _resolve(path: Path, cwd: Path) -> Path:
    """Return an absolute path, resolving relative to ``cwd`` if needed."""
    return path if path.is_absolute() else (cwd / path)


def _error(message: str, path: Path) -> ToolCallResult:
    """Build an error ``ToolCallResult`` for a failed read."""
    err = f"{message}: {path}"
    return ToolCallResult(
        data={"path": str(path), "error": err},
        llm_content=[TextBlock(type="text", text=err)],
        display=FileDisplay(path=str(path), content=err),
    )


def _parse_pages(spec: str | None, total: int) -> list[int]:
    """Parse a page-range string into 0-based page indices.

    Args:
        spec: ``None`` (auto) or a string like ``"1-5"``, ``"3"``,
            ``"1-3,7,10-12"``.
        total: Total number of pages in the document.

    Returns:
        Sorted, deduplicated list of 0-based page indices.

    Raises:
        ValueError: If the spec is malformed or exceeds limits.
    """
    if spec is None:
        if total > _PDF_AUTO_LIMIT:
            raise ValueError(
                f"PDF has {total} pages (>{_PDF_AUTO_LIMIT}).  "
                f'Specify a page range with the "pages" parameter '
                f'(e.g. "1-5").  Max {_PDF_MAX_PAGES_PER_REQUEST} pages per request.'
            )
        return list(range(total))

    indices: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        # Distinguish range ("2-5") from negative number ("-1").
        # A range has a digit before the dash; a bare "-1" starts with "-".
        dash_pos = part.find("-", 1)  # skip position 0 to avoid negative sign
        if dash_pos > 0:
            left, right = part[:dash_pos], part[dash_pos + 1 :]
            try:
                lo, hi = int(left), int(right)
            except ValueError:
                raise ValueError(f"invalid page range: {part!r}")
            if lo < 1 or hi < 1:
                raise ValueError(f"page numbers must be >= 1, got: {part!r}")
            if lo > hi:
                raise ValueError(f"invalid range (start > end): {part!r}")
            for i in range(lo, hi + 1):
                if 1 <= i <= total:
                    indices.add(i - 1)
        else:
            try:
                p = int(part)
            except ValueError:
                raise ValueError(f"invalid page number: {part!r}")
            if p < 1:
                raise ValueError(f"page numbers must be >= 1, got: {p}")
            if 1 <= p <= total:
                indices.add(p - 1)

    if not indices:
        raise ValueError(f"no valid pages in range {spec!r} (document has {total} pages)")

    result = sorted(indices)
    if len(result) > _PDF_MAX_PAGES_PER_REQUEST:
        raise ValueError(
            f"requested {len(result)} pages, max {_PDF_MAX_PAGES_PER_REQUEST} per request"
        )
    return result


def _format_page_list(indices: list[int]) -> str:
    """Format 0-based indices as a human-readable 1-based page list.

    Consecutive pages are collapsed into ranges:
    ``[0, 1, 2, 5, 7, 8]`` → ``"1-3, 6, 8-9"``.
    """
    if not indices:
        return ""
    pages = [i + 1 for i in indices]
    ranges: list[str] = []
    start = pages[0]
    prev = pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
        else:
            ranges.append(f"{start}" if start == prev else f"{start}-{prev}")
            start = prev = p
    ranges.append(f"{start}" if start == prev else f"{start}-{prev}")
    return ", ".join(ranges)


__all__ = ["FileReadTool"]
