from __future__ import annotations

from pathlib import Path

import pytest

from kernel.tools.builtin import shell_exec


def test_bash_shell_spec_prefers_bash_then_sh(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(name: str) -> str | None:
        return {"/usr/bin/bash": "/usr/bin/bash"}.get(f"/usr/bin/{name}")

    monkeypatch.setattr(shell_exec.shutil, "which", fake_which)

    spec = shell_exec.bash_shell_spec("echo hi")

    assert spec is not None
    assert spec.argv == ["/usr/bin/bash", "-lc", "echo hi"]


def test_platform_shell_spec_uses_powershell_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shell_exec.os, "name", "nt")
    monkeypatch.setattr(
        shell_exec.shutil,
        "which",
        lambda name: "C:\\Program Files\\PowerShell\\7\\pwsh.exe" if name == "pwsh" else None,
    )

    spec = shell_exec.platform_shell_spec("Get-Process")

    assert spec is not None
    assert spec.argv == [
        "C:\\Program Files\\PowerShell\\7\\pwsh.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-Process",
    ]


def test_platform_shell_spec_falls_back_to_cmd_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shell_exec.os, "name", "nt")
    monkeypatch.setattr(
        shell_exec.shutil,
        "which",
        lambda name: "C:\\Windows\\System32\\cmd.exe" if name == "cmd.exe" else None,
    )

    spec = shell_exec.platform_shell_spec("dir")

    assert spec is not None
    assert spec.argv == ["C:\\Windows\\System32\\cmd.exe", "/d", "/s", "/c", "dir"]


async def test_spawn_shell_background_uses_exec_for_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    async def fake_exec(*argv: str, **kwargs: object) -> object:
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(shell_exec.asyncio, "create_subprocess_exec", fake_exec)

    proc = await shell_exec.spawn_shell_background(
        shell_exec.ShellSpec(argv=["bash", "-lc", "echo hi"]),
        cwd=tmp_path,
        env={"X": "1"},
        stdout=1,
        stderr=2,
    )

    assert proc is not None
    assert calls["argv"] == ("bash", "-lc", "echo hi")
    assert calls["kwargs"]["cwd"] == str(tmp_path)  # type: ignore[index]
    assert calls["kwargs"]["stdout"] == 1  # type: ignore[index]
    assert calls["kwargs"]["stderr"] == 2  # type: ignore[index]
    assert calls["kwargs"]["env"]["X"] == "1"  # type: ignore[index]
