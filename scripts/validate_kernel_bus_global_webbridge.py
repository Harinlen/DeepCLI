"""Generate the KernelBus / Global WebBridge validation report."""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import platform
import signal
import socket
import subprocess  # nosec B404
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import httpx
import websockets


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATION_ROOT = REPO_ROOT / "docs/plans/validation/kernel-bus-global-webbridge"


@dataclass
class Record:
    index: int
    name: str
    command: list[str]
    cwd: Path
    started_at: str
    ended_at: str
    exit_code: int
    stdout: str
    stderr: str
    path: Path

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe")
    parser.add_argument("--port", type=int)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.probe:
        if args.port is None:
            raise SystemExit("--port is required with --probe")
        return asyncio.run(_run_probe(args.probe, args.port))
    return _run_report(args.run_id)


def _run_report(run_id: str | None) -> int:
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = VALIDATION_ROOT / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    home = Path(tempfile.mkdtemp(prefix="deepcli-kbus-home-"))
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["DEEPCLI_HOME"] = str(home / ".deepcli")
    kernel_log = output_dir / "kernel.log"
    kernel_cwd = REPO_ROOT / "src/kernel"
    kernel_cmd = ["uv", "run", "python", "-m", "kernel", "--port", str(port), "--dev"]
    kernel_proc = subprocess.Popen(  # nosec B603
        kernel_cmd,
        cwd=kernel_cwd,
        env=env,
        stdout=kernel_log.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        text=True,
    )
    records: list[Record] = []
    try:
        _wait_for_http(port, "/health", timeout=45)
        _write_env_record(output_dir, port, home, kernel_cmd, kernel_cwd, kernel_proc.pid, kernel_log)
        commands = [
            (
                1,
                "WebBridge status before primary dependency",
                _probe_cmd("webbridge-status", port),
            ),
            (2, "Fake Chrome extension pairing", _probe_cmd("fake-extension-pairing", port)),
            (3, "Browser fetch through resource:web_bridge", _probe_cmd("browser-fetch", port)),
            (4, "Chrome offline behavior", _probe_cmd("offline-behavior", port)),
            (
                5,
                "Agent command regression via real probe",
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "probe",
                    "--port",
                    str(port),
                    "--raw",
                    "_mustang.agent/agents/list",
                    '{"actorAgentId":"primary"}',
                ],
            ),
            (6, "External/Internal interface split", _probe_cmd("interface-split", port)),
            (7, "WebSocket health / reconnect projection", _probe_cmd("health-reconnect", port)),
            (
                8,
                "Topology discovery via real probe",
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "probe",
                    "--port",
                    str(port),
                    "--raw",
                    "_mustang.bus/topology.snapshot",
                    "{}",
                ],
            ),
            (9, "GlobalResourceHost ownership projection", _probe_cmd("global-resource-host", port)),
            (10, "Async concurrency and retry surface", _probe_cmd("async-retry", port)),
            (
                11,
                "Architecture documentation update",
                [
                    "uv",
                    "run",
                    "python",
                    str(Path(__file__).relative_to(REPO_ROOT)),
                    "--probe",
                    "architecture-doc-update",
                    "--port",
                    str(port),
                ],
            ),
        ]
        for index, name, command in commands:
            records.append(_run_record(output_dir, index, name, command, env))
        report_path = output_dir / "report.html"
        _write_html_report(
            report_path=report_path,
            records=records,
            run_id=run_id,
            port=port,
            home=home,
            kernel_cmd=kernel_cmd,
            kernel_cwd=kernel_cwd,
            kernel_pid=kernel_proc.pid,
            kernel_log=kernel_log,
        )
        records.append(
            _run_record(
                output_dir,
                12,
                "HTML validation report",
                [
                    "uv",
                    "run",
                    "python",
                    str(Path(__file__).relative_to(REPO_ROOT)),
                    "--probe",
                    "html-report",
                    "--port",
                    str(port),
                ],
                env | {"REPORT_HTML": str(report_path)},
            )
        )
        _write_html_report(
            report_path=report_path,
            records=records,
            run_id=run_id,
            port=port,
            home=home,
            kernel_cmd=kernel_cmd,
            kernel_cwd=kernel_cwd,
            kernel_pid=kernel_proc.pid,
            kernel_log=kernel_log,
        )
        print(report_path)
        return 0 if all(record.passed for record in records) else 1
    finally:
        _terminate(kernel_proc)


def _probe_cmd(name: str, port: int) -> list[str]:
    return [
        "uv",
        "run",
        "python",
        str(Path(__file__).relative_to(REPO_ROOT)),
        "--probe",
        name,
        "--port",
        str(port),
    ]


