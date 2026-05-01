"""Session mixin for user-triggered ``!`` shell and ``$`` Python execution."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from kernel.protocol.acp.namespaces import MustangMethod
from kernel.protocol.acp.schemas.updates import MustangExecution, MustangExecutionUpdateNotification
from kernel.protocol.interfaces.contracts.cancel_execution_params import CancelExecutionParams
from kernel.protocol.interfaces.contracts.execute_python_params import ExecutePythonParams
from kernel.protocol.interfaces.contracts.execute_shell_params import ExecuteShellParams
from kernel.protocol.interfaces.contracts.execution_result import ExecutionResult
from kernel.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.protocol.interfaces.errors import InternalError
from kernel.session._shared.base import _SessionMixinBase
from kernel.session.events import ConversationMessageEvent
from kernel.session.message_serde import serialize_message
from kernel.session.runtime.state import Session
from kernel.tools import ToolManager
from kernel.tools.context import ToolContext
from kernel.tools.types import ToolCallProgress, ToolCallResult, ToolInputError

logger = logging.getLogger("kernel.session.user_repl")


class UserReplMixin(_SessionMixinBase):
    """ACP handlers for user REPL execution."""

    async def execute_shell(
        self, ctx: HandlerContext, params: ExecuteShellParams
    ) -> ExecutionResult:
        session = await self._get_or_load(params.session_id)
        return await self._execute_user_tool(
            ctx,
            session,
            kind="shell",
            input_text=params.command,
            tool_names=_shell_tool_names(params.shell),
            tool_input={"command": params.command},
            exclude_from_context=params.exclude_from_context,
            shell=params.shell,
        )

    async def execute_python(
        self, ctx: HandlerContext, params: ExecutePythonParams
    ) -> ExecutionResult:
        session = await self._get_or_load(params.session_id)
        return await self._execute_user_tool(
            ctx,
            session,
            kind="python",
            input_text=params.code,
            tool_names=("Python",),
            tool_input={"code": params.code},
            exclude_from_context=params.exclude_from_context,
            shell=None,
        )

    async def cancel_execution(
        self, ctx: HandlerContext, params: CancelExecutionParams
    ) -> None:
        session = self._sessions.get(params.session_id)
        if session is None:
            return
        for execution_id, task in list(session.user_executions.items()):
            kind = execution_id.split(":", 1)[0]
            if params.kind == "any" or params.kind == kind:
                task.cancel()

    async def _execute_user_tool(
        self,
        ctx: HandlerContext,
        session: Session,
        *,
        kind: str,
        input_text: str,
        tool_names: tuple[str, ...],
        tool_input: dict[str, Any],
        exclude_from_context: bool,
        shell: str | None,
    ) -> ExecutionResult:
        tool_manager = self._module_table.get(ToolManager)
        tool = next((tool_manager.lookup(name) for name in tool_names if tool_manager.lookup(name)), None)
        if tool is None:
            raise InternalError(f"no tool registered for user {kind} execution")

        execution_id = f"{kind}:{uuid.uuid4()}"
        cancel_event = asyncio.Event()
        tool_ctx = ToolContext(
            session_id=session.session_id,
            agent_depth=session.subagent_depth,
            agent_id=None,
            cwd=session.cwd,
            cancel_event=cancel_event,
            file_state=tool_manager.file_state(),
            tasks=session.task_registry,
        )

        await self._broadcast_execution_update(
            session,
            MustangExecution(
                phase="start",
                kind=kind,  # type: ignore[arg-type]
                execution_id=execution_id,
                input=input_text,
                shell=shell,
                exclude_from_context=exclude_from_context,
            ),
        )

        task = asyncio.current_task()
        if task is not None:
            session.user_executions[execution_id] = task

        exit_code = 0
        cancelled = False
        saw_progress = False
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        try:
            await tool.validate_input(tool_input, tool_ctx)
            async for event in tool.call(tool_input, tool_ctx):
                if isinstance(event, ToolCallProgress):
                    text = _progress_text(event)
                    if text:
                        saw_progress = True
                        stdout_parts.append(text)
                        await self._broadcast_execution_update(
                            session,
                            MustangExecution(
                                phase="chunk",
                                kind=kind,  # type: ignore[arg-type]
                                execution_id=execution_id,
                                stream="stdout",
                                text=text,
                            ),
                        )
                elif isinstance(event, ToolCallResult):
                    data = event.data if isinstance(event.data, dict) else {}
                    exit_code = int(data.get("exit_code") or 0)
                    stdout = str(data.get("stdout") or "")
                    stderr = str(data.get("stderr") or "")
                    if stdout and not saw_progress:
                        stdout_parts.append(stdout)
                        await self._broadcast_execution_update(
                            session,
                            MustangExecution(
                                phase="chunk",
                                kind=kind,  # type: ignore[arg-type]
                                execution_id=execution_id,
                                stream="stdout",
                                text=stdout,
                            ),
                        )
                    if stderr and not saw_progress:
                        stderr_parts.append(stderr)
                        await self._broadcast_execution_update(
                            session,
                            MustangExecution(
                                phase="chunk",
                                kind=kind,  # type: ignore[arg-type]
                                execution_id=execution_id,
                                stream="stderr",
                                text=stderr,
                            ),
                        )
        except ToolInputError as exc:
            exit_code = 1
            stderr_parts.append(f"Input validation failed: {exc}")
            await self._broadcast_execution_update(
                session,
                MustangExecution(
                    phase="chunk",
                    kind=kind,  # type: ignore[arg-type]
                    execution_id=execution_id,
                    stream="stderr",
                    text=f"Input validation failed: {exc}",
                ),
            )
        except asyncio.CancelledError:
            cancelled = True
            exit_code = -1
        finally:
            cancel_event.set()
            session.user_executions.pop(execution_id, None)
            if not exclude_from_context:
                await self._append_user_execution_context(
                    session,
                    kind=kind,
                    input_text=input_text,
                    stdout="".join(stdout_parts),
                    stderr="".join(stderr_parts),
                    exit_code=exit_code,
                    cancelled=cancelled,
                )
            await self._broadcast_execution_update(
                session,
                MustangExecution(
                    phase="end",
                    kind=kind,  # type: ignore[arg-type]
                    execution_id=execution_id,
                    exit_code=exit_code,
                    cancelled=cancelled,
                ),
            )

        return ExecutionResult(exit_code=exit_code, cancelled=cancelled)

    async def _broadcast_execution_update(
        self,
        session: Session,
        execution: MustangExecution,
    ) -> None:
        """Fan a DeepCLI user-execution extension notification to observers."""
        if not session.senders:
            return
        notification = MustangExecutionUpdateNotification(
            session_id=session.session_id,
            execution=execution,
            meta={"mustang.agent/version": 1},
        )
        dead: list[str] = []
        for conn_id, sender in list(session.senders.items()):
            try:
                await sender.notify(MustangMethod.SESSION_EXECUTION_UPDATE, notification)
            except Exception:
                logger.exception(
                    "session=%s conn=%s: user execution notify failed — removing",
                    session.session_id,
                    conn_id,
                )
                dead.append(conn_id)
        for conn_id in dead:
            session.senders.pop(conn_id, None)

    async def _append_user_execution_context(
        self,
        session: Session,
        *,
        kind: str,
        input_text: str,
        stdout: str,
        stderr: str,
        exit_code: int,
        cancelled: bool,
    ) -> None:
        """Append included user REPL output to future agent context."""
        append_context = getattr(session.orchestrator, "append_user_context", None)
        if append_context is None:
            return
        context_text = _context_text(
            kind=kind,
            input_text=input_text,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            cancelled=cancelled,
        )
        message = append_context(context_text)
        try:
            await self._write_event(
                session,
                ConversationMessageEvent,
                message=serialize_message(message),
            )
        except Exception:
            logger.warning(
                "session=%s: failed to persist user execution context",
                session.session_id,
                exc_info=True,
            )


def _progress_text(event: ToolCallProgress) -> str:
    parts: list[str] = []
    for block in event.content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _shell_tool_names(shell: str) -> tuple[str, ...]:
    if shell in ("bash", "sh"):
        return ("Bash",)
    if shell in ("pwsh", "powershell"):
        return ("PowerShell",)
    if shell == "cmd":
        return ("Cmd",)
    return ("Bash", "PowerShell", "Cmd")


def _context_text(
    *,
    kind: str,
    input_text: str,
    stdout: str,
    stderr: str,
    exit_code: int,
    cancelled: bool,
) -> str:
    label = "shell" if kind == "shell" else "python"
    parts = [f"<user_{label}_execution>", f"<input>\n{input_text}\n</input>"]
    if stdout:
        parts.append(f"<stdout>\n{stdout.rstrip()}\n</stdout>")
    if stderr:
        parts.append(f"<stderr>\n{stderr.rstrip()}\n</stderr>")
    status = "cancelled" if cancelled else f"exit_code={exit_code}"
    parts.append(f"<status>{status}</status>")
    parts.append(f"</user_{label}_execution>")
    return "\n".join(parts)
