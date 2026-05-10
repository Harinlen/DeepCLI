"""SessionHandler — the contract the session layer must fulfil.

The protocol layer routes every inbound ACP method to one of these
methods after deserialising the params into the corresponding contract
type.  The session layer returns a typed result; the protocol layer
serialises it back into a JSON-RPC response frame.

Isolation guarantee
-------------------
Implementations MUST NOT import anything from ``kernel.core.protocol.acp``
or reference JSON-RPC concepts.  The seam is purely Pydantic objects
in, Pydantic objects out.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from kernel.core.protocol.interfaces.contracts.archive_session_params import ArchiveSessionParams
from kernel.core.protocol.interfaces.contracts.archive_session_result import ArchiveSessionResult
from kernel.core.protocol.interfaces.contracts.activate_skill_params import ActivateSkillParams
from kernel.core.protocol.interfaces.contracts.cancel_params import CancelParams
from kernel.core.protocol.interfaces.contracts.cancel_execution_params import CancelExecutionParams
from kernel.core.protocol.interfaces.contracts.close_session_params import CloseSessionParams
from kernel.core.protocol.interfaces.contracts.close_session_result import CloseSessionResult
from kernel.core.protocol.interfaces.contracts.delete_session_params import DeleteSessionParams
from kernel.core.protocol.interfaces.contracts.delete_session_result import DeleteSessionResult
from kernel.core.protocol.interfaces.contracts.execute_python_params import ExecutePythonParams
from kernel.core.protocol.interfaces.contracts.execute_shell_params import ExecuteShellParams
from kernel.core.protocol.interfaces.contracts.execution_result import ExecutionResult
from kernel.core.protocol.interfaces.contracts.get_usage_params import GetUsageParams
from kernel.core.protocol.interfaces.contracts.get_usage_result import GetUsageResult
from kernel.core.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.core.protocol.interfaces.contracts.list_sessions_params import (
    ListSessionsParams,
)
from kernel.core.protocol.interfaces.contracts.list_sessions_result import (
    ListSessionsResult,
)
from kernel.core.protocol.interfaces.contracts.load_session_params import (
    LoadSessionParams,
)
from kernel.core.protocol.interfaces.contracts.load_session_result import (
    LoadSessionResult,
)
from kernel.core.protocol.interfaces.contracts.new_session_params import (
    NewSessionParams,
)
from kernel.core.protocol.interfaces.contracts.new_session_result import (
    NewSessionResult,
)
from kernel.core.protocol.interfaces.contracts.prompt_params import PromptParams
from kernel.core.protocol.interfaces.contracts.prompt_result import PromptResult
from kernel.core.protocol.interfaces.contracts.rename_session_params import RenameSessionParams
from kernel.core.protocol.interfaces.contracts.rename_session_result import RenameSessionResult
from kernel.core.protocol.interfaces.contracts.resume_session_params import ResumeSessionParams
from kernel.core.protocol.interfaces.contracts.resume_session_result import ResumeSessionResult
from kernel.core.protocol.interfaces.contracts.set_config_option_params import (
    SetConfigOptionParams,
)
from kernel.core.protocol.interfaces.contracts.set_config_option_result import (
    SetConfigOptionResult,
)
from kernel.core.protocol.interfaces.contracts.set_mode_params import SetModeParams
from kernel.core.protocol.interfaces.contracts.set_mode_result import SetModeResult


@runtime_checkable
class SessionHandler(Protocol):
    """Contract implemented by ``SessionManager``."""

    async def new(self, ctx: HandlerContext, params: NewSessionParams) -> NewSessionResult:
        """Create a new session and return its id."""
        ...

    async def load_session(
        self, ctx: HandlerContext, params: LoadSessionParams
    ) -> LoadSessionResult:
        """Resume an existing session, replaying history via ``ctx.sender``."""
        ...

    async def resume_session(
        self, ctx: HandlerContext, params: ResumeSessionParams
    ) -> ResumeSessionResult:
        """Reattach to an existing session without replaying history."""
        ...

    async def list(self, ctx: HandlerContext, params: ListSessionsParams) -> ListSessionsResult:
        """Return a paginated list of sessions."""
        ...

    async def prompt(self, ctx: HandlerContext, params: PromptParams) -> PromptResult:
        """Process a user prompt turn.

        Streams ``session/update`` notifications via ``ctx.sender``
        during processing.  When cancelled, MUST catch
        ``asyncio.CancelledError`` and return
        ``PromptResult(stop_reason="cancelled")`` — never let the
        exception propagate as a JSON-RPC error.
        """
        ...

    async def activate_skill(
        self, ctx: HandlerContext, params: ActivateSkillParams
    ) -> PromptResult:
        """Activate a user-invocable skill, then process the resulting prompt turn."""
        ...

    async def execute_shell(
        self, ctx: HandlerContext, params: ExecuteShellParams
    ) -> ExecutionResult:
        """Execute a user-triggered shell command for a session."""
        ...

    async def execute_python(
        self, ctx: HandlerContext, params: ExecutePythonParams
    ) -> ExecutionResult:
        """Execute user-triggered Python code for a session."""
        ...

    async def cancel_execution(self, ctx: HandlerContext, params: CancelExecutionParams) -> None:
        """Cancel in-flight user REPL execution for a session."""
        ...

    async def set_mode(self, ctx: HandlerContext, params: SetModeParams) -> SetModeResult:
        """Switch the session to a different operating mode."""
        ...

    async def set_config_option(
        self, ctx: HandlerContext, params: SetConfigOptionParams
    ) -> SetConfigOptionResult:
        """Update a session configuration option and return full config state."""
        ...

    async def rename_session(
        self, ctx: HandlerContext, params: RenameSessionParams
    ) -> RenameSessionResult:
        """Rename a session and return its updated summary."""
        ...

    async def archive_session(
        self, ctx: HandlerContext, params: ArchiveSessionParams
    ) -> ArchiveSessionResult:
        """Archive or unarchive a session and return its updated summary."""
        ...

    async def delete_session(
        self, ctx: HandlerContext, params: DeleteSessionParams
    ) -> DeleteSessionResult:
        """Permanently delete a session."""
        ...

    async def get_usage(
        self, ctx: HandlerContext, params: GetUsageParams
    ) -> GetUsageResult:
        """Return context and token usage for one session."""
        ...

    async def close_session(
        self, ctx: HandlerContext, params: CloseSessionParams
    ) -> CloseSessionResult:
        """Release active runtime resources while preserving durable state."""
        ...

    async def cancel(self, ctx: HandlerContext, params: CancelParams) -> None:
        """Handle ``session/cancel``.

        Locate the in-flight ``session/prompt`` task for
        ``params.session_id`` and call ``task.cancel()``.
        This is a notification so no return value is expected.
        """
        ...

    async def on_disconnect(self, connection_id: str) -> None:
        """Called by the protocol layer when a WebSocket connection closes.

        Remove the connection from any sessions it was observing.
        In-flight turns are NOT cancelled — they continue running and
        write events to JSONL; the client can replay on reconnect.
        """
        ...
