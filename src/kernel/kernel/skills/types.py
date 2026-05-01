"""Public skill types — manifest, loaded skill, activation result.

See ``docs/plans/landed/skill-manager.md`` for the full design.

Design highlights:
- Skill = directory with SKILL.md (YAML frontmatter + Markdown body).
- Frontmatter is a superset of Claude Code + Hermes fields.
- Body is lazy-loaded (frontmatter scanned at startup, body on first use).
- ``ActivationResult`` carries rendered body + metadata for SkillTool.
- ``InvokedSkillInfo`` tracks activated skills for compaction preservation.
"""

from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SkillSource — discovery layer
# ---------------------------------------------------------------------------


class SkillSource(str, enum.Enum):
    """Which layer a skill was discovered from.

    Numeric priority: lower value = higher precedence.
    Used for deduplication when the same skill name appears in
    multiple layers.
    """

    PROJECT = "project"
    EXTERNAL = "external"
    USER = "user"
    BUNDLED = "bundled"
    MCP = "mcp"


# ---------------------------------------------------------------------------
# Eligibility predicates (frontmatter ``requires`` + ``fallback-for``)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillRequires:
    """Eligibility predicates.

    ``bins`` / ``env`` align with Claude Code + HookRequires (static,
    checked at startup).  ``tools`` / ``toolsets`` come from Hermes
    (dynamic, checked at listing time — the tool set can change
    mid-session when MCP servers connect or disconnect).
    """

    bins: tuple[str, ...] = ()
    """Binaries that must resolve via ``shutil.which``."""

    env: tuple[str, ...] = ()
    """Environment variables that must be set and non-empty."""

    tools: tuple[str, ...] = ()
    """Tool names that must be registered for the skill to be visible."""

    toolsets: tuple[str, ...] = ()
    """Toolset names that must be available for the skill to be visible."""


@dataclass(frozen=True)
class SkillFallbackFor:
    """Hermes-style fallback: hide this skill when the primary tools
    or toolsets are available.

    Example: a ``web-search-manual`` skill is hidden when the real
    ``WebSearch`` tool is registered.
    """

    tools: tuple[str, ...] = ()
    toolsets: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Environment setup (Hermes ``setup.env``)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillSetupEnvVar:
    """One entry in the interactive environment-variable setup flow.

    Claude Code only does a boolean ``requires.env`` check (present /
    absent).  Hermes provides guided setup with prompts, help text,
    secret masking, and defaults.
    """

    name: str
    prompt: str
    help: str | None = None
    secret: bool = False
    optional: bool = False
    default: str | None = None


@dataclass(frozen=True)
class SkillSetup:
    """Interactive setup flow run on first skill activation when
    required environment variables are missing."""

    env: tuple[SkillSetupEnvVar, ...] = ()


# ---------------------------------------------------------------------------
# SkillManifest — parsed frontmatter (no body)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillManifest:
    """SKILL.md frontmatter after parsing.  Does **not** contain the
    Markdown body — that is lazy-loaded by ``LoadedSkill.body``.

    Field set is a superset of Claude Code's
    ``parseSkillFrontmatterFields`` + Hermes extensions.
    """

    name: str
    """Skill identity.  Defaults to the containing directory name
    when not set explicitly in frontmatter."""

    description: str
    """Short description shown in skill listing.  Falls back to the
    first ``#`` heading or paragraph in the body when frontmatter
    omits it."""

    has_user_specified_description: bool
    """True when ``description`` came from frontmatter, False when
    it was auto-extracted from the body."""

    # -- Claude Code fields --
    allowed_tools: tuple[str, ...] = ()
    argument_hint: str | None = None
    argument_names: tuple[str, ...] = ()
    when_to_use: str | None = None
    user_invocable: bool = True
    disable_model_invocation: bool = False
    requires: SkillRequires = field(default_factory=SkillRequires)
    os: tuple[str, ...] = ()
    context: Literal["inline", "fork"] | None = None
    agent: str | None = None
    model: str | None = None
    hooks: dict[str, Any] | None = None
    paths: tuple[str, ...] | None = None

    # -- Hermes extensions --
    fallback_for: SkillFallbackFor | None = None
    setup: SkillSetup | None = None
    config: dict[str, Any] | None = None

    # -- Filesystem metadata --
    base_dir: Path = field(default_factory=Path)
    """Absolute path to the skill directory (parent of SKILL.md)."""

    supporting_files: tuple[str, ...] = ()
    """Relative paths of non-SKILL.md files discovered in the skill
    directory.  Listed in the activation message for progressive
    disclosure (LLM can ``Read`` them on demand)."""


# ---------------------------------------------------------------------------
# LoadedSkill — post-discovery, pre-activation
# ---------------------------------------------------------------------------


@dataclass
class LoadedSkill:
    """A skill that survived discovery + eligibility checks.

    The Markdown body is **lazy-loaded**: ``_body`` is ``None`` until
    the first access to the ``body`` property, avoiding upfront I/O
    for skills that are never activated.
    """

    manifest: SkillManifest
    source: SkillSource
    layer_priority: int
    """Lower = higher precedence.  project=0, external=1, user=2,
    bundled=3, mcp=4."""

    file_path: Path
    """Absolute path to SKILL.md (used for deduplication via realpath)."""

    _body: str | None = field(default=None, repr=False)

    @property
    def body(self) -> str:
        """Lazy-load the Markdown body on first access."""
        if self._body is None:
            self._body = _load_body(self.file_path)
        return self._body

    @property
    def content_length(self) -> int:
        """Body character count, used for token budget estimation."""
        return len(self.body)


def _load_body(file_path: Path) -> str:
    """Read SKILL.md and strip the YAML frontmatter, returning only
    the Markdown body."""
    # Import here to avoid circular dependency at module level.
    from kernel.skills.manifest import strip_frontmatter

    text = file_path.read_text(encoding="utf-8")
    return strip_frontmatter(text)


# ---------------------------------------------------------------------------
# InvokedSkillInfo — compaction preservation tracking
# ---------------------------------------------------------------------------


@dataclass
class InvokedSkillInfo:
    """Tracks an activated skill so its content can survive compaction.

    Keyed by ``f"{agent_id or ''}:{skill_name}"`` in SkillManager's
    ``_invoked`` dict — prevents cross-agent overwrites (aligned with
    Claude Code's ``invokedSkills`` Map keying).
    """

    skill_name: str
    skill_path: str
    content: str
    invoked_at: float = field(default_factory=time.time)
    agent_id: str | None = None


# ---------------------------------------------------------------------------
# ActivationResult — returned by SkillManager.activate()
# ---------------------------------------------------------------------------


@dataclass
class ActivationResult:
    """Everything ``SkillTool.call()`` needs after a successful
    activation.

    The ``body`` is the fully rendered Markdown (arguments substituted,
    ``${SKILL_DIR}`` resolved, supporting files listed).
    """

    body: str
    allowed_tools: tuple[str, ...] = ()
    model: str | None = None
    context: Literal["inline", "fork"] | None = None
    agent: str | None = None
    hooks: dict[str, Any] | None = None
    skill_root: str | None = None
    config: dict[str, Any] | None = None

    # Hermes setup flow
    setup_needed: bool = False
    setup_message: str | None = None


__all__ = [
    "ActivationResult",
    "InvokedSkillInfo",
    "LoadedSkill",
    "SkillFallbackFor",
    "SkillManifest",
    "SkillRequires",
    "SkillSetup",
    "SkillSetupEnvVar",
    "SkillSource",
]
