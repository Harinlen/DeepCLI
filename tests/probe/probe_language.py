"""Live probe: ``# Language`` section — all 3 closure seams (Phase 5).

Mirrors CC's ``getLanguageSection()`` (prompts.ts:142-149).  The section
is injected into the system prompt when a language preference is set;
CC reads ``getInitialSettings().language``, Mustang reads
``orchestrator.language`` in ``config.yaml`` via ConfigManager.

Closure seams verified:

    Seam 1:  PromptManager.load() → orchestrator/language.txt
             (confirms the .txt ships with the package and renders the
             expected CC-aligned text when substituted with a language
             name)

    Seam 2:  ConfigManager.bind_section → OrchestratorPrefs.language
             → SessionManager.startup → SessionManager._make_orchestrator
             → OrchestratorConfig.language
             (confirms a real config.yaml value drives the language
             populated on new orchestrators — the wiring SessionManager
             added in Phase 5)

    Seam 3:  Orchestrator.query() → PromptBuilder.build(language=...)
             → provider.stream(system=...)
             (confirms the rendered section lands in the list handed to
             the LLM, at the right ordering, with cache=True)

Exit 0 on success, 1 on any mismatch.

Run:

    cd /home/saki/Documents/truenorth/mustang
    .venv/bin/python scripts/probe_language.py
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


# Verbatim expected render for ``language="English"``.
EXPECTED_ENGLISH = (
    "# Language\n"
    "Always respond in English. Use English for all explanations, "
    "comments, and communications with the user. Technical terms "
    "and code identifiers should remain in their original form."
)


def _fail(msg: str) -> int:
    print(f"FAIL: {msg}", file=sys.stderr)
    return 1


@dataclass
class _CapturingProvider:
    """Stand-in for LLMManager.  Captures the ``system`` argument so we
    can prove the rendered language section was routed to the LLM."""

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


async def no_permission(_req: Any) -> Any:  # pragma: no cover — never called
    raise AssertionError("probe does not trigger tool permission requests")


async def run() -> int:  # noqa: C901 — long, but flat
    from kernel.config import ConfigManager
    from kernel.flags import FlagManager
    from kernel.llm.config import ModelRef
    from kernel.llm.types import PromptSection, TextContent
    from kernel.module_table import KernelModuleTable
    from kernel.orchestrator.config_section import OrchestratorPrefs
    from kernel.prompts.manager import PromptManager
    from kernel.session import SessionManager

    scratch = Path(tempfile.mkdtemp(prefix="probe-language-"))
    try:
        state_dir = scratch / "state"
        state_dir.mkdir(mode=0o700)

        # ── Seam 1: real PromptManager scans the shipped .txt file. ──
        prompts = PromptManager()
        prompts.load()

        print("Seam 1 — PromptManager scan:")
        if not prompts.has("orchestrator/language"):
            return _fail("missing prompt key 'orchestrator/language'")
        raw = prompts.get("orchestrator/language")
        print(f"  ✓ loaded orchestrator/language ({len(raw)} chars)")

        rendered = prompts.render("orchestrator/language", language="English")
        if rendered != EXPECTED_ENGLISH:
            print("Mismatch against expected CC render:")
            print(f"  want: {EXPECTED_ENGLISH!r}")
            print(f"  got : {rendered!r}")
            return _fail("language.txt drifted from expected CC text")
        print("  ✓ rendered English version matches expected CC text")

        # Multilingual sanity — the memory gate requires CJK to work too.
        rendered_cn = prompts.render("orchestrator/language", language="中文")
        if rendered_cn.count("中文") != 2:
            return _fail(
                f"CJK substitution broken; expected 2 occurrences of 中文, "
                f"got {rendered_cn.count('中文')} in: {rendered_cn!r}"
            )
        print("  ✓ CJK substitution works (中文 filled in both slots)")

        # ── Seam 2: ConfigManager → OrchestratorPrefs → SessionManager. ─
        cfg_global = scratch / "cfg_global"
        cfg_project = scratch / "cfg_project"
        cfg_global.mkdir()
        cfg_project.mkdir()

        # Write a real YAML file so ConfigManager.startup picks it up.
        (cfg_global / "config.yaml").write_text(
            "orchestrator:\n  language: English\n",
            encoding="utf-8",
        )

        flags = FlagManager(path=scratch / "flags.yaml")
        await flags.initialize()
        config = ConfigManager(global_dir=cfg_global, project_dir=cfg_project, cli_overrides=())
        await config.startup()

        # Prove the section itself validates.
        ro = config.get_section(file="config", section="orchestrator", schema=OrchestratorPrefs)
        if ro.get().language != "English":
            return _fail(
                f"ConfigManager did not surface language from yaml; got {ro.get().language!r}"
            )
        print("\nSeam 2 — ConfigManager yielded language='English' from config.yaml")

        # Boot SessionManager through the real lifecycle so its startup
        # binds the section and _make_orchestrator reads it.
        mt = KernelModuleTable(
            flags=flags,
            config=config,
            state_dir=state_dir,
            prompts=prompts,
        )
        session_mgr = SessionManager(mt)
        await session_mgr.startup()

        # _make_orchestrator is the method that threads OrchestratorPrefs
        # into OrchestratorConfig.language.  Exercise it directly.
        capturing = _CapturingProvider()

        # Replace the provider discovery path: deps.provider is required,
        # so we patch _make_orchestrator's LLMManager lookup by asking
        # it for a config-less orchestrator (config=None triggers the
        # prefs-read branch).  We still need to give it a provider — done
        # via the OrchestratorDeps hand-off inside the manager, which
        # reaches for LLMManager.  Since LLMManager isn't booted here,
        # we patch in a fake via the module table.
        class _FakeLLM:
            def model_for(self, role: str) -> Any:
                return ModelRef(provider="fake", model="fake-model")

            async def stream(self, **kwargs: Any) -> AsyncGenerator[Any, None]:
                return await capturing.stream(**kwargs)

        from kernel.llm import LLMManager

        mt._subsystems[LLMManager] = _FakeLLM()  # type: ignore[assignment]

        orch, _task_reg = session_mgr._make_orchestrator(
            session_id="probe-language",
            cwd=scratch,
            initial_history=[],
            config=None,
        )
        # Swap orch's provider so we can capture stream() calls.  The
        # orchestrator stores deps at construction time; deps.provider
        # was set from the module table lookup above.
        orch._deps.provider = capturing  # type: ignore[attr-defined]

        if orch.config.language != "English":
            return _fail(
                f"SessionManager._make_orchestrator did not propagate "
                f"language into OrchestratorConfig; got "
                f"{orch.config.language!r}"
            )
        print("  ✓ OrchestratorConfig.language = 'English' after _make_orchestrator")

        # ── Seam 3: query() drives language into provider.stream() ─────
        print("\nSeam 3 — Orchestrator.query() → provider.stream(system=...):")
        async for _ in orch.query(
            [TextContent(text="probe")],  # type: ignore[list-item]
            on_permission=no_permission,
        ):
            pass

        if not capturing.calls:
            return _fail("provider.stream() was never called — query aborted early")

        system_sections: list[PromptSection] = capturing.calls[0]["system"]
        if not isinstance(system_sections, list):
            return _fail(
                f"system argument is not a list of PromptSection: {type(system_sections).__name__}"
            )

        language_sections = [
            s
            for s in system_sections
            if isinstance(s, PromptSection) and s.text.startswith("# Language\n")
        ]
        if len(language_sections) != 1:
            return _fail(
                f"expected exactly 1 # Language section, got "
                f"{len(language_sections)} (total sections: {len(system_sections)})"
            )
        lang = language_sections[0]
        if lang.cache is not True:
            return _fail(
                f"language section should be cache=True (stable user "
                f"preference); got cache={lang.cache}"
            )
        if lang.text != EXPECTED_ENGLISH:
            print("Rendered section mismatch:")
            print(f"  want: {EXPECTED_ENGLISH!r}")
            print(f"  got : {lang.text!r}")
            return _fail("language section text drifted from CC expectation")
        print(f"  ✓ language section reached provider.stream() (cache={lang.cache})")
        print(f"  ✓ text matches expected CC render ({len(lang.text)} chars)")

        # Placement check — language must come immediately after env context.
        env_idx: int | None = None
        lang_idx: int | None = None
        for i, s in enumerate(system_sections):
            if isinstance(s, PromptSection):
                if s.text.startswith("# Environment\n"):
                    env_idx = i
                elif s.text.startswith("# Language\n"):
                    lang_idx = i
        if env_idx is None or lang_idx is None:
            return _fail("missing env or language section for ordering check")
        if lang_idx != env_idx + 1:
            return _fail(
                f"language must come immediately after env context; "
                f"got env_idx={env_idx}, lang_idx={lang_idx}"
            )
        print(f"  ✓ ordering respected: env_idx={env_idx}, lang_idx={lang_idx} (contiguous)")

        print("\n── Rendered # Language section (as routed to LLM) ───────────")
        print(lang.text)
        print("──────────────────────────────────────────────────────────────")

        # ── Guard: language=None path emits no # Language section. ───────
        from kernel.orchestrator import OrchestratorConfig, OrchestratorDeps
        from kernel.orchestrator.orchestrator import StandardOrchestrator

        capturing2 = _CapturingProvider()
        deps2 = OrchestratorDeps(provider=capturing2, prompts=prompts)
        orch2 = StandardOrchestrator(
            deps=deps2,
            session_id="probe-language-none",
            config=OrchestratorConfig(
                model=ModelRef(provider="fake", model="fake-model"),
                temperature=None,
                language=None,
            ),
            cwd=scratch,
        )
        async for _ in orch2.query([TextContent(text="probe")], on_permission=no_permission):
            pass
        if not capturing2.calls:
            return _fail("provider.stream() never called on language=None path")
        for s in capturing2.calls[0]["system"]:
            if isinstance(s, PromptSection) and "# Language" in s.text:
                return _fail("language=None path leaked a # Language section")
        print("\n  ✓ language=None path emits no # Language section")

        await session_mgr.shutdown()
        print("\nOK: language closure seams green.")
        return 0

    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
