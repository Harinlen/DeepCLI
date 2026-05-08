# CLI Implemented Summary

This file summarizes shipped CLI work.  Full historical plans remain in this
folder; current design facts live in [`../design.md`](../design.md).

## Current Shape

The CLI is a TypeScript/Bun thin client in `src/cli/`.  It owns terminal UI,
local client config, token resolution, startup orchestration, and ACP event
rendering.  It does not import kernel Python modules or read kernel SQLite /
state files directly; runtime behavior goes through WebSocket ACP.

## Shipped Milestones

| Milestone | What Landed | Detailed Record |
|---|---|---|
| Phase A baseline | Minimal ACP connection and interactive loop. | historical context in [phase-b-tui-migration.md](phase-b-tui-migration.md) |
| Phase B TUI migration | oh-my-pi active-port TUI surface, welcome/editor/status/tool rendering, autocomplete, ACP event mapping, manifest checker. | [phase-b-tui-migration.md](phase-b-tui-migration.md) |
| Phase B repair R1-R6 | Restored OMP status line/controller path, isolated ACP adapter, wired input handling, added golden frames and real PTY/TUI probe. | [phase-b-ui-alignment-repair.md](phase-b-ui-alignment-repair.md) |
| Permissions UI | Permission prompts use the copied OMP hook dialog host while preserving the DeepCLI ACP permission round-trip. | [phase-c-permissions.md](phase-c-permissions.md) |
| Session/config/theme | ACP-backed session creation/switch/list/delete, client config loading, theme config, startup orchestration, recent sessions in welcome. | [phase-d-session-config-theme.md](phase-d-session-config-theme.md) |
| Kernel REPL routing | `!` / `!!` shell and `$` / `$$` Python input route to kernel ACP execution methods instead of local CLI execution. | [kernel-repl-bang-dollar.md](kernel-repl-bang-dollar.md) |
| OMP-first refactor | Production interactive mode starts through the copied OMP path; session selector uses ACP-backed rows; active-port parity checks guard copied files. | [omp-first-refactor.md](omp-first-refactor.md) |
| Active-port prune | Removed unused copied OMP runtime assets and kept the manifest as the source of truth for managed files and local assets. | [plans/cli-active-port-prune-audit.md](plans/cli-active-port-prune-audit.md) |
| ACP namespace migration | CLI calls active DeepCLI extensions through `_mustang.agent/*`; legacy parsing for old execution updates was removed. | [../../kernel/history/plans/acp-acpx-schema-alignment-plan.md](../../kernel/history/plans/acp-acpx-schema-alignment-plan.md) |
| Welcome logo restore | The local welcome logo asset is registered in the active-port manifest and covered by golden-frame glyph assertion. | [../design.md](../design.md) |

## Current Verification Surface

Use the focused command for the surface you touched:

- Typecheck: `bun x tsc -p src/cli/tsconfig.json --noEmit`
- Local CLI suite: `cd src/cli && bun run tests/run_all.ts`
- Phase B UI suite: `cd src/cli && bun run tests/run_phase_b.ts`
- Active-port manifest: `cd src/cli && bun run scripts/check_active_port.ts`
- OMP parity: `cd src/cli && bun run scripts/check_omp_parity.ts`
- Real TUI probe: `cd src/cli && bun run tests/probe_phase_b_pty.ts`

`check_omp_parity.ts` is intentionally strict for copied OMP files.  When it
fails, either restore parity or document the allowed seam in the checker.

## Update Rules

- Add new shipped CLI work here only after it is implemented.
- Keep unfinished work under `docs/plans/`.
- Keep detailed behavior in `../design.md` or the relevant history plan, not
  in `docs/plans/progress.md`.
