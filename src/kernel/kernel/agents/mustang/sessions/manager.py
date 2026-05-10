"""Public SessionManager facade for the session subsystem."""

from __future__ import annotations

from kernel.agents.mustang.sessions.client_stream.broadcast import SessionBroadcastMixin
from kernel.agents.mustang.sessions.client_stream.event_mapper import SessionEventMapperMixin
from kernel.agents.mustang.sessions.persistence.event_writer import SessionEventWriterMixin
from kernel.agents.mustang.sessions.api.gateway import SessionGatewayMixin
from kernel.agents.mustang.sessions.api.handlers import SessionHandlerMixin
from kernel.agents.mustang.sessions.lifecycle.runtime import SessionLifecycleMixin
from kernel.agents.mustang.sessions.lifecycle.load import SessionLoaderMixin
from kernel.agents.mustang.sessions.orchestration.factory import SessionOrchestratorFactoryMixin
from kernel.agents.mustang.sessions.turns.permission import SessionPermissionMixin
from kernel.agents.mustang.sessions.client_stream.replay import SessionReplayMixin
from kernel.agents.mustang.sessions.turns.runner import SessionTurnRunnerMixin
from kernel.agents.mustang.sessions.user_repl import UserReplMixin
from kernel.agents.mustang.sessions.context import AgentContext
from kernel.core.lifecycle import Subsystem


class SessionManager(
    SessionLifecycleMixin,
    SessionGatewayMixin,
    UserReplMixin,
    SessionHandlerMixin,
    SessionTurnRunnerMixin,
    SessionEventMapperMixin,
    SessionPermissionMixin,
    SessionBroadcastMixin,
    SessionReplayMixin,
    SessionEventWriterMixin,
    SessionLoaderMixin,
    SessionOrchestratorFactoryMixin,
    Subsystem,
):
    """Manage session lifecycle, persistence, prompt turns, and broadcast."""

    def __init__(
        self,
        module_table,
        *,
        agent_context: AgentContext | None = None,
    ) -> None:
        super().__init__(module_table)
        self._agent_context = agent_context
