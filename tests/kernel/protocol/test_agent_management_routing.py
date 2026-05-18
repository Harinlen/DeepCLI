from __future__ import annotations

from types import SimpleNamespace

import pytest

from kernel.agent_hub import AgentHub, AgentHubManager
from kernel.agent_hub.contracts import default_primary_agent_definition
from kernel.core.protocol.acp.namespaces import MustangMethod
from kernel.core.protocol.acp.routing import (
    REQUEST_DISPATCH,
    _handle_agents_add,
    _handle_agents_bind,
    _handle_agents_list,
    _handle_channels_add,
    _handle_channels_resolve,
    _handle_channels_status,
)
from kernel.core.protocol.acp.schemas.agent_management import (
    AgentAddRequest,
    AgentBindRequest,
    EmptyManagementRequest,
    ChannelOperationRequest,
    ChannelResolveRequest,
)


def test_agent_and_channel_methods_are_routable() -> None:
    for method in (
        MustangMethod.AGENTS_LIST,
        MustangMethod.AGENTS_ADD,
        MustangMethod.AGENTS_SET_IDENTITY,
        MustangMethod.AGENTS_BINDINGS,
        MustangMethod.AGENTS_BIND,
        MustangMethod.AGENTS_UNBIND,
        MustangMethod.AGENTS_DELETE,
        MustangMethod.CHANNELS_LIST,
        MustangMethod.CHANNELS_STATUS,
        MustangMethod.CHANNELS_CAPABILITIES,
        MustangMethod.CHANNELS_RESOLVE,
        MustangMethod.CHANNELS_LOGS,
        MustangMethod.CHANNELS_ADD,
        MustangMethod.CHANNELS_REMOVE,
        MustangMethod.CHANNELS_LOGIN,
        MustangMethod.CHANNELS_LOGOUT,
        MustangMethod.CHANNELS_SETUP,
    ):
        assert method in REQUEST_DISPATCH


@pytest.mark.asyncio
async def test_agent_management_handlers_mutate_hub_and_refresh_snapshot(tmp_path) -> None:
    hub = AgentHub(
        manager=AgentHubManager(
            [default_primary_agent_definition(home=tmp_path, workspace=str(tmp_path))]
        )
    )

    added = await _handle_agents_add(
        hub,
        None,  # type: ignore[arg-type]
        AgentAddRequest(id="research", name="Research", workspace=str(tmp_path)),
    )
    assert added.agent["id"] == "research"  # type: ignore[index]
    assert hub.router.snapshot.revision == 1

    await _handle_agents_bind(
        hub,
        None,  # type: ignore[arg-type]
        AgentBindRequest.model_validate(
            {
                "agentId": "research",
                "adapterId": "discord-main",
                "platform": "discord",
                "accountId": "u1",
            }
        ),
    )
    listed = await _handle_agents_list(hub, None, EmptyManagementRequest())  # type: ignore[arg-type]
    assert any(agent["id"] == "research" for agent in listed.agents)
    assert hub.router.resolve_target(
        SimpleNamespace(
            target=SimpleNamespace(agent_id="research", route_key=None),
            caller=None,
        )
    )


@pytest.mark.asyncio
async def test_channel_handlers_read_gateway_manager() -> None:
    async def _add(adapter_id, payload):
        return {"adapterId": adapter_id, "type": payload["type"], "started": False}

    gm = SimpleNamespace(
        status_snapshots=lambda: [{"adapterId": "discord-main"}],
        resolve_channel_session=lambda adapter_id, peer_id, thread_id=None: "session-1",
        add_channel_config=_add,
    )

    status = await _handle_channels_status(gm, None, EmptyManagementRequest())  # type: ignore[arg-type]
    resolved = await _handle_channels_resolve(
        gm,
        None,  # type: ignore[arg-type]
        ChannelResolveRequest.model_validate(
            {"adapterId": "discord-main", "peerId": "u1", "threadId": "ch1"}
        ),
    )
    added = await _handle_channels_add(
        gm,
        None,  # type: ignore[arg-type]
        ChannelOperationRequest.model_validate(
            {"adapterId": "discord-main", "payload": {"type": "discord"}}
        ),
    )

    assert status.status == [{"adapterId": "discord-main"}]
    assert resolved.session_id == "session-1"
    assert added.ok is True
