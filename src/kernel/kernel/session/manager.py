"""Public SessionManager facade for the session subsystem."""

from __future__ import annotations

from kernel.session.client_stream.broadcast import SessionBroadcastMixin
from kernel.session.client_stream.event_mapper import SessionEventMapperMixin
from kernel.session.persistence.event_writer import SessionEventWriterMixin
from kernel.session.api.gateway import SessionGatewayMixin
from kernel.session.api.handlers import SessionHandlerMixin
from kernel.session.lifecycle.runtime import SessionLifecycleMixin
from kernel.session.lifecycle.load import SessionLoaderMixin
from kernel.session.orchestration.factory import SessionOrchestratorFactoryMixin
from kernel.session.turns.permission import SessionPermissionMixin
from kernel.session.client_stream.replay import SessionReplayMixin
from kernel.session.turns.runner import SessionTurnRunnerMixin
from kernel.session.user_repl import UserReplMixin
from kernel.session.context import AgentContext
from kernel.subsystem import Subsystem


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
