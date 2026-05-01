"""Live probe: MCP instructions section — two closure seams.

Mirrors CC's ``getMcpInstructions()`` (prompts.ts:579-604).

Closure seams verified:

    Seam 1:  PromptManager.render("orchestrator/mcp_instructions", blocks=...)
             → byte-equal to hand-assembled expected string
             (confirms the .txt file exists, loads, and the {blocks}
             placeholder is filled correctly)

    Seam 2:  OrchestratorDeps.mcp_instructions closure
             → Orchestrator._run_query → PromptBuilder.build()
             → PromptSection in provider.stream(system=...)
             (confirms the closure reaches the LLM; also verifies the
             degraded path — MCPManager absent — emits no section)

Exit 0 on success, 1 on any mismatch.

Run:

    cd /home/saki/Documents/truenorth/mustang
    .venv/bin/python scripts/probe_mcp_instructions.py
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

_CC_HEADER = "# MCP Server Instructions"
_CC_INTRO = (
    "The following MCP servers have provided instructions "
    "for how to use their tools and resources:"
)


def _fail(msg: str) -> int:
    print(f"FAIL: {msg}", file=sys.stderr)
    return 1


@dataclass
class _CapturingProvider:
    """Stub LLMManager — records every stream() call for inspection."""

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


async def no_permission(_req: Any) -> Any:  # pragma: no cover
    raise AssertionError("probe does not trigger tool permission requests")


async def run() -> int:  # noqa: C901
    from kernel.llm.config import ModelRef
    from kernel.llm.types import PromptSection, TextContent
    from kernel.orchestrator import OrchestratorConfig, OrchestratorDeps
    from kernel.orchestrator.orchestrator import StandardOrchestrator
    from kernel.prompts.manager import PromptManager

    scratch = Path(tempfile.mkdtemp(prefix="probe-mcp-"))
    try:
        # ── Seam 1: PromptManager renders the template correctly ─────────
        print("Seam 1 — PromptManager.render byte-equality:")

        pm = PromptManager()
        pm.load()

        if not pm.has("orchestrator/mcp_instructions"):
            return _fail("orchestrator/mcp_instructions not loaded by PromptManager")

        blocks_str = "## test-server\nuse tool alpha for beta"
        rendered = pm.render("orchestrator/mcp_instructions", blocks=blocks_str)

        expected = (
            f"{_CC_HEADER}\n\n"
            f"{_CC_INTRO}\n\n"
            f"{blocks_str}"
        )
        if rendered != expected:
            return _fail(
                f"render output does not match expected.\n"
                f"  want: {expected!r}\n"
                f"  got : {rendered!r}"
            )
        print(f"  ✓ render output byte-equal to expected CC format")

        if "{blocks}" in rendered:
            return _fail("placeholder {blocks} was not substituted")
        print("  ✓ {blocks} placeholder fully substituted")

        # ── Seam 2a: section present when MCP server has instructions ────
        print("\nSeam 2a — Orchestrator.query() with connected MCP server:")

        capturing = _CapturingProvider()

        def _mcp_instructions_with_server() -> list[tuple[str, str]]:
            return [("my-mcp-server", "Call tool foo to do bar.")]

        deps = OrchestratorDeps(
            provider=capturing,
            prompts=pm,
            mcp_instructions=_mcp_instructions_with_server,
        )
        orch = StandardOrchestrator(
            deps=deps,
            session_id="probe-mcp-connected",
            config=OrchestratorConfig(
                model=ModelRef(provider="fake", model="fake-model"),
                temperature=None,
            ),
            cwd=scratch,
        )

        async for _ in orch.query([TextContent(text="probe")], on_permission=no_permission):
            pass

        if not capturing.calls:
            return _fail("provider.stream() was never called")

        system_sections: list[PromptSection] = capturing.calls[0]["system"]
        mcp_sections = [
            s for s in system_sections
            if isinstance(s, PromptSection) and s.text.startswith(_CC_HEADER)
        ]
        if len(mcp_sections) != 1:
            return _fail(
                f"expected 1 MCP section in system prompt, "
                f"got {len(mcp_sections)} (total: {len(system_sections)})"
            )
        mcp = mcp_sections[0]

        if mcp.cache is not False:
            return _fail(f"MCP section cache must be False, got {mcp.cache}")
        print(f"  ✓ MCP section reached provider.stream() (cache={mcp.cache})")

        if _CC_INTRO not in mcp.text:
            return _fail(f"CC intro sentence missing from MCP section:\n{mcp.text!r}")
        print("  ✓ CC intro sentence present verbatim")

        if "## my-mcp-server\nCall tool foo to do bar." not in mcp.text:
            return _fail(f"server block missing from MCP section:\n{mcp.text!r}")
        print("  ✓ server block formatted correctly (## name + instructions)")

        # Verify ordering: MCP section comes after env context (no language set).
        env_idx: int | None = None
        mcp_idx: int | None = None
        for i, s in enumerate(system_sections):
            if isinstance(s, PromptSection) and s.text.startswith("# Environment\n"):
                env_idx = i
            elif isinstance(s, PromptSection) and s.text.startswith(_CC_HEADER):
                mcp_idx = i
        if env_idx is None or mcp_idx is None:
            return _fail("could not locate env or mcp sections for ordering check")
        if mcp_idx != env_idx + 1:
            return _fail(
                f"ordering wrong: expected mcp immediately after env; "
                f"env_idx={env_idx}, mcp_idx={mcp_idx}"
            )
        print(f"  ✓ MCP section immediately follows env context (idx {env_idx}→{mcp_idx})")

        # ── Seam 2b: section absent when mcp_instructions is None ───────
        print("\nSeam 2b — Orchestrator.query() in degraded mode (no MCPManager):")

        capturing2 = _CapturingProvider()
        deps2 = OrchestratorDeps(
            provider=capturing2,
            prompts=pm,
            mcp_instructions=None,
        )
        orch2 = StandardOrchestrator(
            deps=deps2,
            session_id="probe-mcp-degraded",
            config=OrchestratorConfig(
                model=ModelRef(provider="fake", model="fake-model"),
                temperature=None,
            ),
            cwd=scratch,
        )

        async for _ in orch2.query([TextContent(text="probe")], on_permission=no_permission):
            pass

        system2: list[PromptSection] = capturing2.calls[0]["system"]
        mcp_sections2 = [
            s for s in system2
            if isinstance(s, PromptSection) and s.text.startswith(_CC_HEADER)
        ]
        if mcp_sections2:
            return _fail(
                "MCP section appeared in degraded mode (mcp_instructions=None) — "
                "it must be absent"
            )
        print("  ✓ MCP section correctly absent in degraded mode")

        # ── Seam 3: SessionManager closure → MCPManager.get_connected() ─
        print("\nSeam 3 — SessionManager._mcp_instructions closure → MCPManager.get_connected():")

        from kernel.mcp import MCPManager
        from kernel.mcp.types import ConnectedServer

        # Instantiate MCPManager without calling startup() — we inject
        # ConnectedServer directly to avoid needing real stdio/SSE transports.
        mcp_mgr = MCPManager(module_table=None)
        mcp_mgr._connections["srv-with-instructions"] = ConnectedServer(
            name="srv-with-instructions",
            client=object(),  # not accessed by get_connected()
            instructions="use tool alpha for beta",
        )
        mcp_mgr._connections["srv-no-instructions"] = ConnectedServer(
            name="srv-no-instructions",
            client=object(),
            instructions=None,
        )

        # Replicate the exact closure body from SessionManager._make_orchestrator.
        def _mcp_instructions_seam3() -> list[tuple[str, str]]:
            return [
                (c.name, c.instructions)
                for c in mcp_mgr.get_connected()
                if c.instructions
            ]

        result = _mcp_instructions_seam3()

        if ("srv-with-instructions", "use tool alpha for beta") not in result:
            return _fail(
                f"closure did not return expected pair.\n"
                f"  want: ('srv-with-instructions', 'use tool alpha for beta')\n"
                f"  got : {result!r}"
            )
        print("  ✓ get_connected() + instructions field flows through closure correctly")

        if any(name == "srv-no-instructions" for name, _ in result):
            return _fail("server with None instructions leaked through the filter")
        print("  ✓ servers with None instructions correctly filtered out")

        # Also verify module_table.get(MCPManager) lookup — the actual path
        # SessionManager uses to retrieve the manager before building the closure.
        from kernel.module_table import KernelModuleTable

        mt = KernelModuleTable(
            flags=None,   # type: ignore[arg-type]
            config=None,  # type: ignore[arg-type]
            state_dir=scratch,
        )
        mt.register(mcp_mgr)

        retrieved = mt.get(MCPManager)
        if retrieved is not mcp_mgr:
            return _fail("module_table.get(MCPManager) returned wrong instance")
        print("  ✓ module_table.get(MCPManager) returns the registered manager")

        print("\nOK: MCP instructions closure seams green.")
        return 0

    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
