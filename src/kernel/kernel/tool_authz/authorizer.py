"""ToolAuthorizer — the Subsystem that owns ``authorize()``.

Wires together the four internal components (RuleStore, RuleEngine,
SessionGrantCache, BashClassifier) into the per-call decision flow
documented in ``docs/plans/landed/tool-authorizer.md`` § 4.3.

Degradation:
- LLMManager is optional at ``startup`` time (step 3 runs before
  step 4 Provider). BashClassifier is bound, but its LLM call is a
  stub (M2c integration) — it produces "unknown" and lets caller prompt.
- If ``bind_config`` fails (ConfigManager malformed), log + continue
  with empty rules; flag layer still applies.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, cast

from kernel.subsystem import Subsystem
from kernel.tool_authz.bash_classifier import BashClassifier
from kernel.tool_authz.config_section import PermissionsSection
from kernel.tool_authz.constants import SHELL_TOOL_NAMES
from kernel.tool_authz.rule_engine import EngineOutcome, RuleEngine
from kernel.tool_authz.rule_store import RuleStore
from kernel.tool_authz.session_grant_cache import SessionGrantCache
from kernel.tool_authz.types import (
    AuthorizeContext,
    PermissionAllow,
    PermissionAsk,
    PermissionDecision,
    PermissionDeny,
    PermissionRule,
    PermissionSuggestionBtn,
    ReasonBashClassifier,
    ReasonDefaultRisk,
    ReasonFailClosed,
    ReasonMode,
    ReasonNoPrompt,
    ReasonRuleMatched,
    ReasonSessionGrant,
)

if TYPE_CHECKING:
    from kernel.hooks import HookManager
    from kernel.module_table import KernelModuleTable
    from kernel.tools.tool import Tool

logger = logging.getLogger(__name__)


_CONFIG_FILE = "config"
_CONFIG_SECTION = "permissions"


class ToolAuthorizer(Subsystem):
    """Connection-level tool-call authorization.

    Lifespan step 3 — must start before Tools (step 5) and Session
    (step 10).  Failure-safe: if ``authorize()`` crashes, it returns a
    fail-closed deny; nothing the authorizer does can abort a call-site
    beyond the deny itself.
    """

    def __init__(self, module_table: KernelModuleTable) -> None:
        super().__init__(module_table)
        self._rule_store = RuleStore()
        self._rule_engine = RuleEngine()
        self._grant_cache = SessionGrantCache()
        self._bash_classifier = BashClassifier(prompts=module_table.prompts)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """Bind config section + wire RuleStore to its signal."""
        try:
            section = self._module_table.config.bind_section(
                file=_CONFIG_FILE,
                section=_CONFIG_SECTION,
                schema=PermissionsSection,
            )
        except Exception:
            logger.exception(
                "ToolAuthorizer: could not bind PermissionsSection — running with empty rules"
            )
            return

        self._rule_store.bind_config(section)

        # Pick up the bash classifier tuning.  The model itself is
        # resolved via ``LLMManager.model_for("bash_judge")`` when the
        # classifier actually calls the LLM (M2c).
        current = section.get()
        self._bash_classifier.enabled = current.bash_llm_judge_enabled
        self._bash_classifier.fail_closed = current.bash_llm_judge_fail_closed

        logger.info(
            "ToolAuthorizer started: %d rules loaded, bash_llm_judge=%s",
            len(self._rule_store.snapshot()),
            self._bash_classifier.enabled,
        )

    async def shutdown(self) -> None:
        """Drop session caches.  No persistent state to flush."""
        self._grant_cache.clear()
        logger.info("ToolAuthorizer: shutdown complete")

    # ------------------------------------------------------------------
    # Session lifecycle hooks — called by SessionHandler
    # ------------------------------------------------------------------

    def on_session_open(self, session_id: str) -> None:
        self._grant_cache.on_session_open(session_id)
        self._bash_classifier.on_session_open(session_id)

    def on_session_close(self, session_id: str) -> None:
        self._grant_cache.on_session_close(session_id)
        self._bash_classifier.on_session_close(session_id)

    # ------------------------------------------------------------------
    # Grant — called after an allow_always response
    # ------------------------------------------------------------------

    def grant(
        self,
        *,
        tool: Tool,
        tool_input: dict[str, Any],
        ctx: AuthorizeContext,
    ) -> None:
        """Register an ``allow_always`` grant for this exact call.

        Called by the Orchestrator's ToolExecutor after the Session's
        permission round-trip returns ``allow_always``.  Grant is scoped
        to ``ctx.session_id`` only; sub-agents use a derived session id
        and therefore see empty caches (aligned with CC).
        """
        self._grant_cache.grant(session_id=ctx.session_id, tool=tool, tool_input=tool_input)

    # ------------------------------------------------------------------
    # filter_denied_tools — pool-level deny filter for ToolManager.snapshot
    # ------------------------------------------------------------------

    def filter_denied_tools(self, tool_names: set[str]) -> set[str]:
        """Return the subset of ``tool_names`` blocked by a deny rule.

        Only tool-level and MCP server-level deny rules participate
        (content-scoped rules still evaluate at ``authorize()`` time
        because they depend on the specific input).

        Defense-in-depth with ``authorize()``: denied tools don't reach
        the LLM at all (aligned with CC ``tools.ts:262``); ``authorize()``
        catches the case where a rule is added mid-session after the
        snapshot was built.
        """
        denied: set[str] = set()
        rules = self._rule_store.snapshot()
        for rule in rules:
            if rule.behavior != "deny":
                continue
            if rule.value.rule_content is not None:
                # Content-scoped rules can't be evaluated without an
                # actual tool_input — skip at pool-filter time.
                continue
            for name in tool_names:
                if _rule_blocks_tool_name(rule, name):
                    denied.add(name)
        return denied

    # ------------------------------------------------------------------
    # authorize — the main decision flow
    # ------------------------------------------------------------------

    async def authorize(
        self,
        *,
        tool: Tool,
        tool_input: dict[str, Any],
        ctx: AuthorizeContext,
    ) -> PermissionDecision:
        """Decide whether a tool call may proceed.

        See ``docs/plans/landed/tool-authorizer.md`` § 4.3 for the full
        flow.  Summary:

        0. Short-circuit 1 — session grant cache hit → allow.
        1. Mode override — ``plan`` denies mutating tools, ``bypass`` allows.
        2. RuleEngine.decide — rule scan + Tool contract consults.
        3. Arbitration — deny > ask > allow priority.
        4. If final decision is ask:
           a. ``should_avoid_prompts`` → deny.
           b. BashClassifier LLMJudge (stub in Phase 1).
           c. Build suggestions — filter out ``allow_always`` for
              destructive tools.
        5. Fail-closed wrapper — any unexpected exception → deny.
        """
        try:
            decision = await self._authorize_impl(tool=tool, tool_input=tool_input, ctx=ctx)
        except Exception:
            logger.exception("ToolAuthorizer: unexpected exception during authorize — fail-closed")
            decision = PermissionDeny(
                message="permission check failed",
                decision_reason=ReasonFailClosed(error_class="authorize_crash"),
            )

        # Fire observer hooks *after* the decision is finalised.  Non-allow
        # outcomes get permission_denied / permission_requested notifications
        # so operator tooling (audit, Slack alerts, ...) can react.  Allow
        # decisions stay silent — if they need observability, the
        # pre_tool_use / post_tool_use fire points downstream already cover it.
        try:
            await self._fire_permission_hook(decision=decision, tool=tool, ctx=ctx)
        except Exception:
            # Hook fire failures must never corrupt the authorize decision.
            logger.exception("permission hook fire failed — returning original decision")
        return decision

    async def _authorize_impl(
        self,
        *,
        tool: Tool,
        tool_input: dict[str, Any],
        ctx: AuthorizeContext,
    ) -> PermissionDecision:
        # -- Short-circuit 1: session grant cache --------------------
        cached_grant = self._grant_cache.check(
            session_id=ctx.session_id, tool=tool, tool_input=tool_input
        )
        if cached_grant is not None:
            return PermissionAllow(
                decision_reason=ReasonSessionGrant(
                    granted_at=cached_grant.granted_at,
                    signature=cached_grant.signature,
                )
            )

        # -- Mode overrides ------------------------------------------
        from kernel.orchestrator.types import ToolKind  # lazy — avoids circular import

        if ctx.mode == "plan" and _is_mutating(tool.kind):
            # Gap 4: Allow writing to the session's plan file even in plan mode.
            if _is_plan_file_write(tool, tool_input, ctx):
                return PermissionAllow(
                    decision_reason=ReasonMode(mode="plan"),
                )
            return PermissionDeny(
                message="plan mode forbids side effects",
                decision_reason=ReasonMode(mode="plan"),
            )
        if ctx.mode == "accept_edits" and tool.kind == ToolKind.edit:
            return PermissionAllow(decision_reason=ReasonMode(mode="accept_edits"))
        if ctx.mode == "bypass":
            return PermissionAllow(decision_reason=ReasonMode(mode="bypass"))

        # -- Rule engine ---------------------------------------------
        rules = self._rule_store.snapshot()
        outcome = self._rule_engine.decide(rules, tool, tool_input, ctx)
        return await self._apply_outcome(outcome=outcome, tool=tool, tool_input=tool_input, ctx=ctx)

    async def _apply_outcome(
        self,
        *,
        outcome: EngineOutcome,
        tool: Tool,
        tool_input: dict[str, Any],
        ctx: AuthorizeContext,
    ) -> PermissionDecision:
        """Turn an ``EngineOutcome`` into a ``PermissionDecision``.

        Arbitration priority:
            rule.deny > suggestion.deny
          > rule.ask ∨ suggestion.ask
          > rule.allow > suggestion.allow
          > fallback ask
        """
        suggestion = outcome.suggestion
        matched = outcome.matched_rule

        # 1. Deny wins.
        if outcome.rule_behavior == "deny":
            assert matched is not None
            return PermissionDeny(
                message=f"tool call denied by rule {matched.rule_id}",
                decision_reason=_rule_reason(matched),
            )
        if suggestion.default_decision == "deny":
            return PermissionDeny(
                message=f"tool call denied: {suggestion.reason}",
                decision_reason=ReasonDefaultRisk(
                    risk=suggestion.risk,
                    reason=suggestion.reason,
                    tool_name=tool.name,
                ),
            )

        # 2. Ask (rule ask OR suggestion ask OR fallback).
        if outcome.rule_behavior == "ask" or suggestion.default_decision == "ask":
            return await self._handle_ask(
                outcome=outcome, tool=tool, tool_input=tool_input, ctx=ctx
            )

        # 3. Allow.
        if outcome.rule_behavior == "allow":
            assert matched is not None
            return PermissionAllow(decision_reason=_rule_reason(matched))
        if suggestion.default_decision == "allow":
            return PermissionAllow(
                decision_reason=ReasonDefaultRisk(
                    risk=suggestion.risk,
                    reason=suggestion.reason,
                    tool_name=tool.name,
                )
            )

        # 4. Fallback — nothing matched, default to asking.
        return await self._handle_ask(outcome=outcome, tool=tool, tool_input=tool_input, ctx=ctx)

    async def _handle_ask(
        self,
        *,
        outcome: EngineOutcome,
        tool: Tool,
        tool_input: dict[str, Any],
        ctx: AuthorizeContext,
    ) -> PermissionDecision:
        """Apply ``should_avoid_prompts`` + BashClassifier, then build
        the ``PermissionAsk`` if we're still asking."""

        # (0) Auto mode — auto-allow low-risk calls without prompting.
        if ctx.mode == "auto" and outcome.suggestion.risk == "low":
            return PermissionAllow(decision_reason=ReasonMode(mode="auto"))

        # (0b) dont_ask mode — only pre-approved (allow-rule) tools execute;
        #      everything that reaches _handle_ask is denied.  User-initiated
        #      counterpart of should_avoid_prompts (system-initiated).
        if ctx.mode == "dont_ask":
            return PermissionDeny(
                message="dont_ask mode: only pre-approved tools are allowed",
                decision_reason=ReasonMode(mode="dont_ask"),
            )

        # (a) should_avoid_prompts — convert ask to deny if no human online.
        if ctx.should_avoid_prompts:
            return PermissionDeny(
                message="no interactive channel available for permission request",
                decision_reason=ReasonNoPrompt(),
            )

        # (b) BashClassifier speculative LLMJudge.
        if tool.name in SHELL_TOOL_NAMES and self._bash_classifier.enabled:
            command = str(tool_input.get("command", ""))
            llm_manager, model_ref = self._resolve_bash_judge_model()
            verdict = await self._bash_classifier.classify(
                session_id=ctx.session_id,
                command=command,
                cwd=str(ctx.cwd),
                llm_manager=llm_manager,
                model_ref=model_ref,
            )
            if verdict == "safe":
                return PermissionAllow(
                    decision_reason=ReasonBashClassifier(verdict="safe", model_used=model_ref),
                )
            if verdict == "unsafe":
                return PermissionDeny(
                    message="bash command classified as unsafe",
                    decision_reason=ReasonBashClassifier(verdict="unsafe", model_used=model_ref),
                )
            # "unknown" / "budget_exceeded" → fall through to asking the user.

        # (c) Build suggestions — filter allow_always for destructive tools.
        suggestions: list[PermissionSuggestionBtn] = [
            PermissionSuggestionBtn(label="Allow once", outcome="allow_once"),
        ]
        if not outcome.is_destructive:
            suggestions.append(
                PermissionSuggestionBtn(label="Allow always", outcome="allow_always")
            )
        suggestions.append(PermissionSuggestionBtn(label="Deny", outcome="deny"))

        reason = (
            _rule_reason(outcome.matched_rule)
            if outcome.matched_rule is not None
            else ReasonDefaultRisk(
                risk=outcome.suggestion.risk,
                reason=outcome.suggestion.reason,
                tool_name=tool.name,
            )
        )

        return PermissionAsk(
            message=_build_ask_message(tool, tool_input, outcome.suggestion.reason),
            decision_reason=reason,
            suggestions=suggestions,
        )

    # ------------------------------------------------------------------
    # Hook fire — observer events (permission_denied / _requested)
    # ------------------------------------------------------------------

    async def _fire_permission_hook(
        self,
        *,
        decision: PermissionDecision,
        tool: Tool,
        ctx: AuthorizeContext,
    ) -> None:
        """Emit ``permission_denied`` / ``permission_requested`` events.

        Both events are ``can_block=False`` / ``accepts_input_mutation=False``
        — pure observer notifications.  ``ctx.messages`` appended by
        handlers is **dropped** (authorizer has no session-level
        ``queue_reminders`` wiring and the event semantics in
        [hook-manager.md §6.C](../../../docs/plans/landed/hook-manager.md)
        explicitly exclude reminder scheduling from permission events).

        Allow decisions do not fire — tool-lifecycle hooks
        (``pre_tool_use`` / ``post_tool_use``) handle that observability.
        """
        from kernel.hooks import AmbientContext, HookEvent, HookEventCtx

        hooks = self._get_hooks()
        if hooks is None:
            return

        if isinstance(decision, PermissionDeny):
            event = HookEvent.PERMISSION_DENIED
            error_message = decision.message
        elif isinstance(decision, PermissionAsk):
            event = HookEvent.PERMISSION_REQUESTED
            error_message = decision.message
        else:
            return  # Allow — silent

        ambient = AmbientContext(
            session_id=ctx.session_id,
            cwd=ctx.cwd,
            agent_depth=ctx.agent_depth,
            mode=ctx.mode,
            timestamp=time.time(),
        )
        hook_ctx = HookEventCtx(
            event=event,
            ambient=ambient,
            tool_name=tool.name,
            error_message=error_message,
        )
        await hooks.fire(hook_ctx)

    def _get_hooks(self) -> HookManager | None:
        """Look up HookManager lazily.

        ToolAuthorizer loads at step 3 but HookManager at step 7; resolving
        on first fire avoids a load-time dependency and supports degraded
        mode (HookManager failed to start → resolve returns None).
        """
        try:
            from kernel.hooks import HookManager

            return cast("HookManager | None", self._module_table.get(HookManager))
        except (KeyError, ImportError):
            return None

    def _resolve_bash_judge_model(self) -> tuple[Any, Any]:
        """Look up the ``bash_judge`` role on LLMManager.

        Returns ``(llm_manager, model_ref)``.  Either element being
        ``None`` disables the LLMJudge path for this call — classifier
        falls back to "unknown" → user gets prompted.

        Authorizer loads at step 3 and LLMManager at step 4, so lookup
        must be lazy (module_table.get at call time, not startup).
        Covers three failure modes with the same None-tuple response:
        LLMManager missing, ``bash_judge`` role unset (``KeyError``),
        config resolution exception.
        """
        try:
            from kernel.llm import LLMManager

            llm_manager = self._module_table.get(LLMManager)
        except (KeyError, ImportError):
            return None, None
        try:
            model_ref = llm_manager.model_for("bash_judge")
        except KeyError:
            return None, None
        except Exception:
            logger.exception("Unexpected error resolving bash_judge model")
            return None, None
        return llm_manager, model_ref


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rule_reason(rule: PermissionRule) -> ReasonRuleMatched:
    """Build a ``ReasonRuleMatched`` from a matched rule."""
    layer = cast(Any, rule.source.value)
    return ReasonRuleMatched(
        rule_id=rule.rule_id,
        rule_behavior=rule.behavior,
        matched_pattern=rule.raw_dsl,
        layer=layer,
    )


