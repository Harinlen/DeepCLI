"""User question request/response bridge for a single WebSocket session.

Mirrors :class:`PermissionHandler` but for structured user questions.
The LLM's ``ask_user_question`` tool triggers a ``UserQuestion``
event; the client replies with ``user_question_response``.
"""

from __future__ import annotations

import asyncio

from daemon.engine.stream import UserQuestionResponse


class QuestionHandler:
    """Manages question request/response round-trips over WebSocket."""

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[UserQuestionResponse]] = {}

    def create_waiter(self, request_id: str) -> asyncio.Future[UserQuestionResponse]:
        """Create a future that resolves when the client answers."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[UserQuestionResponse] = loop.create_future()
        self._pending[request_id] = future
        return future

    def resolve(self, request_id: str, response: UserQuestionResponse) -> bool:
        """Resolve a pending question request."""
        future = self._pending.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(response)
            return True
        return False

    @property
    def has_pending(self) -> bool:
        """Whether any questions are awaiting a response."""
        return any(not f.done() for f in self._pending.values())

    def cancel_all(self) -> None:
        """Cancel all pending questions (e.g. on disconnect)."""
        for request_id, future in self._pending.items():
            if not future.done():
                future.set_result(
                    UserQuestionResponse(request_id=request_id, answers={})
                )
        self._pending.clear()
