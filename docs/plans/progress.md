# Progress

Short orientation ledger only.  Detailed facts belong in subsystem docs;
shipped implementation history belongs in the history docs linked below.

## Current Status

**Active track**: supervised single-Primary Agent Control Plane is landed;
current work is docs cleanup, release hardening, and full-system regression
coverage.

**Runtime path**: `deepcli` launcher -> Supervisor -> Agent Hub / Access Agent /
Primary Agent Runtime.  CLI, Probe, and future frontends enter through ACP over
Access Agent WebSocket and route to the Primary Runtime through the Hub.

**Kernel version**: `1.0.0` (session DB schema v1 pre-release baseline).

**Active code**:

- `src/kernel/` — Kernel, Supervisor, Access Agent, Agent Hub, Primary Runtime.
- `src/cli/` — thin TypeScript/Bun ACP client and TUI.
- `src/launcher/` — user-local launchers and release packaging.
- `src/probe/`, `scripts/` — ACP probe client, dev startup, release helpers.
- `archive/` — read-only daemon-era reference.

## Where History Lives

| Area | Durable record |
|---|---|
| Kernel architecture | [`../kernel/architecture.md`](../kernel/architecture.md) |
| Kernel shipped milestones | [`../kernel/history.md`](../kernel/history.md) |
| Kernel completed plans | [`../kernel/history/plans/`](../kernel/history/plans/) |
| Kernel subsystem facts | [`../kernel/subsystems/`](../kernel/subsystems/) |
| CLI shipped milestones | [`../cli/history/implemented-summary.md`](../cli/history/implemented-summary.md) |
| CLI design facts | [`../cli/design.md`](../cli/design.md) |
| CLI completed plans | [`../cli/history/plans/`](../cli/history/plans/) |
| Launcher / installers | [`../launcher/history/plans/`](../launcher/history/plans/) |
| Future work | [`roadmap.md`](roadmap.md), [`backlog.md`](backlog.md) |

## Recent Log

Keep only the newest handful of entries here; move details to the owning
history or design document when this table grows.

