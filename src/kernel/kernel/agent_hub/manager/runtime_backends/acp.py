"""ACP runtime backend controller."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4


class AcpRuntime(Protocol):
    async def initialize(self) -> dict[str, Any]: ...
    async def new_session(self, *, cwd: str) -> str: ...
    async def prompt(self, *, session_id: str, text: str) -> Any: ...
    async def cancel(self, *, session_id: str) -> None: ...
    async def close_session(self, *, session_id: str) -> dict[str, Any]: ...
    async def close(self) -> None: ...


@dataclass
class FakeAcpRuntime:
    """Deterministic in-process ACP runtime used by probes."""

    crash_on_prompt: bool = False
    permission_requests: list[dict[str, Any]] = field(default_factory=list)
    sessions: list[str] = field(default_factory=list)
    closed: bool = False
    cancelled: list[str] = field(default_factory=list)

    async def initialize(self) -> dict[str, Any]:
        return {"protocolVersion": 1, "runtime": "fake-acp"}

    async def new_session(self, *, cwd: str) -> str:
        session_id = f"acp-{uuid4().hex}"
        self.sessions.append(session_id)
        return session_id

    async def prompt(self, *, session_id: str, text: str) -> dict[str, Any]:
        if self.crash_on_prompt:
            raise RuntimeError("fake ACP runtime crashed")
        if "permission" in text:
            self.permission_requests.append(
                {"sessionId": session_id, "method": "session/request_permission", "tool": "fake"}
            )
        return {
            "stopReason": "end_turn",
            "updates": [{"sessionUpdate": "agent_message_chunk", "text": f"Echo: {text}"}],
        }

    async def cancel(self, *, session_id: str) -> None:
        self.cancelled.append(session_id)

    async def close_session(self, *, session_id: str) -> dict[str, Any]:
        return {"closed": True, "sessionId": session_id}

    async def close(self) -> None:
        self.closed = True


class AcpRuntimeController:
    """Lifecycle wrapper for one ACP runtime process/session."""

    def __init__(self, runtime: AcpRuntime) -> None:
        self._runtime = runtime
        self._initialized = False
        self._sessions: set[str] = set()
        self._last_error: str | None = None

    async def initialize(self) -> dict[str, Any]:
        result = await self._runtime.initialize()
        self._initialized = True
        return result

    async def new(self, *, cwd: str) -> dict[str, Any]:
        if not self._initialized:
            await self.initialize()
        session_id = await self._runtime.new_session(cwd=cwd)
        self._sessions.add(session_id)
        return {"sessionId": session_id, "status": "running"}

    async def prompt(self, *, session_id: str, text: str) -> dict[str, Any]:
        try:
            raw = await self._runtime.prompt(session_id=session_id, text=text)
        except Exception as exc:
            self._last_error = str(exc)
            return {"success": False, "status": "failed", "error": str(exc)}
        result = _normalize_prompt_result(raw)
        return {"success": True, "status": "completed", **result}

    async def cancel(self, *, session_id: str) -> dict[str, Any]:
        await self._runtime.cancel(session_id=session_id)
        return {"success": True, "status": "cancelled", "sessionId": session_id}

    async def close_session(self, *, session_id: str) -> dict[str, Any]:
        result = await self._runtime.close_session(session_id=session_id)
        self._sessions.discard(session_id)
        return {"success": True, **result}

    async def close(self) -> dict[str, Any]:
        await self._runtime.close()
        self._sessions.clear()
        return {"success": True, "closed": True}

    def status(self) -> dict[str, Any]:
        return {
            "initialized": self._initialized,
            "sessions": sorted(self._sessions),
            "lastError": self._last_error,
        }


__all__ = ["AcpRuntimeController", "FakeAcpRuntime"]


def _normalize_prompt_result(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    stop_reason = getattr(raw, "stop_reason", None)
    updates = getattr(raw, "updates", None)
    if stop_reason is not None:
        return {
            "stopReason": str(stop_reason),
            "updates": list(updates or ()),
        }
    return {"result": raw}
