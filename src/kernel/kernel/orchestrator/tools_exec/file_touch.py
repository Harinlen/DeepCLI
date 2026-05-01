"""Skill discovery bridge for file-mutating tools."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kernel.orchestrator.types import OrchestratorDeps

logger = logging.getLogger(__name__)


class FileTouchMixin:
    """Notify SkillManager after tools mutate files."""

    _cwd: Path
    _deps: OrchestratorDeps

    async def _notify_file_touched(self, tool_name: str, tool_input: dict[str, Any]) -> None:
        """Notify SkillManager that file-mutating tools ran.

        Args:
            tool_name: Name of the mutating tool.
            tool_input: Effective tool input after authorization/hooks.

        Returns:
            ``None``.
        """
        skills = self._deps.skills
        if skills is None:
            return

        file_path = tool_input.get("file_path") or tool_input.get("path")
        if not file_path or not isinstance(file_path, str):
            return

        try:
            await skills.on_file_touched([file_path], str(self._cwd))
        except Exception:
            logger.debug("skills.on_file_touched failed - non-fatal", exc_info=True)