def _is_plan_file_write(tool: Tool, tool_input: dict[str, Any], ctx: Any) -> bool:
    """True if this tool call is writing to the session's plan file.

    Only FileEdit and FileWrite target a ``file_path`` parameter.
    We check if that path resolves to the session's plan file.
    """
    from kernel.plans import is_session_plan_file

    file_path = tool_input.get("file_path")
    if not file_path or not isinstance(file_path, str):
        return False
    if tool.name not in ("FileEdit", "FileWrite"):
        return False
    return is_session_plan_file(file_path, ctx.session_id)


def _is_mutating(kind: Any) -> bool:
    """True when ``kind`` is a write-category ToolKind."""
    from kernel.orchestrator.types import ToolKind

    return kind in {ToolKind.edit, ToolKind.delete, ToolKind.move, ToolKind.execute}


def _build_ask_message(tool: Tool, tool_input: dict[str, Any], reason: str) -> str:
    """One-line user-facing explanation of the pending call."""
    desc = tool.user_facing_name(tool_input)
    hint = tool.activity_description(tool_input)
    base = f"{desc}: {hint} ({reason})" if hint else f"{desc} ({reason})"

    warning = tool.destructive_warning(tool_input)
    if warning:
        return f"{base} — ⚠ {warning}"
    return base


def _rule_blocks_tool_name(rule: PermissionRule, tool_name: str) -> bool:
    """True when ``rule`` is a tool-level deny that blocks ``tool_name``.

    Covers:
    - primary-name equality
    - MCP server-level rule ``"mcp__slack"`` matches ``"mcp__slack__*"``
    - wildcard ``"mcp__*"`` matches every MCP tool
    """
    target = rule.value.tool_name
    if target == tool_name:
        return True
    if target == "mcp__*":
        return tool_name.startswith("mcp__")
    if target.startswith("mcp__") and "__" not in target[len("mcp__") :]:
        return tool_name.startswith(target + "__")
    return False


__all__ = ["ToolAuthorizer"]
