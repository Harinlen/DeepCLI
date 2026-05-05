"""PromptBuilder — assembles the system prompt for each query() call.

Mirrors Claude Code's ``getSystemPrompt()`` ordering (prompts.ts):
stable/cacheable content first, volatile content last.

CC order: static → session guidance → memory → Environment (last, contains git).

**Cacheable sections** (stable across turns — maximise cache prefix):

1. Identity + security posture          ``orchestrator/identity``
2. ``# System``                         ``orchestrator/system``
3. ``# First principles``               ``orchestrator/first_principles``
4. ``# Doing tasks``                    ``orchestrator/doing_tasks``
5. ``# Executing actions with care``    ``orchestrator/actions_with_care``
6. ``# Using your tools``              ``orchestrator/using_tools``
7. ``# Tone and style``                ``orchestrator/tone_and_style``
8. ``# Output efficiency``             ``orchestrator/output_efficiency``
9. ``# Language``                       ``orchestrator/language`` (when set)
10. Memory strategy (Channel C)        from MemoryManager
11. Memory index (Channel A)           from MemoryManager
12. Available skills listing           from SkillManager
13. Git commit/PR instructions         ``orchestrator/git_commit_pr``
14. Summarize tool results reminder    ``orchestrator/summarize_tool_results``

**Volatile sections** (rebuilt every turn — not cacheable):

15. ``# MCP Server Instructions``      (when servers have instructions)
16. Git context                        from GitManager
17. AGENTS.md contents                 (future)
18. ``# Environment``                  computed at runtime (timestamp → always last)
19. Plan mode instructions             injected by Orchestrator after build()
"""

from __future__ import annotations

import datetime
import logging
import os
import platform
from pathlib import Path

from kernel.llm.config import ModelRef
from kernel.llm.types import PromptSection
from kernel.orchestrator.runtime import system_reminder_section
from kernel.orchestrator.types import OrchestratorDeps

logger = logging.getLogger(__name__)

# Ordered list of static prompt section keys.
# Each key maps to ``prompts/default/orchestrator/<key>.txt``.
# Order matches Claude Code's ``getSystemPrompt()`` return array.
_STATIC_SECTION_KEYS: list[str] = [
    "orchestrator/identity",
    "orchestrator/system",
    "orchestrator/first_principles",
    "orchestrator/doing_tasks",
    "orchestrator/actions_with_care",
    "orchestrator/using_tools",
    "orchestrator/tone_and_style",
    "orchestrator/output_efficiency",
]


