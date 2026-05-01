"""Live probe: env-context section — closure seam Orchestrator → PromptBuilder.

Mirrors CC's ``computeSimpleEnvInfo()`` (prompts.ts:651-710), minus the
deviations documented in ``PromptBuilder._build_env_context``.

Closure seam verified:

    Seam 1:  Orchestrator._run_query → PromptBuilder.build(model=...)
             → _build_env_context(..., model=...)
             (confirms ``self._config.model`` reaches PromptBuilder and
             the rendered env section lands in the PromptSection list
             handed to the LLM's provider.stream())

Exit 0 on success, 1 on any mismatch.

Run:

    cd /home/saki/Documents/truenorth/mustang
    .venv/bin/python scripts/probe_env_context.py
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


def _fail(msg: str) -> int:
    print(f"FAIL: {msg}", file=sys.stderr)
    return 1


@dataclass
class _CapturingProvider:
    """Stand-in for LLMManager.  Captures the ``system`` argument handed
    to ``stream()`` so the probe can prove the env section was actually
    routed into the LLM call — not just rendered in a vacuum.
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


async def no_permission(_req: Any) -> Any:  # pragma: no cover — never called
    raise AssertionError("probe does not trigger tool permission requests")


async def run() -> int:  # noqa: C901 — long, but flat
    from kernel.llm.config import ModelRef
    from kernel.llm.types import PromptSection, TextContent
    from kernel.orchestrator import OrchestratorConfig, OrchestratorDeps
    from kernel.orchestrator.orchestrator import StandardOrchestrator

    scratch = Path(tempfile.mkdtemp(prefix="probe-env-context-"))
    try:
        # ── Boot Orchestrator with a distinguishable model id. ──────────
        model_id = "claude-opus-4-7"
        capturing = _CapturingProvider()

        deps = OrchestratorDeps(provider=capturing)
        orch = StandardOrchestrator(
            deps=deps,
            session_id="probe-env-context",
            config=OrchestratorConfig(
                model=ModelRef(provider="anthropic", model=model_id),
                temperature=None,
            ),
            cwd=scratch,
        )

        # ── Drive the real query() pipeline. ────────────────────────────
        print("Seam 1 — Orchestrator.query() → provider.stream(system=...):")
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

        env_sections = [
            s
            for s in system_sections
            if isinstance(s, PromptSection) and s.text.startswith("# Environment\n")
        ]
        if len(env_sections) != 1:
            return _fail(
                f"expected exactly 1 env-context section in system prompt, "
                f"got {len(env_sections)} (total sections: {len(system_sections)})"
            )
        env = env_sections[0]
        if env.cache is not False:
            return _fail(f"env-context section cache must be False, got {env.cache}")
        print(f"  ✓ env-context section reached provider.stream() (cache={env.cache})")

        expected_model_line = f" - You are powered by the model {model_id}."
        if expected_model_line not in env.text:
            return _fail(
                f"missing model line.\n"
                f"  want: {expected_model_line!r}\n"
                f"  in  : {env.text!r}"
            )
        print(f"  ✓ model line present: {expected_model_line!r}")

        # Required env fields must all survive in the same block.
        required_fragments = [
            f" - Primary working directory: {scratch}",
            "  - Is a git repository: False",
            " - Platform: ",
            " - Shell: ",
            " - OS Version: ",
            " - Date/time (UTC): ",
        ]
        for frag in required_fragments:
            if frag not in env.text:
                return _fail(f"missing required env fragment {frag!r}")
        print("  ✓ all required env fragments present")

        # Guard against CC-only text leaking in.
        forbidden = [
            "knowledge cutoff",
            "named ",  # marketing-name branch framing
            "The exact model ID is",
            "Claude Code is available",
            "Fast mode",
            "most recent Claude model family",
            "(with 1M context)",
        ]
        low = env.text.lower()
        for phrase in forbidden:
            if phrase.lower() in low:
                return _fail(f"forbidden CC-only text leaked into env: {phrase!r}")
        print("  ✓ no CC marketing / cutoff / marketing-name text leaked")

        print("\n── Rendered env section (as routed to LLM) ─────────────────────")
        print(env.text)
        print("────────────────────────────────────────────────────────────────")

        # ── Sanity: model=None path omits the line entirely. ────────────
        from kernel.orchestrator.prompt_builder import PromptBuilder

        text_no_model = PromptBuilder._build_env_context(scratch, model=None)
        if "You are powered by the model" in text_no_model:
            return _fail(
                "model line leaked when model=None — the optional kwarg is "
                "not being respected"
            )
        print("\n  ✓ model=None path correctly omits the model line")

        print("\nOK: env-context closure seam green.")
        return 0

    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
