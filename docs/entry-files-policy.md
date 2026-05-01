# Entry Files Policy

## Entry files are READ-ONLY bootstrap pointers

Three files at the repo root act as entry points for AI coding agents:

- `CLAUDE.md` — auto-loaded by Claude Code; thin redirect to `AGENTS.md`
- `AGENTS.md` — canonical agent-neutral entry (Claude Code, Codex, etc.)
- `INIT.md` — first-time machine setup instructions (uv + reference paths)

**Do NOT edit these files to change agent behavior.**  They exist only
to direct agents to the real documentation under `docs/`.  If you need
to change how agents work on this project, edit the relevant file
under `docs/` instead:

| Goal | File to edit |
|------|--------------|
| Update bootstrap reading order / responsibility map | `docs/README.md` |
| Change workflow / phases / ground rules | `docs/workflow/workflow.md` |
| Add or revise a design decision | `docs/reference/decisions.md` |
| Tighten code-quality requirements | `docs/workflow/code-quality.md` |
| Document progress or findings | `docs/plans/progress.md` |
| Adjust dev environment notes | `docs/setup.md` |
| Anything architectural | `docs/kernel/architecture.md` |
| Record gotchas / deferred debt | `docs/lessons-learned.md` |
| Future phase planning | `docs/plans/roadmap.md` |

The entry files (`CLAUDE.md`, `AGENTS.md`, `INIT.md`) should only ever
change when:

1. **A new agent is supported** — add a new thin entry shim (e.g.
   `.cursorrules`) that also points into `docs/`
2. **Docs structure is reorganized** — update the paths referenced in
   the entry files
3. **A referenced file is renamed** — update the path
4. **A new setup step is added to INIT.md** — e.g. a new local config
   file that new machines need to scaffold

## Why

- **Single source of truth**: rules live in exactly one place (`docs/`)
- **Prevents drift**: Claude Code and Codex never see different
  "versions" of the project rules
- **Clean diffs**: PRs show real rule changes in `docs/`, not noise in
  entry files
- **Tool-neutral**: any future agent just needs a thin entry shim;
  project rules don't multiply

## For agents

If you (the agent) are about to modify `CLAUDE.md`, `AGENTS.md`, or
`INIT.md`, stop and ask: "Am I adding a new agent type, fixing a
broken path, or adding a new one-time setup step?"  If the answer is
anything else — edit `docs/` instead.

## Reference project paths are per-machine

Paths to Claude Code / OpenClaw / other reference source trees live in
`.mustang-refs.yaml` (gitignored, per-machine).  The template
`.mustang-refs.example.yaml` ships in the repo.

Agents should look up paths with:

```bash
./resolve-ref.sh <logical-name>
```

**Never** hardcode absolute paths like `/home/foo/...` into docs or
entry files — use the logical names (`claude-code`, `openclaw`) and
let `resolve-ref.sh` resolve them at use time.  If a new reference
project is added, update `.mustang-refs.example.yaml` with a new
logical name + placeholder.
