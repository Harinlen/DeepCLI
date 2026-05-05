from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import subprocess

from kernel.llm.config import ModelRef
from kernel.llm.types import TextChunk
from kernel.orchestrator.types import StopReason
from kernel.session.runtime.helpers import (
    decode_cursor,
    encode_cursor,
    get_git_branch,
    make_summarise_closure,
    map_orch_stop_reason,
)


def test_cursor_round_trip_preserves_modified_and_session_id() -> None:
    token = encode_cursor("2026-05-01T00:00:00Z", "session|with|pipes")

    assert decode_cursor(token) == ("2026-05-01T00:00:00Z", "session|with|pipes")


def test_decode_cursor_rejects_malformed_token() -> None:
    with pytest.raises(Exception):
        decode_cursor("not-base64")


def test_map_orchestrator_stop_reason_known_values() -> None:
    assert map_orch_stop_reason(StopReason.end_turn) == "end_turn"
    assert map_orch_stop_reason(StopReason.max_turns) == "max_turn_requests"
    assert map_orch_stop_reason(StopReason.cancelled) == "cancelled"
    assert map_orch_stop_reason(StopReason.error) == "error"
    assert map_orch_stop_reason(StopReason.hook_blocked) == "end_turn"


def test_get_git_branch_returns_none_outside_git_repo(tmp_path: Path) -> None:
    assert get_git_branch(tmp_path) is None


def test_get_git_branch_returns_current_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(returncode=0, stdout="main\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert get_git_branch(tmp_path) == "main"


def test_get_git_branch_returns_none_for_detached_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(returncode=0, stdout="HEAD\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert get_git_branch(tmp_path) is None


def test_get_git_branch_returns_none_when_git_command_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("git missing")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert get_git_branch(tmp_path) is None


def test_make_summarise_closure_returns_none_without_llm_manager() -> None:
    assert make_summarise_closure(None) is None


class _DeltaChunk:
    def __init__(self, delta: str) -> None:
        self.delta = delta


class _TextAttrChunk:
    def __init__(self, text: str) -> None:
        self.text = text


class _LLMManager:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def model_for_or_default(self, role: str) -> ModelRef:
        assert role == "compact"
        return ModelRef(provider="test", model="compact")

    async def stream(self, **kwargs: Any):
        self.calls.append(kwargs)

        async def _chunks():
            yield TextChunk(content="alpha")
            yield _TextAttrChunk(" beta")
            yield _DeltaChunk(" gamma")
            yield object()

        return _chunks()


@pytest.mark.anyio
async def test_make_summarise_closure_collects_supported_chunk_shapes() -> None:
    manager = _LLMManager()
    summarise = make_summarise_closure(manager)
    assert summarise is not None

    result = await summarise("long content", "make it short")

    assert result == "alpha beta gamma"
    assert manager.calls[0]["model"] == ModelRef(provider="test", model="compact")
    assert manager.calls[0]["messages"][0].content[0].text == "long content"
    assert manager.calls[0]["tool_schemas"] == []