class PromptBuilder:
    """Builds ``list[PromptSection]`` for one ``query()`` call.

    Args:
        session_id: Used for debug logging only.
        deps: The orchestrator deps bag — currently only ``memory`` and
              ``skills`` are consulted (both expected to be ``None`` for now).
    """

    def __init__(
        self,
        session_id: str,
        deps: OrchestratorDeps,
    ) -> None:
        """Create a prompt builder.

        Args:
            session_id: Session id used for subsystem context and logging.
            deps: Orchestrator dependency bundle.
        """
        self._session_id = session_id
        self._deps = deps

    async def build(
        self,
        prompt_text: str = "",
        cwd: Path | None = None,
        *,
        model: ModelRef | None = None,
        language: str | None = None,
    ) -> list[PromptSection]:
        """Return the ordered list of prompt sections for this query.

        Args:
            prompt_text: The user's current prompt text.  Reserved for
                future memory relevance queries.
            cwd: Session working directory.  Used for git context
                injection.  Falls back to ``os.getcwd()`` if not
                provided (legacy behaviour).
            model: The active ``ModelRef`` for this turn.  When provided,
                the env context appends a ``You are powered by the model
                <id>.`` line (CC parity, prompts.ts:627 fallback path).
            language: User-preferred response language.  When provided,
                a ``# Language`` section is injected immediately after
                the static block (position 8) — stable for the session
                lifetime so it is cacheable.

        Returns:
            Ordered prompt sections with cacheable sections before volatile ones.
        """
        effective_cwd = cwd or Path(os.getcwd())
        prompts = self._deps.prompts
        sections: list[PromptSection] = []

        # ══ CACHEABLE SECTIONS ══════════════════════════════════════
        # All cache=True sections are grouped first so they form one
        # contiguous prefix that Anthropic prompt caching can target.
        # Order mirrors CC: static → language → memory → skills → git
        # commit/PR → summarize.

        # 1-7. Static behavioral instructions (one merged block)
        if prompts is not None:
            static_parts: list[str] = []
            for key in _STATIC_SECTION_KEYS:
                if prompts.has(key):
                    static_parts.append(prompts.get(key))
            if static_parts:
                sections.append(PromptSection(text="\n\n".join(static_parts), cache=True))

        # 8. Language — stable for the session lifetime, so cacheable.
        if language and prompts is not None and prompts.has("orchestrator/language"):
            sections.append(
                PromptSection(
                    text=prompts.render("orchestrator/language", language=language),
                    cache=True,
                )
            )

        # 9-10. Memory (CC: ``# auto memory`` before ``# Environment``).
        memory = self._deps.memory
        if memory is not None:
            # Channel C — strategy rules (static, cacheable)
            strategy = memory.get_strategy_text() if hasattr(memory, "get_strategy_text") else ""
            if strategy:
                sections.append(PromptSection(text=strategy, cache=True))
            # Channel A — index (cacheable; invalidated on write)
            index_text = await memory.get_index_text()
            if index_text:
                sections.append(
                    PromptSection(
                        text=f"# Memory index\n\n{index_text}",
                        cache=True,
                    )
                )

        # 11. Skills listing
        skills = self._deps.skills
        if skills is not None:
            listing = skills.get_skill_listing()
            if listing:
                sections.append(
                    system_reminder_section(
                        "# Available skills\n\n"
                        "These skills are available through the Skill tool:\n\n"
                        f"{listing}"
                    )
                )

        # 12. Git commit/PR instructions (static, cacheable)
        if prompts is not None and prompts.has("orchestrator/git_commit_pr"):
            sections.append(
                PromptSection(text=prompts.get("orchestrator/git_commit_pr"), cache=True)
            )

        # 13. Summarize tool results reminder
        if prompts is not None and prompts.has("orchestrator/summarize_tool_results"):
            sections.append(
                PromptSection(
                    text=prompts.get("orchestrator/summarize_tool_results"),
                    cache=True,
                )
            )

        # ══ VOLATILE SECTIONS ═══════════════════════════════════════
        # cache=False — rebuilt every turn.  Placed after all cacheable
        # content so the stable prefix above is never broken.

        # 14. MCP Server Instructions — CC's ``getMcpInstructions()``.
        #     Servers can connect/disconnect between turns.
        getter = getattr(self._deps, "mcp_instructions", None)
        if (
            getter is not None
            and prompts is not None
            and prompts.has("orchestrator/mcp_instructions")
        ):
            pairs = [(n, i) for n, i in getter() if i]
            if pairs:
                blocks = "\n\n".join(f"## {name}\n{instructions}" for name, instructions in pairs)
                sections.append(
                    PromptSection(
                        text=prompts.render("orchestrator/mcp_instructions", blocks=blocks),
                        cache=False,
                    )
                )

        # 15. Git context — per-session snapshot from GitManager.
        git_mgr = getattr(self._deps, "git", None)
        if git_mgr is not None:
            git_ctx = await git_mgr.get_context(
                cwd=effective_cwd,
                session_id=self._session_id,
            )
            if git_ctx is not None:
                sections.append(PromptSection(text=git_ctx.format(), cache=False))

        # 16. Project instruction files are deferred until the kernel
        # owns AGENTS.md / CLAUDE.md discovery end-to-end.

        # 17. Environment — contains a live timestamp so it must be last.
        #     CC puts ``# Environment`` last for the same reason.
        sections.append(
            PromptSection(
                text=self._build_env_context(effective_cwd, model=model),
                cache=False,
            )
        )

        # 18. Plan mode instructions — inserted by Orchestrator after build().

        return sections

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_env_context(cwd: Path, *, model: ModelRef | None = None) -> str:
        """Return an environment summary matching CC's ``computeSimpleEnvInfo()``.

        Args:
            cwd: Effective working directory for the session.
            model: Optional active model reference for the environment footer.

        Returns:
            Rendered ``# Environment`` section text.

        CC format (prompts.ts:651-710):
        ``# Environment``
        ``You have been invoked in the following environment:``
        ``- Primary working directory: ...``
        ``- Is a git repository: ...``
        ``- Platform: ...``
        ``- Shell: ...``
        ``- OS Version: ...``
        ``- You are powered by the model <id>.``       (when ``model`` given)

        Deliberately omitted (vs CC):

        - Marketing-name lookup + ``(with 1M context)`` suffix — low ROI,
          per-release maintenance burden, awkward under multi-provider.
          We use CC's null-marketing-name fallback phrasing instead
          (``prompts.ts:627``).
        - Knowledge-cutoff line — only meaningful for Claude models, and
          the WebSearch tool covers "is this post-cutoff?" cases.
        - CC's three product-marketing bullets (``most recent Claude
          model family`` / ``Claude Code is available`` / ``Fast mode``).
          DeepCLI is not Claude Code and is multi-provider.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        shell = os.environ.get("SHELL", "unknown")
        if "zsh" in shell:
            shell_name = "zsh"
        elif "bash" in shell:
            shell_name = "bash"
        else:
            shell_name = shell

        # Detect git repository
        try:
            is_git = (Path(cwd) / ".git").exists()
        except OSError:
            is_git = False

        lines = [
            "# Environment",
            "You have been invoked in the following environment: ",
            f" - Primary working directory: {cwd}",
            f"  - Is a git repository: {is_git}",
            f" - Platform: {platform.system().lower()}",
            f" - Shell: {shell_name}",
            f" - OS Version: {platform.system()} {platform.release()}",
            f" - Date/time (UTC): {now.strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        if model is not None:
            lines.append(f" - You are powered by the model {model.model}.")

        return "\n".join(lines)
