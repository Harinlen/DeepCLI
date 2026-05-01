"""End-to-end integration tests for Phase 4 Batch 1.

Exercises the full daemon stack (FastAPI app + WebSocket + session
manager + orchestrator) with a stubbed provider, covering:

  - Step 4.7: git context injected into the system prompt
  - Step 4.5.1: cost_query returns accumulated per-model usage
  - Step 4.5: model_status / model_list / model_switch flow

These tests verify the interaction between layers, not individual
module behaviour (unit tests cover that).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from daemon.config.defaults import apply_defaults
from daemon.config.schema import ProviderRuntimeConfig, SourceConfig
from daemon.engine.stream import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    UsageInfo,
)
from daemon.engine.context import PromptSection, prompt_sections_to_text
from daemon.providers.base import Message, ModelInfo, Provider, ToolDefinition

AUTH_TOKEN = "e2e-token"


# ------------------------------------------------------------------
# Capturing provider — records system prompts + model passed each turn
# ------------------------------------------------------------------


class CapturingProvider(Provider):
    """Provider that records calls and yields a canned response.

    Each instance carries a ``name`` and a per-turn ``usage``.
    ``calls`` is a list of ``(system_text, model)`` tuples — one per query.
    """

    def __init__(self, name: str, input_tokens: int = 3, output_tokens: int = 2) -> None:
        self._name = name
        self._input = input_tokens
        self._output = output_tokens
        self.calls: list[tuple[str, str | None]] = []

    @property  # type: ignore[override]
    def name(self) -> str:
        return self._name

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        system: list[PromptSection] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        text = prompt_sections_to_text(system) if system else ""
        self.calls.append((text, model))
        yield TextDelta(content=f"reply from {self._name}")
        yield StreamEnd(usage=UsageInfo(input_tokens=self._input, output_tokens=self._output))

    async def models(self) -> list[ModelInfo]:
        return [ModelInfo(id=self._name, name=self._name, provider=self._name)]


# ------------------------------------------------------------------
# Fixtures — fresh registry + config per test
# ------------------------------------------------------------------


def _build_two_provider_config() -> Any:
    """Isolated RuntimeConfig with two providers: alpha (default) + beta."""
    config = apply_defaults(SourceConfig())
    config.providers = {
        "alpha": ProviderRuntimeConfig(
            type="openai_compatible",
            base_url="http://alpha",
            model="model-alpha",
            api_key="k1",
        ),
        "beta": ProviderRuntimeConfig(
            type="openai_compatible",
            base_url="http://beta",
            model="model-beta",
            api_key="k2",
        ),
    }
    config.default_provider = "alpha"
    return config


@pytest.fixture
def provider_pair() -> tuple[CapturingProvider, CapturingProvider]:
    """Shared pair of capturing providers used inside the fixture below."""
    return CapturingProvider("alpha"), CapturingProvider("beta")


@pytest.fixture
def client(
    tmp_path: Path,
    provider_pair: tuple[CapturingProvider, CapturingProvider],
) -> Any:
    """TestClient wired to a two-provider isolated config."""
    from daemon.providers.registry import ProviderRegistry

    alpha, beta = provider_pair

    def fake_load_config() -> Any:
        return _build_two_provider_config()

    def fake_from_config(_config: Any) -> Any:
        registry = ProviderRegistry()
        registry._default_provider = "alpha"
        registry.register(alpha)
        registry.register(beta)
        return registry

    with (
        patch("daemon.auth.AUTH_DIR", tmp_path),
        patch("daemon.auth.AUTH_TOKEN_PATH", tmp_path / ".auth_token"),
        patch("daemon.app.ensure_auth_token", return_value=AUTH_TOKEN),
        patch("daemon.app.load_config", side_effect=fake_load_config),
        patch(
            "daemon.app.ProviderRegistry.from_config",
            side_effect=fake_from_config,
        ),
    ):
        from daemon.app import create_app

        app = create_app()
        with TestClient(app) as c:
            yield c


def _consume_session_id(ws: Any) -> str:
    msg = ws.receive_json()
    assert msg["type"] == "session_id"
    return msg["session_id"]  # type: ignore[no-any-return]


def _drain_to_end(ws: Any) -> None:
    while True:
        m = ws.receive_json()
        if m["type"] == "end" or m["type"] == "error":
            return


# ------------------------------------------------------------------
# Step 4.7 — Git Context Injection
# ------------------------------------------------------------------


class TestE2EGitContext:
    """Git context reaches the system prompt of the active provider."""

    @pytest.fixture
    def git_cwd(self, tmp_path: Path) -> Path:
        """A real git repo used as the session cwd."""
        cwd = tmp_path / "repo"
        cwd.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=cwd, check=True)
        subprocess.run(["git", "config", "user.email", "e2e@test"], cwd=cwd, check=True)
        subprocess.run(["git", "config", "user.name", "E2E"], cwd=cwd, check=True)
        (cwd / "a.py").write_text("pass\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=cwd, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "e2e init"], cwd=cwd, check=True)
        return cwd

    def test_git_context_in_system_prompt(
        self,
        client: Any,
        provider_pair: tuple[CapturingProvider, CapturingProvider],
        git_cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After a chat turn, the captured system prompt contains git context."""
        alpha, _beta = provider_pair

        # Force orchestrator's cwd to the git repo by monkeypatching
        # Path.cwd() during session creation (session_manager reads cwd
        # at create() time).
        monkeypatch.setattr(Path, "cwd", classmethod(lambda _cls: git_cwd))

        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)
            ws.send_json({"type": "user_message", "content": "hi"})
            _drain_to_end(ws)

        assert alpha.calls, "provider was never called"
        system_prompt, _ = alpha.calls[0]
        assert "# Git Context" in system_prompt
        assert "Current branch: main" in system_prompt
        assert "e2e init" in system_prompt
        assert "Git user: E2E" in system_prompt

    def test_no_git_context_outside_repo(
        self,
        client: Any,
        provider_pair: tuple[CapturingProvider, CapturingProvider],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-git cwd → no Git Context section in the prompt."""
        alpha, _beta = provider_pair

        non_git = tmp_path / "plain"
        non_git.mkdir()
        monkeypatch.setattr(Path, "cwd", classmethod(lambda _cls: non_git))

        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)
            ws.send_json({"type": "user_message", "content": "hi"})
            _drain_to_end(ws)

        system_prompt, _ = alpha.calls[0]
        assert "# Git Context" not in system_prompt

    def test_git_context_memoized_across_turns(
        self,
        client: Any,
        provider_pair: tuple[CapturingProvider, CapturingProvider],
        git_cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Same prompt content across consecutive turns (snapshot semantics)."""
        alpha, _ = provider_pair
        monkeypatch.setattr(Path, "cwd", classmethod(lambda _cls: git_cwd))

        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)
            ws.send_json({"type": "user_message", "content": "q1"})
            _drain_to_end(ws)
            ws.send_json({"type": "user_message", "content": "q2"})
            _drain_to_end(ws)

        assert len(alpha.calls) >= 2
        # Both calls contain the same git context block.
        for prompt, _ in alpha.calls:
            assert "Current branch: main" in prompt


