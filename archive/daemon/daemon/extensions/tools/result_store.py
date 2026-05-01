"""Tool result store — persist large tool outputs and enforce budgets.

When a tool returns output exceeding a configurable character limit,
the full text is written to a cache file and a truncated preview is
returned to the LLM.  The LLM can then use the built-in
``file_read`` tool to read specific sections on demand.

This module generalises the original MCP-only result store to serve
**all** tool types.  The orchestrator calls :meth:`ResultStore.apply_budget`
after every tool execution to enforce per-tool size limits.

Lifecycle:
  - **Startup**: ``cleanup_on_startup()`` clears stale files from
    the previous daemon run.
  - **Write**: ``store()`` writes content with SHA-256 dedup and
    enforces a directory size cap via LRU eviction.
  - **Read**: Handled by the existing ``FileReadTool`` — no special
    retrieval API needed.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Defaults
DEFAULT_MAX_RESULT_CHARS = 50_000  # per-tool default (~12.5K tokens)
_DEFAULT_MAX_CACHE_SIZE = 100 * 1024 * 1024  # 100 MB
_PREVIEW_CHARS = 2000


def _generate_preview(content: str, max_chars: int = _PREVIEW_CHARS) -> tuple[str, bool]:
    """Generate a truncated preview, preferring newline boundaries.

    Mirrors Claude Code's ``generatePreview`` strategy: if a newline
    exists in the upper 50% of the limit, cut there for readability.

    Args:
        content: Full text to preview.
        max_chars: Maximum characters in the preview.

    Returns:
        Tuple of (preview_text, has_more).
    """
    if len(content) <= max_chars:
        return content, False

    truncated = content[:max_chars]
    last_nl = truncated.rfind("\n")

    # Use the newline if it's in the upper half of the window
    if last_nl > max_chars * 0.5:
        cut_point = last_nl
    else:
        cut_point = max_chars

    return content[:cut_point], True


class ResultStore:
    """Persist large tool outputs to disk and return previews.

    Shared by the orchestrator (for all tool types) and by MCP
    bridge (for MCP-specific pre-checks).

    Args:
        cache_dir: Directory for cached result files
            (e.g. ``~/.mustang/cache/tool_results/``).
        max_cache_size: Maximum total bytes in the cache directory.
            When exceeded, oldest files (by mtime) are evicted.
    """

    def __init__(
        self,
        cache_dir: Path,
        max_cache_size: int = _DEFAULT_MAX_CACHE_SIZE,
    ) -> None:
        self._cache_dir = cache_dir
        self._max_cache_size = max_cache_size

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def cleanup_on_startup(self) -> int:
        """Remove all cached result files from a previous daemon run.

        Called once during startup.  Leftover files from previous
        sessions may be unreferenceable.

        Returns:
            Number of files removed.
        """
        if not self._cache_dir.is_dir():
            return 0

        removed = 0
        for f in self._cache_dir.iterdir():
            if f.is_file():
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    logger.warning("Failed to remove stale cache file: %s", f)
        if removed:
            logger.info("Cleaned up %d stale tool result cache files", removed)
        return removed

    def apply_budget(
        self,
        tool_name: str,
        output: str,
        max_chars: int | None,
    ) -> str:
        """Enforce a character budget on a single tool result.

        If the output is within budget (or budget is ``None``), returns
        the original string unchanged.  Otherwise, persists the full
        output to disk and returns a preview message.

        Args:
            tool_name: Name of the tool (for logging / summary).
            output: Full tool output text.
            max_chars: Character limit.  ``None`` means no limit.

        Returns:
            Original output if within budget, else a preview summary.
        """
        if max_chars is None:
            return output
        if len(output) <= max_chars:
            return output

        logger.info(
            "Tool '%s' output exceeds budget (%d > %d chars) — persisting",
            tool_name,
            len(output),
            max_chars,
        )
        return self.store(output, tool_name=tool_name)

    def store(self, content: str, *, tool_name: str = "tool") -> str:
        """Persist *content* to disk and return a summary for the LLM.

        The file name is derived from a SHA-256 hash of the content,
        so identical outputs are deduplicated automatically.  After
        writing, the directory size cap is enforced via LRU eviction.

        Args:
            content: Full text output from a tool.
            tool_name: Tool name for the summary message.

        Returns:
            A summary string containing the file path, size, and a
            preview of the first ~2000 characters.
        """
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        content_bytes = content.encode()
        digest = hashlib.sha256(content_bytes).hexdigest()[:16]
        path = self._cache_dir / f"{digest}.txt"

        # Dedup: skip write if identical content already cached
        if not path.exists():
            path.write_bytes(content_bytes)
            self._evict_if_needed()

        size_kb = len(content_bytes) / 1024
        preview, has_more = _generate_preview(content)

        parts = [
            f"[Output too large ({size_kb:.0f} KB). Saved to {path}]",
            "",
            f"Preview (first {_PREVIEW_CHARS} chars):",
            preview,
        ]
        if has_more:
            parts.append("\n...")
        parts.append("")
        parts.append("Use file_read tool with offset/limit to read specific sections.")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _evict_if_needed(self) -> None:
        """Remove oldest files until directory size is under the cap."""
        if not self._cache_dir.is_dir():
            return

        files = sorted(self._cache_dir.iterdir(), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files if f.is_file())

        while total > self._max_cache_size and files:
            victim = files.pop(0)
            if not victim.is_file():
                continue
            try:
                size = victim.stat().st_size
                victim.unlink()
                total -= size
                logger.debug("Evicted tool result cache: %s (%d bytes)", victim, size)
            except OSError:
                logger.warning("Failed to evict cache file: %s", victim)