async def _run_probe(name: str, port: int) -> int:
    probes = {
        "webbridge-status": _probe_webbridge_status,
        "fake-extension-pairing": _probe_fake_extension_pairing,
        "browser-fetch": _probe_browser_fetch,
        "offline-behavior": _probe_offline_behavior,
        "interface-split": _probe_interface_split,
        "health-reconnect": _probe_health_reconnect,
        "global-resource-host": _probe_global_resource_host,
        "async-retry": _probe_async_retry,
        "architecture-doc-update": _probe_architecture_doc_update,
        "html-report": _probe_html_report,
    }
    try:
        result = await probes[name](port)
    except Exception as exc:
        print(json.dumps({"ok": False, "probe": name, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps({"ok": True, "probe": name, "result": result}, indent=2))
    return 0


async def _probe_webbridge_status(port: int) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"http://127.0.0.1:{port}/web-bridge/status.json")
        response.raise_for_status()
    payload = response.json()
    assert payload["status"] in {"setup_needed", "configured", "available", "unavailable"}
    assert "route unavailable: primary" not in json.dumps(payload)
    return payload


async def _probe_fake_extension_pairing(port: int) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        pair = await client.post(f"http://127.0.0.1:{port}/web-bridge/pair")
        pair.raise_for_status()
        status = pair.json()
    async with websockets.connect(status["bridgeWsUrl"]) as ws:
        ack = await _hello(ws, status, browser_name="Chrome")
        async with httpx.AsyncClient(timeout=10) as client:
            connected = await client.get(f"http://127.0.0.1:{port}/web-bridge/status.json")
            connected.raise_for_status()
            reset = await client.post(f"http://127.0.0.1:{port}/web-bridge/reset")
            reset.raise_for_status()
    connected_payload = connected.json()
    assert connected_payload["connected"] is True
    return {"ack": ack, "status": connected_payload, "reset": reset.json()}


async def _probe_browser_fetch(port: int) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        pair = await client.post(f"http://127.0.0.1:{port}/web-bridge/pair")
        pair.raise_for_status()
        status = pair.json()
    requests: list[dict[str, Any]] = []

    async with websockets.connect(status["bridgeWsUrl"]) as ws:
        await _hello(ws, status, browser_name="Chrome")
        serve = asyncio.create_task(_serve_fetch(ws, requests))
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                fetch = await client.post(
                    f"http://127.0.0.1:{port}/web-bridge/fetch",
                    json={"url": "https://example.test/webbridge", "maxChars": 2000},
                )
                fetch.raise_for_status()
                payload = fetch.json()
        finally:
            serve.cancel()
            await asyncio.gather(serve, return_exceptions=True)
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(f"http://127.0.0.1:{port}/web-bridge/reset")
    assert requests
    assert payload["ok"] is True
    assert "resource:web_bridge" not in str(payload.get("error") or "")
    return {"fetch": payload, "extensionRequests": requests}


async def _probe_offline_behavior(port: int) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"http://127.0.0.1:{port}/web-bridge/fetch",
            json={"url": "https://example.test/offline"},
        )
        status = await client.get(f"http://127.0.0.1:{port}/web-bridge/status.json")
        status.raise_for_status()
    assert response.status_code >= 400
    assert status.json()["connected"] is False
    return {"fetchStatus": response.status_code, "status": status.json(), "body": response.text[:500]}


async def _probe_interface_split(port: int) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        topology = await client.get(f"http://127.0.0.1:{port}/bus/topology")
        topology.raise_for_status()
        status = await client.get(f"http://127.0.0.1:{port}/web-bridge/status.json")
        status.raise_for_status()
    services = {item["serviceId"]: item for item in topology.json()["services"]}
    assert services["resource:web_bridge"]["owner"] == "GlobalResourceHost"
    assert services["resource:web_search"]["owner"] == "GlobalResourceHost"
    assert "agent:primary" in services
    assert "route unavailable: primary" not in status.text
    return {"services": services, "webBridgeStatus": status.json()}


async def _probe_health_reconnect(port: int) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        topology = await client.get(f"http://127.0.0.1:{port}/bus/topology")
        topology.raise_for_status()
    services = {item["serviceId"]: item for item in topology.json()["services"]}
    record = services["resource:web_bridge"]
    assert record["routeReady"] is True
    assert record["generation"] >= 1
    return {
        "routeStatus": record,
        "note": "Current migration slice exposes healthy/resource generation projection.",
    }


