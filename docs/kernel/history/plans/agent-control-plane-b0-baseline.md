# Agent Control Plane B0 Baseline

状态: active baseline
创建: 2026-04-30
相关: [`agent-control-plane.md`](agent-control-plane.md)

Batch B0 只建立目录 skeleton 和 wire/schema contract，不改变运行路径。当前 single
Primary Agent 兼容路径仍是 baseline：CLI 和 Probe 都通过现有 WebSocket `/session` 与
Primary Agent 通信。

## Fixture 目录

- Probe fixtures: `src/probe/`
- CLI smoke/tests: `src/cli/tests/`
- Kernel schema tests: `tests/kernel/agents/`

## B0 Schema/Test 命令

```bash
cd src/kernel
uv run pytest ../../tests/kernel/agents -q
uv run ruff check kernel/agents ../../tests/kernel/agents
uv run python -m py_compile \
  kernel/agents/schemas.py \
  kernel/agents/transport.py \
  kernel/supervisor/__init__.py \
  kernel/agent_hub/__init__.py \
  kernel/agent_hub/router/__init__.py \
  kernel/agent_hub/manager/__init__.py \
  kernel/agent_hub/global_resources/__init__.py \
  kernel/access_agent/__init__.py \
  kernel/agent_runtime/__init__.py
git diff --check
```

## CLI Smoke Baseline

CLI 的 baseline 是现有用户入口，不经过 fake ACP stdio agent：

```bash
cd src/cli
bun run tests/test_connect.ts
bun run tests/test_session.ts
bun run tests/test_prompt.ts
```

完整 CLI regression 入口：

```bash
cd src/cli
bun run tests/run_all.ts
```

## Probe Baseline

Probe 的 baseline 是验证底层 WebSocket/API 闭包，和 CLI 走同一个 Primary Agent
WebSocket 语义路径：

```bash
cd src/probe
uv run probe --help
```

带真实 Kernel 时使用当前 `/session` WebSocket endpoint：

```bash
cd src/probe
uv run probe --port 8200 --test --prompt "ping"
```

B0 不新增产品 runtime，因此本文件只记录 baseline 命令。后续改动 Access Agent /
Router / Primary Agent 主路径的 batch 必须同时跑 Probe 和 CLI smoke。
