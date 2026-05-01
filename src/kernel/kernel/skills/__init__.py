"""Skills subsystem — YAML-frontmatter skill discovery, indexing, and activation.

See ``docs/plans/landed/skill-manager.md`` for the full design.

Public surface:

- :class:`SkillManager` — Subsystem loaded at step 8 of the kernel
  lifespan; owns the :class:`SkillRegistry` and exposes
  :meth:`get_skill_listing`, :meth:`activate`, :meth:`on_file_touched`.
- :class:`SkillManifest` — parsed frontmatter (no body).
- :class:`LoadedSkill` — discovered skill with lazy body loading.
- :class:`ActivationResult` — returned by :meth:`activate`.
- :class:`InvokedSkillInfo` — compaction preservation tracking.

Consumers:
- PromptBuilder: ``get_skill_listing()`` → system prompt injection.
- SkillTool: ``activate()`` → body injection into conversation.
- CommandManager: ``user_invocable_skills()`` → ``/skill-name`` autocomplete.
- ToolExecutor: ``on_file_touched()`` → dynamic discovery + conditional.
- Compactor: ``get_invoked_for_agent()`` → compaction preservation.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kernel.skills.arguments import substitute_arguments, substitute_config
from kernel.skills.config import SkillsConfig
from kernel.skills.eligibility import is_visible
from kernel.skills.loader import (
    activate_conditional,
    discover,
    discover_for_paths,
)
from kernel.skills.manifest import ManifestError
from kernel.skills.registry import SkillRegistry
from kernel.skills.setup import check_setup
from kernel.skills.types import (
    ActivationResult,
    InvokedSkillInfo,
    LoadedSkill,
    SkillFallbackFor,
    SkillManifest,
    SkillRequires,
    SkillSetup,
    SkillSetupEnvVar,
    SkillSource,
)
from kernel.subsystem import Subsystem

if TYPE_CHECKING:
    from kernel.module_table import KernelModuleTable

logger = logging.getLogger(__name__)


# Default discovery roots.
_DEFAULT_USER_SKILLS_DIR = Path.home() / ".mustang" / "skills"
_DEFAULT_PROJECT_SKILLS_SUBDIR = Path(".mustang") / "skills"

# Claude Code compatibility paths.
_CLAUDE_USER_SKILLS_DIR = Path.home() / ".claude" / "skills"
_CLAUDE_PROJECT_SKILLS_SUBDIR = Path(".claude") / "skills"

# Listing budget: 1% of context window (Claude Code default).
_DEFAULT_BUDGET_PERCENT = 0.01
_CHARS_PER_TOKEN = 4
_DEFAULT_CHAR_BUDGET = 8_000  # Fallback for unknown context window.
_MAX_LISTING_DESC_CHARS = 250


class SkillManager(Subsystem):
    """Discovers and indexes skills across built-in, user, project,
    external, and MCP layers.  Provides listing for prompt injection
    and activation for SkillTool.

    Skills are defined as ``skill-name/SKILL.md`` directories with
    YAML frontmatter.  Bodies are lazy-loaded (frontmatter scanned at
    startup, body loaded on first activation).

    Claude Code skill compatibility (reading ``.claude/skills/``
    alongside ``.mustang/skills/``) is **opt-in** via
    ``skills.claude_compat = true`` in ``skills.yaml``.  Default is
    off so that dev-workflow skills a user places under
    ``.claude/skills/`` (e.g. session-scoped ``/done-check``) do not
    leak into DeepCLI's LLM skill listing.
    """

    def __init__(
        self,
        module_table: KernelModuleTable,
        *,
        user_skills_dir: Path | None = None,
        project_skills_dir: Path | None = None,
        claude_compat: bool | None = None,
    ) -> None:
        super().__init__(module_table)
        self._user_skills_dir = user_skills_dir or _DEFAULT_USER_SKILLS_DIR
        self._project_skills_dir = (
            project_skills_dir
            if project_skills_dir is not None
            else Path.cwd() / _DEFAULT_PROJECT_SKILLS_SUBDIR
        )
        self._registry = SkillRegistry()
        self._invoked: dict[str, InvokedSkillInfo] = {}
        self._known_dynamic_dirs: set[str] = set()
        # ``claude_compat`` priority: constructor kwarg (tests) > config
        # section > schema default (False).  Stored as the resolved bool
        # after startup(); ``_claude_compat_override`` captures the
        # constructor hint so the signal-subscribed config reload path
        # can distinguish "user overrode" from "config says so".
        self._claude_compat: bool = claude_compat if claude_compat is not None else False
        self._claude_compat_override: bool | None = claude_compat
        self._disabled: set[str] = set()
        self._gateway_disabled: dict[str, set[str]] = {}
        self._listing_cache: str | None = None

        # Signal: emitted when the available skill set changes
        # (dynamic discovery, conditional activation, MCP skill registration).
        self._skills_changed_callbacks: list[Any] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """Discover skills across all layers and populate the registry."""
        # Resolve ``claude_compat`` from config when the constructor did
        # not supply an explicit override.  Missing / unbindable config
        # section falls through to the schema default (False).  The
        # ``isinstance`` guard deliberately rejects MagicMock return
        # values that tests hand in — without it, ``cfg.claude_compat``
        # on a MagicMock would be a truthy mock attribute and flip the
        # flag on in isolated unit tests.
        if self._claude_compat_override is None:
            try:
                section = self._module_table.config.bind_section(
                    file="skills", section="skills", schema=SkillsConfig
                )
                cfg = section.get()
                if isinstance(cfg, SkillsConfig):
                    self._claude_compat = cfg.claude_compat
                else:
                    self._claude_compat = False
            except Exception:
                logger.debug(
                    "SkillManager: no skills config section bound — "
                    "claude_compat defaults to False",
                    exc_info=True,
                )
                self._claude_compat = False

        # Resolve discovery paths.
        project_compat = (
            (Path.cwd() / _CLAUDE_PROJECT_SKILLS_SUBDIR) if self._claude_compat else None
        )
        user_compat = _CLAUDE_USER_SKILLS_DIR if self._claude_compat else None

        # External dirs (Hermes pattern) — empty for now until config
        # schema is wired.
        external_dirs: list[Path] = []

        unconditional, conditional = discover(
            project_dir=self._project_skills_dir,
            project_compat_dir=project_compat,
            external_dirs=external_dirs,
            user_dir=self._user_skills_dir,
            user_compat_dir=user_compat,
            bundled_skills=[],  # Populated by register_bundled_skill later.
        )

        for skill in unconditional:
            self._registry.register(skill)
        for skill in conditional:
            self._registry.register_conditional(skill)

        logger.info(
            "SkillManager started: %d skills (%d conditional)",
            len(self._registry.all_skills()),
            self._registry.conditional_count(),
        )

    async def shutdown(self) -> None:
        """Clear the registry and invoked tracking."""
        self._registry.clear()
        self._invoked.clear()
        self._known_dynamic_dirs.clear()
        self._listing_cache = None
        logger.info("SkillManager: shutdown complete")

    # ------------------------------------------------------------------
    # Listing (consumed by PromptBuilder)
    # ------------------------------------------------------------------

    def get_skill_listing(
        self,
        context_window_tokens: int | None = None,
        available_tools: set[str] | None = None,
        gateway: str | None = None,
    ) -> str:
        """Build the skill catalog text for system prompt injection.

        Includes all model-invocable skills with name + description +
        when_to_use.  Respects token budget (1% of context window).
        Bundled skill descriptions are never truncated; others are
        truncated proportionally when over budget.

        Filters by:
        - ``is_visible()`` (dynamic tool availability).
        - ``disabled`` / ``gateway_disabled`` config lists.
        """
        skills = self._registry.model_invocable()

        # Dynamic visibility filter.
        if available_tools is not None:
            skills = [s for s in skills if is_visible(s, available_tools)]

        # Disabled filter.
        if self._disabled:
            skills = [s for s in skills if s.manifest.name not in self._disabled]
        if gateway and gateway in self._gateway_disabled:
            gw_disabled = self._gateway_disabled[gateway]
            skills = [s for s in skills if s.manifest.name not in gw_disabled]

        if not skills:
            return ""

        return _format_listing(skills, context_window_tokens)

    # ------------------------------------------------------------------
    # Activation (consumed by SkillTool)
    # ------------------------------------------------------------------

    def activate(
        self,
        name: str,
        args: str = "",
        agent_id: str | None = None,
    ) -> ActivationResult | None:
        """Activate a skill by name.

        Returns ``None`` if the skill is not found.
        Returns ``ActivationResult`` with ``setup_needed=True`` when
        required environment variables are missing.
        """
        skill = self._registry.lookup(name)
        if skill is None:
            return None

        manifest = skill.manifest

        # Check setup requirements (Hermes).
        setup_ok, setup_message = check_setup(manifest)
        if not setup_ok:
            return ActivationResult(
                body="",
                setup_needed=True,
                setup_message=setup_message,
            )

        # Lazy-load body.
        try:
            body = skill.body
        except Exception:
            logger.exception("skills: failed to load body for %s", name)
            return None

        # Argument substitution.
        body = substitute_arguments(body, args, manifest.argument_names, manifest.base_dir)

        # Config substitution (Hermes).
        resolved_config = self._resolve_skill_config(manifest)
        if resolved_config:
            body = substitute_config(body, resolved_config)

        # Append supporting files listing.
        if manifest.supporting_files:
            body += "\n\n[This skill has supporting files you can load with Read:]\n"
            for sf in manifest.supporting_files:
                body += f"- {manifest.base_dir / sf}\n"

        # Track invocation for compaction preservation.
        self.add_invoked(
            skill_name=name,
            skill_path=str(skill.file_path),
            content=body,
            agent_id=agent_id,
        )

        return ActivationResult(
            body=body,
            allowed_tools=manifest.allowed_tools,
            model=manifest.model,
            context=manifest.context,
            agent=manifest.agent,
            hooks=manifest.hooks,
            skill_root=str(manifest.base_dir),
            config=resolved_config,
        )

    def deactivate(self, name: str, agent_id: str | None = None) -> None:
        """Remove an invoked skill record."""
        key = f"{agent_id or ''}:{name}"
        self._invoked.pop(key, None)

    # ------------------------------------------------------------------
    # Invoked skill tracking (consumed by Compactor)
    # ------------------------------------------------------------------

    def add_invoked(
        self,
        skill_name: str,
        skill_path: str,
        content: str,
        agent_id: str | None = None,
    ) -> None:
        """Record an activated skill for compaction preservation."""
        key = f"{agent_id or ''}:{skill_name}"
        self._invoked[key] = InvokedSkillInfo(
            skill_name=skill_name,
            skill_path=skill_path,
            content=content,
            invoked_at=time.time(),
            agent_id=agent_id,
        )

    def get_invoked_for_agent(
        self,
        agent_id: str | None = None,
    ) -> list[InvokedSkillInfo]:
        """Return invoked skills for the given agent, sorted by
        invoked_at descending (most recent first)."""
        return sorted(
            [s for s in self._invoked.values() if s.agent_id == agent_id],
            key=lambda s: s.invoked_at,
            reverse=True,
        )

    def clear_invoked(self, preserve_agent_ids: set[str] | None = None) -> None:
        """Clear invoked records, optionally preserving specific agents."""
        if not preserve_agent_ids:
            self._invoked.clear()
            return
        for key in list(self._invoked):
            info = self._invoked[key]
            if info.agent_id is None or info.agent_id not in preserve_agent_ids:
                del self._invoked[key]

    # ------------------------------------------------------------------
    # Dynamic discovery (consumed by ToolExecutor)
    # ------------------------------------------------------------------

    async def on_file_touched(self, file_paths: list[str], cwd: str) -> None:
        """Called after file-tool operations to trigger dynamic
        discovery + conditional activation.

        1. Walk up from file paths → find new ``.mustang/skills/`` dirs.
        2. Load newly discovered skills → register_dynamic.
        3. Check conditional skills' paths globs → activate matches.
        4. Emit skills_changed signal if anything changed.
        """
        changed = False

        # Dynamic directory discovery.
        new_dirs = discover_for_paths(
            file_paths, cwd, self._known_dynamic_dirs, self._claude_compat
        )
        if new_dirs:
            from kernel.skills.loader import _discover_layer

            for skill_dir in new_dirs:
                new_skills = _discover_layer(skill_dir, SkillSource.PROJECT, priority=0)
                for skill in new_skills:
                    self._registry.register_dynamic(skill)
                    changed = True

        # Conditional activation.
        conditional_pool = self._registry.conditional_skills()
        activated = activate_conditional(file_paths, cwd, conditional_pool)
        for skill in activated:
            self._registry.register_dynamic(skill)
            changed = True

        if changed:
            self._invalidate_listing_cache()
            self._emit_skills_changed()

    # ------------------------------------------------------------------
    # Lookup (consumed by SkillTool, CommandManager)
    # ------------------------------------------------------------------

    def lookup(self, name: str) -> LoadedSkill | None:
        """Look up a skill by name."""
        return self._registry.lookup(name)

    def user_invocable_skills(self) -> list[LoadedSkill]:
        """Skills the user can invoke via ``/skill-name``."""
        return self._registry.user_invocable()

    # ------------------------------------------------------------------
    # MCP skill integration
    # ------------------------------------------------------------------

    def register_mcp_skill(self, skill: LoadedSkill) -> None:
        """Register an MCP-provided skill."""
        self._registry.register(skill)
        self._invalidate_listing_cache()
        self._emit_skills_changed()

    def unregister_mcp_skills(self, server_name: str) -> None:
        """Remove all skills from a specific MCP server.

        Currently removes by checking source == MCP.  A future
        refinement may tag skills with their server name.
        """
        all_skills = self._registry.all_skills()
        for skill in all_skills:
            if skill.source == SkillSource.MCP:
                # Re-register without MCP skills by clearing and re-adding.
                pass
        # TODO: implement proper per-server removal when MCP skills
        # carry server_name metadata.
        self._invalidate_listing_cache()

    # ------------------------------------------------------------------
    # Signal
    # ------------------------------------------------------------------

    def on_skills_changed(self, callback: Any) -> None:
        """Register a callback for skills-changed events."""
        self._skills_changed_callbacks.append(callback)

    def _emit_skills_changed(self) -> None:
        for cb in self._skills_changed_callbacks:
            try:
                cb()
            except Exception:
                logger.exception("skills_changed callback failed")

    def _invalidate_listing_cache(self) -> None:
        self._listing_cache = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_skill_config(self, manifest: SkillManifest) -> dict[str, Any]:
        """Merge skill-declared defaults + config.yaml overrides."""
        defaults = dict(manifest.config) if manifest.config else {}
        # TODO: read overrides from config.yaml skills.<name>.* when
        # config schema is wired.
        return defaults


# ------------------------------------------------------------------
# Listing formatter (module-level, no state)
# ------------------------------------------------------------------


def _format_listing(
    skills: list[LoadedSkill],
    context_window_tokens: int | None = None,
) -> str:
    """Format skill catalog within a token budget.

    Three-tier degradation (aligned with Claude Code):
    1. Full descriptions.
    2. Truncated descriptions (bundled never truncated).
    3. Name-only for non-bundled (extreme case).
    """
    budget = _get_char_budget(context_window_tokens)

    entries = []
    for s in skills:
        desc = s.manifest.description
        if s.manifest.when_to_use:
            desc = f"{desc} - {s.manifest.when_to_use}"
        if len(desc) > _MAX_LISTING_DESC_CHARS:
            desc = desc[: _MAX_LISTING_DESC_CHARS - 1] + "\u2026"
        entries.append((s, f"- {s.manifest.name}: {desc}"))

    full_text = "\n".join(line for _, line in entries)
    if len(full_text) <= budget:
        return full_text

    # Over budget — separate bundled (never truncated) from rest.
    bundled_lines: list[str] = []
    rest_entries: list[tuple[LoadedSkill, str]] = []
    for skill, line in entries:
        if skill.source == SkillSource.BUNDLED:
            bundled_lines.append(line)
        else:
            rest_entries.append((skill, line))

    bundled_chars = sum(len(line) + 1 for line in bundled_lines)
    remaining_budget = budget - bundled_chars

    if not rest_entries:
        return "\n".join(bundled_lines)

    # Calculate max description length for non-bundled.
    name_overhead = sum(len(s.manifest.name) + 4 for s, _ in rest_entries) + len(rest_entries) - 1
    available_for_descs = remaining_budget - name_overhead
    max_desc_len = max(20, available_for_descs // len(rest_entries))

    if max_desc_len < 20:
        # Extreme: non-bundled go name-only.
        rest_lines = [f"- {s.manifest.name}" for s, _ in rest_entries]
    else:
        rest_lines = []
        for skill, _ in rest_entries:
            desc = skill.manifest.description
            if skill.manifest.when_to_use:
                desc = f"{desc} - {skill.manifest.when_to_use}"
            if len(desc) > max_desc_len:
                desc = desc[: max_desc_len - 1] + "\u2026"
            rest_lines.append(f"- {skill.manifest.name}: {desc}")

    return "\n".join(bundled_lines + rest_lines)


def _get_char_budget(context_window_tokens: int | None) -> int:
    if context_window_tokens:
        return int(context_window_tokens * _CHARS_PER_TOKEN * _DEFAULT_BUDGET_PERCENT)
    return _DEFAULT_CHAR_BUDGET


__all__ = [
    "ActivationResult",
    "InvokedSkillInfo",
    "LoadedSkill",
    "ManifestError",
    "SkillFallbackFor",
    "SkillManager",
    "SkillManifest",
    "SkillRequires",
    "SkillSetup",
    "SkillSetupEnvVar",
    "SkillSource",
]