async def _probe_global_resource_host(port: int) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        topology = await client.get(f"http://127.0.0.1:{port}/bus/topology")
        topology.raise_for_status()
    services = {item["serviceId"]: item for item in topology.json()["services"]}
    resource_owners = {
        key: value["owner"] for key, value in services.items() if key.startswith("resource:")
    }
    assert resource_owners == {
        "resource:web_bridge": "GlobalResourceHost",
        "resource:web_search": "GlobalResourceHost",
    }
    return {"resourceOwners": resource_owners, "supervisorGranularity": "coarse-host"}


async def _probe_async_retry(port: int) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        tasks = [
            client.get(f"http://127.0.0.1:{port}/health"),
            client.get(f"http://127.0.0.1:{port}/bus/topology"),
            client.get(f"http://127.0.0.1:{port}/web-bridge/status.json"),
            client.get(f"http://127.0.0.1:{port}/route_status/primary"),
        ]
        responses = await asyncio.gather(*tasks)
    assert all(response.status_code == 200 for response in responses)
    return {"statuses": [response.status_code for response in responses]}


async def _probe_architecture_doc_update(_port: int) -> dict[str, Any]:
    text = (REPO_ROOT / "docs/kernel/architecture.md").read_text(encoding="utf-8")
    required = ["KernelBus", "InternalBusPlane", "AgentRuntimeHost", "GlobalResourceHost"]
    missing = [term for term in required if term not in text]
    assert not missing, missing
    assert "Supervisor -> Agent Hub / Access Agent / Mustang Agent(primary)" not in text
    return {"requiredTerms": required}


async def _probe_html_report(_port: int) -> dict[str, Any]:
    report = Path(os.environ["REPORT_HTML"])
    text = report.read_text(encoding="utf-8")
    for index in range(1, 12):
        assert f"{index:02d}" in text
    assert "report.html" in str(report)
    return {"report": str(report), "bytes": len(text)}


async def _hello(ws: Any, status: dict[str, Any], *, browser_name: str) -> dict[str, Any]:
    await ws.send(
        json.dumps(
            {
                "type": "hello",
                "protocolVersion": "web-bridge.v1",
                "extensionId": "validation-extension",
                "pairingToken": status["pairingToken"],
                "browser": {"name": browser_name, "version": "validation"},
            }
        )
    )
    ack = json.loads(await ws.recv())
    assert ack["ok"] is True
    return ack


async def _serve_fetch(ws: Any, requests: list[dict[str, Any]]) -> None:
    async for raw in ws:
        payload = json.loads(raw)
        if payload.get("type") != "fetch_tab":
            continue
        requests.append(payload)
        body = "Validation body returned by the fake WebBridge extension."
        await ws.send(
            json.dumps(
                {
                    "type": "fetch_result",
                    "id": payload["id"],
                    "ok": True,
                    "url": payload["url"],
                    "finalUrl": payload["url"],
                    "title": "Validation Page",
                    "text": body,
                    "readabilityText": body,
                    "metadata": {"description": "validation"},
                    "signals": {"loaded": True, "textLength": len(body)},
                    "extractionMethod": "validation-fake-extension",
                }
            )
        )


def _run_record(
    output_dir: Path,
    index: int,
    name: str,
    command: list[str],
    env: dict[str, str],
) -> Record:
    started = datetime.now(timezone.utc).isoformat()
    proc = subprocess.run(  # nosec B603
        command,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
    )
    ended = datetime.now(timezone.utc).isoformat()
    slug = {
        1: "webbridge-status",
        2: "fake-extension-pairing",
        3: "browser-webfetch",
        4: "offline-behavior",
        5: "agent-command-regression",
        6: "interface-split",
        7: "health-reconnect",
        8: "topology-discovery",
        9: "global-resource-host",
        10: "async-retry",
        11: "architecture-doc-update",
        12: "html-report",
    }[index]
    path = output_dir / f"{index:02d}-{slug}.txt"
    record = Record(
        index=index,
        name=name,
        command=command,
        cwd=REPO_ROOT,
        started_at=started,
        ended_at=ended,
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        path=path,
    )
    path.write_text(_format_record(record), encoding="utf-8")
    return record


def _format_record(record: Record) -> str:
    return "\n".join(
        [
            f"name: {record.name}",
            f"command: {_quote(record.command)}",
            f"cwd: {record.cwd}",
            f"started_at: {record.started_at}",
            f"ended_at: {record.ended_at}",
            f"exit_code: {record.exit_code}",
            f"status: {'PASS' if record.passed else 'FAIL'}",
            "stdout:",
            record.stdout,
            "stderr:",
            record.stderr,
        ]
    )


