"""Shared test utilities for building Orchestrator instances.

Provides ``make_test_orchestrator()`` that constructs a fully-wired
Orchestrator with composition subsystems, matching the old
``_make_orchestrator()`` pattern used across test files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from daemon.config.defaults import apply_defaults
from daemon.config.schema import ProviderRuntimeConfig, SourceConfig
from daemon.engine.conversation import Conversation
from daemon.engine.orchestrator.compactor import Compactor
from daemon.engine.orchestrator.memory_manager import MemoryManager
from daemon.engine.orchestrator.memory_extractor import MemoryExtractor
from daemon.engine.orchestrator.orchestrator import Orchestrator
from daemon.engine.orchestrator.plan_mode import PlanModeController
from daemon.engine.orchestrator.prompt_builder import SystemPromptBuilder
from daemon.engine.orchestrator.tool_executor import ToolExecutor
from daemon.extensions.hooks.registry import HookRegistry
from daemon.extensions.skills.registry import SkillRegistry
from daemon.extensions.tools.registry import ToolRegistry
from daemon.permissions.engine import PermissionEngine
from daemon.permissions.settings import PermissionSettings
from daemon.providers.base import Provider
from daemon.providers.registry import ProviderRegistry


def make_test_orchestrator(
    provider: Provider,
    tmp_path: Path | Any,
    tool_registry: ToolRegistry | None = None,
    hook_registry: HookRegistry | None = None,
    skill_registry: SkillRegistry | None = None,
    memory_store: Any = None,
    config: Any = None,
    permission_engine: PermissionEngine | None = None,
    conversation: Conversation | None = None,
    session_id: str | None = None,
    session_dir: Path | None = None,
    image_cache: Any = None,
    auto_extract_cfg: Any = None,
    on_entry: Any = None,
    task_store: Any = None,
    context_window: int = 200_000,
) -> Orchestrator:
    """Build a test Orchestrator with all composition subsystems wired up.

    Mirrors the old ``_make_orchestrator()`` pattern but uses the new
    composition architecture.
    """
    if config is None:
        config = apply_defaults(SourceConfig())

    # Align config with the fake provider.
    config.default_provider = provider.name
    config.providers = {
        provider.name: ProviderRuntimeConfig(
            type="openai_compatible",
            base_url="http://fake",
            model=f"{provider.name}-model",
            api_key="k",
        )
    }

    registry = ProviderRegistry()
    registry._default_provider = provider.name
    registry.register(provider)

    effective_tool_registry = tool_registry or ToolRegistry()
    effective_hook_registry = hook_registry or HookRegistry()

    if permission_engine is None:
        permission_engine = PermissionEngine(PermissionSettings())

    cwd = Path(tmp_path) if not isinstance(tmp_path, Path) else tmp_path

    compactor = Compactor(context_window=context_window)
    plan_mode = PlanModeController(
        permission_engine,
        session_dir=session_dir,
        session_id=session_id,
    )
    prompt_builder = SystemPromptBuilder(cwd)

    tool_executor = ToolExecutor(
        permission_engine=permission_engine,
        tool_registry=effective_tool_registry,
        hook_registry=effective_hook_registry,
        image_cache=image_cache,
        max_result_chars_override=config.tools.max_result_chars,
        plan_mode_controller=plan_mode,
        skill_setter=lambda p: setattr(prompt_builder, "_active_skill_prompt", p),
        task_store=task_store,
    )

    memory_manager: MemoryManager | None = None
    if memory_store is not None:
        memory_manager = MemoryManager(memory_store, config, cwd)

    memory_extractor: MemoryExtractor | None = None
    if auto_extract_cfg is not None:
        memory_extractor = MemoryExtractor(auto_extract_cfg, session_id=session_id)
    elif config.memory.auto_extract.enabled and memory_store is not None:
        memory_extractor = MemoryExtractor(
            config.memory.auto_extract,
            session_id=session_id,
        )

    orch = Orchestrator(
        registry=registry,
        config=config,
        conversation=conversation,
        tool_executor=tool_executor,
        compactor=compactor,
        memory_manager=memory_manager,
        memory_extractor=memory_extractor,
        plan_mode=plan_mode,
        prompt_builder=prompt_builder,
        skill_registry=skill_registry,
        on_entry=on_entry,
        session_id=session_id,
        session_dir=session_dir,
        task_store=task_store,
    )

    return orch
