"""ToolAuthorizer — permission_denied / permission_requested fire sites."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from kernel.connection_auth import AuthContext
from kernel.hooks import HookEvent, HookEventCtx
from kernel.orchestrator.types import ToolKind
from kernel.tool_authz.authorizer import ToolAuthorizer
from kernel.tool_authz.rule_parser import parse_rule
from kernel.tool_authz.types import (
    AuthorizeContext,
    PermissionAsk,
    PermissionDeny,
    RuleSource,
)
from kernel.tools.tool import Tool
from kernel.tools.types import (
    PermissionSuggestion,
    ToolCallProgress,
    ToolCallResult,
)


class _FakeTool(Tool[dict[str, Any], str]):
    name = "Echo"
    description = "test"
    kind = ToolKind.read

    def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
        return PermissionSuggestion(risk="low", default_decision="allow", reason="test")

    def is_destructive(self, _input: dict[str, Any]) -> bool:
        return False

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        yield ToolCallResult(data=input, llm_content=[], display=None)  # type: ignore[arg-type]


class _RecordingHookManager:
    """Captures every fire() call; mimics HookManager public surface."""

    def __init__(self, handlers: dict[HookEvent, list[Any]] | None = None) -> None:
        self._handlers = handlers or {}
        self.captured: list[HookEventCtx] = []

    async def fire(self, ctx: HookEventCtx) -> bool:
        self.captured.append(ctx)
        blocked = False
        for handler in self._handlers.get(ctx.event, []):
            try:
                result = handler(ctx)
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:
                if type(exc).__name__ == "HookBlock":
                    from kernel.hooks import EVENT_SPECS

                    if EVENT_SPECS[ctx.event].can_block:
                        blocked = True
        return blocked


def _fake_auth() -> AuthContext:
    return AuthContext(
        connection_id="test",
        credential_type="token",
        remote_addr="127.0.0.1:1",
        authenticated_at=datetime.now(timezone.utc),
    )


def _auth_ctx(mode: str = "default") -> AuthorizeContext:
    return AuthorizeContext(
        session_id="s-1",
        agent_depth=0,
        mode=mode,  # type: ignore[arg-type]
        cwd=Path.cwd(),
        connection_auth=_fake_auth(),
        should_avoid_prompts=False,
    )


def _authorizer_with_hooks(hook_mgr: Any) -> ToolAuthorizer:
    """Build an authorizer whose module_table.get(HookManager) returns hook_mgr."""
    from unittest.mock import MagicMock

    from kernel.tool_authz.bash_classifier import BashClassifier
    from kernel.tool_authz.rule_engine import RuleEngine
    from kernel.tool_authz.rule_store import RuleStore
    from kernel.tool_authz.session_grant_cache import SessionGrantCache

    authz = ToolAuthorizer.__new__(ToolAuthorizer)
    mt = MagicMock()

    def _get(cls: Any) -> Any:
        # Return hook_mgr only when asked for HookManager; raise KeyError otherwise
        # (matches KernelModuleTable's semantics).
        if cls.__name__ == "HookManager":
            return hook_mgr
        raise KeyError(cls)

    mt.get.side_effect = _get
    authz._module_table = mt  # type: ignore[attr-defined]
    authz._rule_store = RuleStore()
    authz._rule_engine = RuleEngine()
    authz._grant_cache = SessionGrantCache()
    authz._bash_classifier = BashClassifier(enabled=False)
    return authz


# ---------------------------------------------------------------------------
# Fire points
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_deny_decision_fires_permission_denied() -> None:
    mgr = _RecordingHookManager()
    authz = _authorizer_with_hooks(mgr)
    authz._rule_store._config_rules = [parse_rule("Echo", "deny", RuleSource.USER, 0)]

    decision = await authz.authorize(tool=_FakeTool(), tool_input={"text": "x"}, ctx=_auth_ctx())
    assert isinstance(decision, PermissionDeny)
    # Exactly one hook fire, for permission_denied.
    assert len(mgr.captured) == 1
    assert mgr.captured[0].event == HookEvent.PERMISSION_DENIED
    assert mgr.captured[0].tool_name == "Echo"
    assert mgr.captured[0].error_message == decision.message


@pytest.mark.anyio
async def test_ask_decision_fires_permission_requested() -> None:
    mgr = _RecordingHookManager()
    authz = _authorizer_with_hooks(mgr)
    authz._rule_store._config_rules = [parse_rule("Echo", "ask", RuleSource.USER, 0)]

    decision = await authz.authorize(tool=_FakeTool(), tool_input={"text": "x"}, ctx=_auth_ctx())
    assert isinstance(decision, PermissionAsk)
    assert len(mgr.captured) == 1
    assert mgr.captured[0].event == HookEvent.PERMISSION_REQUESTED


@pytest.mark.anyio
async def test_allow_decision_fires_no_hook() -> None:
    """Allow is silent — downstream pre/post tool use hooks cover observability."""
    mgr = _RecordingHookManager()
    authz = _authorizer_with_hooks(mgr)
    # No rules → default_risk "allow" path.
    await authz.authorize(tool=_FakeTool(), tool_input={"text": "x"}, ctx=_auth_ctx())
    assert mgr.captured == []


@pytest.mark.anyio
async def test_hook_fire_failure_does_not_corrupt_decision() -> None:
    class ExplodingHooks:
        async def fire(self, ctx: HookEventCtx) -> bool:
            raise RuntimeError("hook fire internals blew up")

    authz = _authorizer_with_hooks(ExplodingHooks())
    authz._rule_store._config_rules = [parse_rule("Echo", "deny", RuleSource.USER, 0)]

    decision = await authz.authorize(tool=_FakeTool(), tool_input={"text": "x"}, ctx=_auth_ctx())
    # Deny decision survives the hook crash.
    assert isinstance(decision, PermissionDeny)


@pytest.mark.anyio
async def test_missing_hookmanager_skips_fire_silently() -> None:
    """Degraded mode: no HookManager in module_table → authorize still works."""
    authz = _authorizer_with_hooks(None)  # resolver returns None
    # Fix the mock: return None instead of hook_mgr when cls is HookManager.
    from unittest.mock import MagicMock

    mt = MagicMock()

    def _get(cls: Any) -> Any:
        raise KeyError(cls)  # always missing

    mt.get.side_effect = _get
    authz._module_table = mt  # type: ignore[attr-defined]
    authz._rule_store._config_rules = [parse_rule("Echo", "deny", RuleSource.USER, 0)]

    decision = await authz.authorize(tool=_FakeTool(), tool_input={"text": "x"}, ctx=_auth_ctx())
    assert isinstance(decision, PermissionDeny)