| Date | Item | Result | Verification |
|---|---|---|---|
| 2026-05-09 | POSIX macOS installer path | Merged latest `origin/main`, added macOS amd64 / arm64 one-line release packaging, and collapsed Linux/macOS packaging into shared `packaging/posix` build/install implementations with thin platform wrappers. The published POSIX `install.sh` auto-detects Linux vs macOS; `install-macos.sh` remains a compatibility alias. | `bash -n src/launcher/bin/deepcli src/launcher/packaging/linux/*.sh src/launcher/packaging/macos/*.sh src/launcher/packaging/posix/*.sh install-dev.sh`; `sh -n src/launcher/packaging/linux/install.sh src/launcher/packaging/macos/install.sh src/launcher/packaging/posix/install.sh`; Ruby YAML parse for `.github/workflows/release-packages.yml`; macOS release-shaped installer smoke with temp HOME and fake private `uv` -> `installer-smoke-ok`; macOS build-script smoke with fake `uv` / `bun` for `arm64` and `amd64` -> `build-smoke-ok`; `git diff --check` |
| 2026-05-09 | REPL non-Windows completion | Retired the old JSON batch dispatcher and tests, moved primitive allowlist to `tools/repl/primitives.py`, and reduced the REPL plan to a completed record with only Windows probe remaining. | REPL/tool focused tests `38 passed`; REPL E2E `3 passed`; `git diff --check` |
| 2026-05-09 | Remove `/auth` slash command | Removed `auth` from the CommandManager catalog and gateway special-case handling; credential storage remains available through ACP `secrets/auth`. | `73 passed`; `git diff --check` |
| 2026-05-08 | Config-folder path cleanup | Moved canonical flags path to `~/.deepcli/config/flags.yaml`, kept legacy root flags as read-only fallback, confirmed CLI `client.yaml` stays under `config/`, and refreshed current docs/tests. | `18 passed`; CLI config/OOBE probes; REPL E2E `3 passed`; `git diff --check` |
| 2026-05-08 | CLI Welcome recent sessions sync | Welcome Recent sessions now refreshes from the ACP-backed session list after session lifecycle and prompt actions, with the active empty session merged locally so `/session new` is visible before the first persisted user turn. | `bun run src/cli/tests/test_agent_session_adapter.ts`; `bun run src/cli/tests/test_session_startup.ts`; `bun run src/cli/tests/test_input_controller_r4.ts`; `bun run src/cli/tests/test_session_list_mapper.ts`; `bun run src/cli/tests/test_autocomplete_sort.ts`; `bun run src/cli/tests/test_ui_golden_r5.ts`; `bun run src/cli/tests/test_session_selector_omp.ts`; `bun run src/cli/tests/test_session_picker.ts`; `bun run src/cli/tests/test_status_line.ts`; `bunx tsc --noEmit --pretty false` from `src/cli`; `git diff --check` |
| 2026-05-08 | Kernel-owned context usage snapshot | `usage_update` now carries per-session context `used` / `size`; `session/load` and `session/resume` rehydrate that snapshot; CLI status line and idle compaction read it instead of local transcript/cumulative token guesses. | `bun test ./src/cli/tests/test_status_line.ts ./src/cli/tests/test_agent_session_adapter.ts ./src/cli/tests/test_session_startup.ts ./src/cli/tests/test_session_resume_before_prompt.ts`; `uv run pytest tests/kernel/session/test_session_manager.py::test_resume_session_replays_usage_snapshot tests/kernel/session/test_session_manager.py::test_prompt_broadcasts_usage_update tests/kernel/session/test_client_stream_replay.py::test_replay_turn_completed_emits_usage_update tests/kernel/protocol/test_codec.py -q`; `uv run pytest tests/e2e/test_session_load_cost_replay_e2e.py -q -m e2e`; `uv run ruff format --check ...`; `uv run ruff check ...`; `bunx tsc --noEmit --pretty false`; `git diff --check -- ...` |
| 2026-05-08 | Scriptable REPL Tool v1 | Added out-of-process Python REPL worker, ToolExecutor nested dispatch, primitive tool wrappers, cwd propagation, prompt/docs updates, and `.deepcli` E2E sandbox fix. | `57 passed`; `3 passed in 70.20s` for `tests/e2e/test_repl_e2e.py`; `git diff --check` |
| 2026-05-08 | REPL rewrite plan audit | Rewrote `repl-rewrite.md` in Chinese as an implementable plan: verified Claude Code visible REPL gating, corrected DeepCLI executor/session assumptions, made ToolExecutor nested dispatch first, and added Windows shell rules. | `git diff --check -- docs/plans/repl-rewrite.md docs/plans/backlog.md`; plan link checker |

## Capability Snapshot

### Kernel / Runtime

- Supervisor-owned process lifecycle for Hub, Access, and Primary Runtime.
- ACP WebSocket transport through Access Agent with Hub router backend.
- SQLite-backed sessions, reconnect-safe `clientTurnId`, permission tunneling,
  mode/config updates, replay, and user REPL execution.
- LLM provider routing, tool authorization, built-in tools, MCP, skills,
  hooks, memory, schedule, git context, and commands.

### CLI / Launcher

- Thin ACP client with active-port TUI, permission dialogs, session/model/theme
  commands, reconnect handling, slash command catalog, and PTY probes.
- User-local `deepcli` launcher for release-shaped runtime startup, status,
  logs, restart/stop, and uninstall.
- Linux, macOS, and Windows installer paths with private DeepCLI `uv` / managed Python
  runtime and precompiled CLI artifact.

## Update Rules

- Add only the newest completion to **Recent Log**.
- Keep this file below roughly 80 lines; compress or move older rows when needed.
- Put durable facts in the owning docs:
  `docs/kernel/architecture.md`, `docs/kernel/subsystems/*`, `docs/cli/*`,
  `docs/launcher/*`, or completed-plan archives.
- Put gotchas and environment quirks in [`../lessons-learned.md`](../lessons-learned.md).
