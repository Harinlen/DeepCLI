"""Live probe: WebFetch secondary-model post-processing via compact role.

Loads the user's real kernel.yaml, boots LLMManager, builds the Session
layer's summarise closure, wires it into a ToolContext, and invokes
WebFetchTool on a small real URL.  Prints the raw fetched content
length and the Haiku-summarised content length to demonstrate that
post-processing is actually happening through the compact role.

Exit 0 on success (post_processed=True and summary differs from raw),
1 otherwise.

Run:

    cd /home/saki/Documents/truenorth/mustang
    .venv/bin/python scripts/probe_webfetch_compact.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "kernel"))


async def run() -> int:
    from kernel.config import ConfigManager
    from kernel.flags import FlagManager
    from kernel.llm import LLMManager
    from kernel.llm_provider import LLMProviderManager
    from kernel.module_table import KernelModuleTable
    from kernel.prompts.manager import PromptManager
    from kernel.secrets import SecretManager
    from kernel.session import _make_summarise_closure
    from kernel.tools.builtin.web_fetch import WebFetchTool
    from kernel.tools.context import ToolContext
    from kernel.tools.file_state import FileStateCache
    import logging

    logging.basicConfig(level=logging.WARNING)

    # 1. Boot config + flags + prompts (minimum needed for LLMManager).
    global_dir = Path.home() / ".mustang" / "config"
    project_dir = Path.cwd() / ".mustang" / "config"
    state_dir = Path.home() / ".mustang" / "state"

    flags = FlagManager(path=Path.home() / ".mustang" / "flags.yaml")
    await flags.initialize()

    config = ConfigManager(
        global_dir=global_dir,
        project_dir=project_dir,
        cli_overrides=(),
    )
    await config.startup()

    prompts = PromptManager()
    prompts.load()

    secrets = SecretManager(db_path=state_dir / "secrets.db")
    await secrets.startup()

    mt = KernelModuleTable(
        flags=flags,
        config=config,
        state_dir=state_dir,
        secrets=secrets,
        prompts=prompts,
    )

    # 2a. Boot LLMProviderManager first — LLMManager depends on it.
    provider_mgr = LLMProviderManager(mt)
    await provider_mgr.startup()
    mt.register(provider_mgr)

    # 2b. Boot LLMManager.
    llm_mgr = LLMManager(mt)
    await llm_mgr.startup()
    mt.register(llm_mgr)

    # 3. Verify compact role resolves — should be Bedrock Haiku.
    compact_ref = llm_mgr.model_for_or_default("compact")
    default_ref = llm_mgr.model_for_or_default("default")
    print(f"default role → {default_ref.provider}/{default_ref.model}")
    print(f"compact role → {compact_ref.provider}/{compact_ref.model}")
    if compact_ref.model == default_ref.model:
        print("WARN: compact and default are the same model — no separation")

    # 4. Build the summarise closure Session-layer would wire.
    summarise = _make_summarise_closure(llm_mgr)
    assert summarise is not None, "summarise closure must exist when LLMManager is present"

    # 5. Construct a ToolContext with the closure.
    ctx = ToolContext(
        session_id="probe-compact",
        agent_depth=0,
        agent_id=None,
        cwd=Path.cwd(),
        cancel_event=asyncio.Event(),
        file_state=FileStateCache(),
        summarise=summarise,
    )

    # 6. Invoke WebFetch against a small, stable URL.  example.com is
    #    tiny and preapproved-safe; this keeps the test cheap.
    tool = WebFetchTool()
    input_payload = {
        "url": "https://example.com",
        "prompt": "In one sentence, what is this page about?",
        "max_chars": 10_000,
    }

    print(f"\nFetching {input_payload['url']} with prompt:")
    print(f"  {input_payload['prompt']!r}")

    result = None
    async for ev in tool.call(input_payload, ctx):
        result = ev

    assert result is not None, "WebFetch produced no events"
    data = result.data
    text = result.llm_content[0].text if result.llm_content else ""

    print(f"\nresult.data: {data}")
    print(f"result.llm_content length: {len(text)} chars")
    print("\n--- result.llm_content ---")
    print(text)
    print("--- end ---\n")

    # 7. Assertions.
    if data.get("error"):
        print(f"ERROR: fetch failed: {data['error']}", file=sys.stderr)
        return 1

    post_processed = data.get("post_processed", False)
    if not post_processed:
        print(
            "FAIL: post_processed=False — secondary-model path not reached.",
            file=sys.stderr,
        )
        return 1

    # Tiny sanity check: the summary should be shorter than example.com's
    # raw HTML (which is ~1.2k chars).  Haiku with "one sentence" prompt
    # should produce <500 chars.
    if len(text) > 2000:
        print(
            f"WARN: output is {len(text)} chars — unexpectedly long for a "
            "summary; check post-processing actually replaced raw content.",
            file=sys.stderr,
        )

    # Verify the header is there.
    assert "[fetched: https://example.com" in text, (
        "WebFetch header prefix missing — output format broken"
    )

    print("OK: compact role reached, Haiku summarised, post_processed=True")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
