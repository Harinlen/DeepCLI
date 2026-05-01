"""Live probe: session-specific guidance section — all 3 closure seams.

Mirrors CC's ``getSessionSpecificGuidanceSection()`` (prompts.ts:352-400).

Closure seams verified:

    Seam 1:  orchestrator._deps.prompts.get(...)
             Orchestrator → PromptManager
             (confirms the 6 .txt files exist and render with expected
             bullets across tool-set scenarios)

    Seam 2:  tool_manager.snapshot_for_session(...).lookup.keys()
             Orchestrator → ToolManager
             (confirms the real tool-name set coming out of the registry
             matches the names our conditional gate checks for)

    Seam 3:  Orchestrator.query() → provider.stream(system=...)
             (confirms _inject_session_guidance is called at the right
             point in the real query pipeline and the rendered section
             lands in the PromptSection list handed to the LLM)

Exit 0 on success, 1 on any mismatch.

Run:

    cd /home/saki/Documents/truenorth/mustang
    .venv/bin/python scripts/probe_session_guidance.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "kernel"))


# CC verbatim (prompts.ts:370).  Byte-compared as a canary for
# silent edits to the always-on bullet.
CC_INTERACTIVE_SHELL = (
    "If you need the user to run a shell command themselves (e.g., an "
    "interactive login like `gcloud auth login`), suggest they type `! "
    "<command>` in the prompt — the `!` prefix runs the command in this "
    "session so its output lands directly in the conversation."
)


def _fail(msg: str) -> int:
    print(f"FAIL: {msg}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# Minimal LLM provider for Seam 3 (captures what was sent to stream()).
# ---------------------------------------------------------------------------


@dataclass
class _CapturingProvider:
    """Stand-in for LLMManager.  Captures the ``system`` argument handed
    to ``stream()`` so the probe can prove the guidance section was
    actually routed into the LLM call — not just rendered in a vacuum.
    """

    calls: list[dict[str, Any]] = field(default_factory=list)

    def model_for(self, role: str) -> str:
        return "fake-model"

    async def stream(self, **kwargs: Any) -> AsyncGenerator[Any, None]:
        self.calls.append(kwargs)
        from kernel.llm.types import TextChunk, UsageChunk

        async def _emit() -> AsyncGenerator[Any, None]:
            yield TextChunk(content="ok")
            yield UsageChunk(input_tokens=5, output_tokens=2)

        return _emit()


class _StubSkills:
    """SkillManager stand-in reporting a non-empty listing so the
    has_skills path fires."""

    def get_skill_listing(self) -> str:
        return "# Available skills\n- /commit"


async def no_permission(_req: Any) -> Any:  # pragma: no cover — never called
    raise AssertionError("probe does not trigger tool permission requests")


# ---------------------------------------------------------------------------
# Main probe.
# ---------------------------------------------------------------------------


async def run() -> int:  # noqa: C901 — long, but flat
    from kernel.config import ConfigManager
    from kernel.flags import FlagManager
    from kernel.llm.config import ModelRef
    from kernel.llm.types import PromptSection, TextContent
    from kernel.module_table import KernelModuleTable
    from kernel.orchestrator import OrchestratorConfig, OrchestratorDeps
    from kernel.orchestrator.orchestrator import StandardOrchestrator
    from kernel.prompts.manager import PromptManager
    from kernel.tools import ToolManager

    # ── 0. Scratch workspace so we never touch ~/.mustang. ──────────
    scratch = Path(tempfile.mkdtemp(prefix="probe-session-guidance-"))
    try:
        state_dir = scratch / "state"
        state_dir.mkdir(mode=0o700)

        # ── Seam 1: real PromptManager scans the shipped .txt files. ─
        prompts = PromptManager()
        prompts.load()

        bullet_keys = [
            "deny_ask",
            "interactive_shell",
            "agent_tool",
            "search_direct",
            "search_explore_agent",
            "skill_invoke",
        ]
        print("Seam 1 — PromptManager scan:")
        for key in bullet_keys:
            full = f"orchestrator/session_guidance/{key}"
            if not prompts.has(full):
                return _fail(f"missing prompt key {full!r}")
            text = prompts.get(full)
            print(f"  ✓ {full}: {len(text)} chars")

        got = prompts.get("orchestrator/session_guidance/interactive_shell").strip()
        if got != CC_INTERACTIVE_SHELL:
            print("Mismatch for interactive_shell bullet:")
            print(f"  want: {CC_INTERACTIVE_SHELL!r}")
            print(f"  got : {got!r}")
            return _fail("interactive_shell bullet drift — update probe or .txt")
        print("  ✓ interactive_shell matches CC verbatim")

        # ── 1. Boot FlagManager + ConfigManager (bootstrap services). ─
        flags = FlagManager(path=scratch / "flags.yaml")
        await flags.initialize()

        cfg_global = scratch / "cfg_global"
        cfg_project = scratch / "cfg_project"
        cfg_global.mkdir()
        cfg_project.mkdir()
        config = ConfigManager(
            global_dir=cfg_global,
            project_dir=cfg_project,
            cli_overrides=(),
        )
        await config.startup()

        mt = KernelModuleTable(
            flags=flags,
            config=config,
            state_dir=state_dir,
            prompts=prompts,
        )

        # ── Seam 2: real ToolManager.snapshot_for_session(...) ───────
        tool_mgr = ToolManager(mt)
        await tool_mgr.startup()
        mt.register(tool_mgr)

        snapshot = tool_mgr.snapshot_for_session(session_id="probe")
        tool_names = set(snapshot.lookup.keys())

        print(f"\nSeam 2 — ToolManager snapshot yielded {len(tool_names)} tools:")
        print(f"  {sorted(tool_names)}")

        required = {"Agent", "AskUserQuestion", "Skill"}
        missing = required - tool_names
        if missing:
            return _fail(
                f"ToolManager snapshot missing names our gate depends on: "
                f"{sorted(missing)}.  The conditional strings in "
                f"_build_session_guidance wouldn't match real snapshot output."
            )
        print(f"  ✓ all gate-dependent names present: {sorted(required)}")

        # ── Seam 2b: plan-mode snapshot still contains Agent ─────────
        # CC parity: Agent (kind=orchestrate) is NOT in _MUTATING_KINDS so
        # it survives plan-mode filtering.  Session guidance must keep its
        # agent/search/explore bullets even when plan_mode=True.
        pm_snapshot = tool_mgr.snapshot_for_session(session_id="probe", plan_mode=True)
        pm_names = {s.name for s in pm_snapshot.schemas}  # LLM-visible only

        print(f"\nSeam 2b — plan-mode snapshot (schema tools) yielded {len(pm_names)} tools:")
        print(f"  {sorted(pm_names)}")

        if "Agent" not in pm_names:
            return _fail(
                "AgentTool absent from plan-mode snapshot — kind must be "
                "ToolKind.orchestrate (not execute) to survive plan-mode filter."
            )
        print("  ✓ Agent present in plan-mode snapshot")

        for mutating in ("Bash", "FileEdit", "FileWrite"):
            if mutating in pm_names:
                return _fail(
                    f"{mutating} should be filtered in plan mode (kind=execute/edit)"
                )
        print("  ✓ Bash / FileEdit / FileWrite absent (mutating, correctly filtered)")
        # (Guidance builder check follows after orchestrator construction below.)

        # ── 2. Build an Orchestrator wired to the real ToolManager.
        capturing = _CapturingProvider()
        skills = _StubSkills()

        deps = OrchestratorDeps(
            provider=capturing,
            prompts=prompts,
            skills=skills,
            tool_source=tool_mgr,
        )
        orch = StandardOrchestrator(
            deps=deps,
            session_id="probe-session-guidance",
            config=OrchestratorConfig(
                model=ModelRef(provider="fake", model="fake-model"),
                temperature=None,
            ),
        )

        # ── Seam 2b (cont): guidance bullets with plan-mode tool set. ─
        pm_text = orch._build_session_guidance(pm_names, has_skills=False)
        if pm_text is None:
            return _fail("_build_session_guidance returned None for plan-mode tool set")
        for phrase in ("Agent tool", "Glob or Grep", "subagent_type=Explore"):
            if phrase not in pm_text:
                return _fail(
                    f"plan-mode guidance missing {phrase!r} — "
                    f"agent bullets must appear even in plan mode (CC parity)"
                )
        print("  ✓ agent/search/explore bullets present with plan-mode tool set")

        # ── 3. Drive the real query() pipeline. ─────────────────────
        print("\nSeam 3 — Orchestrator.query() pipeline:")
        async for _ in orch.query(
            [TextContent(text="probe")], on_permission=no_permission
        ):
            pass

        if not capturing.calls:
            return _fail("provider.stream() was never called — query aborted early")

        system_sections: list[PromptSection] = capturing.calls[0]["system"]
        if not isinstance(system_sections, list):
            return _fail(
                f"system argument is not a list of PromptSection: "
                f"{type(system_sections).__name__}"
            )

        guidance_sections = [
            s for s in system_sections
            if isinstance(s, PromptSection) and "# Session-specific guidance" in s.text
        ]
        if len(guidance_sections) != 1:
            return _fail(
                f"expected exactly 1 Session-specific-guidance section in "
                f"system prompt, got {len(guidance_sections)} "
                f"(total sections: {len(system_sections)})"
            )
        gs = guidance_sections[0]
        if gs.cache is not False:
            return _fail(
                f"guidance section cache should be False (dynamic), got {gs.cache}"
            )

        bullets = [ln for ln in gs.text.splitlines() if ln.startswith(" - ")]
        print(f"  ✓ guidance section reached provider.stream(): {len(bullets)} bullets")

        # With Agent + AskUserQuestion + Skill + skills stub enabled, all
        # 6 bullets should fire.
        if len(bullets) != 6:
            return _fail(
                f"expected 6 bullets (full bundle), got {len(bullets)}\n{gs.text}"
            )

        for expected in (
            "denied a tool call",
            "`! <command>`",
            "Subagents",
            "Glob or Grep",
            "subagent_type=Explore",
            "/<skill-name>",
        ):
            if expected not in gs.text:
                return _fail(f"missing expected phrase {expected!r} in guidance text")
        print("  ✓ all 6 expected phrases present")

        # Guard against forbidden CC-only markers.
        low = gs.text.lower()
        for forbidden in (
            "fork",
            "adversarial verification",
            "verifier",
            "discoverskills",
            "skills relevant to your task",
        ):
            if forbidden in low:
                return _fail(
                    f"forbidden CC-only text leaked into guidance: {forbidden!r}"
                )
        print("  ✓ no fork / verification / discover-skills text leaked")

        print("\n── Rendered section (as routed to LLM) ─────────────────────────")
        print(gs.text)
        print("────────────────────────────────────────────────────────────────")

        # ── 4. Scenario sweep on _build_session_guidance directly. ──
        print("\nBuilder sweep (varying tool sets + has_skills):")
        for label, tools, has_skills, expected in [
            ("no tools, no skills", set(), False, 1),
            ("only AskUserQuestion", {"AskUserQuestion"}, False, 2),
            ("only Agent", {"Agent"}, False, 4),
            ("Agent + Skill + has_skills", {"Agent", "Skill"}, True, 5),
            (
                "full bundle",
                {"AskUserQuestion", "Agent", "Skill"},
                True,
                6,
            ),
        ]:
            text = orch._build_session_guidance(tools, has_skills)
            if text is None:
                return _fail(f"{label}: _build_ returned None unexpectedly")
            got_n = len([ln for ln in text.splitlines() if ln.startswith(" - ")])
            if got_n != expected:
                return _fail(
                    f"{label}: expected {expected} bullets, got {got_n}\n{text}"
                )
            print(f"  ✓ {label}: {got_n} bullets")

        # ── Seam 3b: plan-mode query() — agent bullets reach the LLM. ─
        # Drive a real query() with plan_mode=True and verify the captured
        # system prompt contains the agent/search/explore guidance bullets.
        print("\nSeam 3b — plan-mode Orchestrator.query() pipeline:")
        pm_capturing = _CapturingProvider()
        pm_deps = OrchestratorDeps(
            provider=pm_capturing,
            prompts=prompts,
            skills=skills,
            tool_source=tool_mgr,
        )
        pm_orch = StandardOrchestrator(
            deps=pm_deps,
            session_id="probe-plan-mode",
            config=OrchestratorConfig(
                model=ModelRef(provider="fake", model="fake-model"),
                temperature=None,
            ),
        )
        pm_orch.set_plan_mode(True)

        async for _ in pm_orch.query(
            [TextContent(text="probe plan mode")], on_permission=no_permission
        ):
            pass

        if not pm_capturing.calls:
            return _fail("plan-mode provider.stream() was never called")

        pm_system: list[PromptSection] = pm_capturing.calls[0]["system"]
        pm_guidance = [
            s for s in pm_system
            if isinstance(s, PromptSection) and "# Session-specific guidance" in s.text
        ]
        if len(pm_guidance) != 1:
            return _fail(
                f"expected 1 Session-specific-guidance section in plan-mode system "
                f"prompt, got {len(pm_guidance)}"
            )
        for phrase in ("Agent tool", "Glob or Grep", "subagent_type=Explore"):
            if phrase not in pm_guidance[0].text:
                return _fail(
                    f"plan-mode system prompt missing {phrase!r} — "
                    f"agent bullets must survive plan mode (CC parity)"
                )
        pm_bullets = [
            ln for ln in pm_guidance[0].text.splitlines() if ln.startswith(" - ")
        ]
        print(f"  ✓ guidance section reached LLM in plan mode: {len(pm_bullets)} bullets")
        print(f"  ✓ agent/search/explore bullets present")

        # ── 5. Shutdown so state_dir resources close cleanly. ───────
        await tool_mgr.shutdown()

        print("\nOK: all closure seams green (including plan-mode).")
        return 0

    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