# ------------------------------------------------------------------
# Step 4.5.1 — Cost tracking
# ------------------------------------------------------------------


class TestE2ECost:
    """cost_query reflects accumulated per-model usage."""

    def test_cost_zero_initially(self, client: Any) -> None:
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)
            ws.send_json({"type": "cost_query"})
            msg = ws.receive_json()
            assert msg["type"] == "cost_info"
            assert msg["total_input_tokens"] == 0
            assert msg["model_usage"] == {}

    def test_cost_after_single_turn(self, client: Any) -> None:
        """Single provider in use → one entry in model_usage."""
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)
            ws.send_json({"type": "user_message", "content": "hi"})
            _drain_to_end(ws)

            ws.send_json({"type": "cost_query"})
            msg = ws.receive_json()
            # CapturingProvider defaults: input=3, output=2.
            assert msg["total_input_tokens"] == 3
            assert msg["total_output_tokens"] == 2
            assert len(msg["model_usage"]) == 1

    def test_cost_per_model_after_switch(self, client: Any) -> None:
        """Switching provider accumulates usage under both models."""
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)

            # Turn 1 with alpha.
            ws.send_json({"type": "user_message", "content": "q1"})
            _drain_to_end(ws)

            # Switch to beta.
            ws.send_json({"type": "model_switch", "provider_name": "beta"})
            sw = ws.receive_json()
            assert sw["ok"] is True

            # Turn 2 with beta.
            ws.send_json({"type": "user_message", "content": "q2"})
            _drain_to_end(ws)

            ws.send_json({"type": "cost_query"})
            msg = ws.receive_json()
            assert msg["total_input_tokens"] == 6
            assert msg["total_output_tokens"] == 4
            mu = msg["model_usage"]
            assert "model-alpha" in mu
            assert "model-beta" in mu
            assert mu["model-alpha"]["input_tokens"] == 3
            assert mu["model-beta"]["input_tokens"] == 3