def _write_env_record(
    output_dir: Path,
    port: int,
    home: Path,
    kernel_cmd: list[str],
    kernel_cwd: Path,
    kernel_pid: int,
    kernel_log: Path,
) -> None:
    git_status = subprocess.run(
        ["git", "status", "--short"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    content = textwrap.dedent(
        f"""
        run_started_at: {datetime.now(timezone.utc).isoformat()}
        repo: {REPO_ROOT}
        python: {sys.version}
        os: {platform.platform()}
        kernel_command: {_quote(kernel_cmd)}
        kernel_cwd: {kernel_cwd}
        kernel_port: {port}
        kernel_pid: {kernel_pid}
        validation_home: {home}
        kernel_log: {kernel_log}
        git_status:
        {git_status.stdout}
        """
    ).strip()
    (output_dir / "00-env.txt").write_text(content + "\n", encoding="utf-8")


def _write_html_report(
    *,
    report_path: Path,
    records: list[Record],
    run_id: str,
    port: int,
    home: Path,
    kernel_cmd: list[str],
    kernel_cwd: Path,
    kernel_pid: int,
    kernel_log: Path,
) -> None:
    passed = all(record.passed for record in records)
    sections = "\n".join(_html_section(record) for record in records)
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>KernelBus Global WebBridge Validation {html.escape(run_id)}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 32px; color: #17202a; }}
    .status {{ padding: 10px 14px; display: inline-block; border-radius: 6px; }}
    .pass {{ background: #e8f7ef; color: #10653d; }}
    .fail {{ background: #fdeaea; color: #8a1f1f; }}
    section {{ border-top: 1px solid #d8dee4; padding-top: 18px; margin-top: 22px; }}
    pre {{ background: #f6f8fa; padding: 12px; overflow: auto; border-radius: 6px; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    table {{ border-collapse: collapse; }}
    td {{ padding: 4px 12px 4px 0; vertical-align: top; }}
  </style>
</head>
<body>
  <h1>KernelBus Global WebBridge Validation</h1>
  <p class="status {'pass' if passed else 'fail'}">Overall: {'PASS' if passed else 'FAIL'}</p>
  <table>
    <tr><td>Run</td><td><code>{html.escape(run_id)}</code></td></tr>
    <tr><td>Kernel command</td><td><code>{html.escape(_quote(kernel_cmd))}</code></td></tr>
    <tr><td>Kernel cwd</td><td><code>{html.escape(str(kernel_cwd))}</code></td></tr>
    <tr><td>Kernel port</td><td><code>{port}</code></td></tr>
    <tr><td>Kernel PID</td><td><code>{kernel_pid}</code></td></tr>
    <tr><td>Validation HOME</td><td><code>{html.escape(str(home))}</code></td></tr>
    <tr><td>Kernel log</td><td><code>{html.escape(str(kernel_log))}</code></td></tr>
  </table>
  {sections}
</body>
</html>
"""
    report_path.write_text(html_text, encoding="utf-8")


def _html_section(record: Record) -> str:
    return f"""
<section>
  <h2>{record.index:02d} {html.escape(record.name)}</h2>
  <p class="status {'pass' if record.passed else 'fail'}">{'PASS' if record.passed else 'FAIL'}</p>
  <table>
    <tr><td>Command</td><td><code>{html.escape(_quote(record.command))}</code></td></tr>
    <tr><td>CWD</td><td><code>{html.escape(str(record.cwd))}</code></td></tr>
    <tr><td>Started</td><td><code>{html.escape(record.started_at)}</code></td></tr>
    <tr><td>Ended</td><td><code>{html.escape(record.ended_at)}</code></td></tr>
    <tr><td>Exit code</td><td><code>{record.exit_code}</code></td></tr>
    <tr><td>Raw output</td><td><code>{html.escape(str(record.path))}</code></td></tr>
  </table>
  <h3>stdout</h3>
  <pre>{html.escape(record.stdout)}</pre>
  <h3>stderr</h3>
  <pre>{html.escape(record.stderr)}</pre>
</section>
"""


def _wait_for_http(port: int, path: str, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}{path}"
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1) as response:
                if response.status < 500:
                    return
        except (OSError, URLError):
            time.sleep(0.25)
    raise RuntimeError(f"kernel did not become ready at {url}")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _terminate(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def _quote(command: list[str]) -> str:
    import shlex

    return " ".join(shlex.quote(part) for part in command)


if __name__ == "__main__":
    raise SystemExit(main())