# ------------------------------------------------------------------
# Step 4.5 — /model command flow
# ------------------------------------------------------------------


class TestE2EModelCommand:
    """model_status / model_list / model_switch route to the right provider."""

    def test_model_status_default(self, client: Any) -> None:
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)
            ws.send_json({"type": "model_status"})
            msg = ws.receive_json()
            assert msg["type"] == "model_status_result"
            assert msg["provider_name"] == "alpha"
            assert msg["model"] == "model-alpha"
            assert msg["is_override"] is False
            assert msg["default_provider_name"] == "alpha"

    def test_model_list_shows_both(self, client: Any) -> None:
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)
            ws.send_json({"type": "model_list"})
            msg = ws.receive_json()
            assert msg["type"] == "model_list_result"
            assert msg["current"] == "alpha"
            names = [p["name"] for p in msg["providers"]]
            assert names == ["alpha", "beta"]

    def test_model_switch_affects_next_query(
        self,
        client: Any,
        provider_pair: tuple[CapturingProvider, CapturingProvider],
    ) -> None:
        alpha, beta = provider_pair

        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)

            ws.send_json({"type": "user_message", "content": "q1"})
            _drain_to_end(ws)

            ws.send_json({"type": "model_switch", "provider_name": "beta"})
            sw = ws.receive_json()
            assert sw["ok"] is True
            assert sw["provider_name"] == "beta"
            assert sw["model"] == "model-beta"

            ws.send_json({"type": "user_message", "content": "q2"})
            _drain_to_end(ws)

        # Alpha was called once (turn 1), beta once (turn 2).
        assert len(alpha.calls) == 1
        assert len(beta.calls) == 1
        assert alpha.calls[0][1] == "model-alpha"
        assert beta.calls[0][1] == "model-beta"

    def test_model_switch_invalid(self, client: Any) -> None:
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)
            ws.send_json({"type": "model_switch", "provider_name": "ghost"})
            msg = ws.receive_json()
            assert msg["type"] == "model_switch_result"
            assert msg["ok"] is False
            assert "ghost" in msg["error"]
            assert set(msg["available"]) == {"alpha", "beta"}

    def test_model_status_after_switch_shows_override(self, client: Any) -> None:
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)
            ws.send_json({"type": "model_switch", "provider_name": "beta"})
            ws.receive_json()

            ws.send_json({"type": "model_status"})
            msg = ws.receive_json()
            assert msg["provider_name"] == "beta"
            assert msg["is_override"] is True
            assert msg["default_provider_name"] == "alpha"
