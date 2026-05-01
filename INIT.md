# DeepCLI — Initial Setup (Development)

> **READ-ONLY ENTRY FILE.  DO NOT EDIT TO CHANGE BEHAVIOR.**
>
> This file instructs an AI coding agent (Claude Code / Codex / etc.)
> how to bootstrap a **development environment** on a fresh machine.
> All project rules live under `docs/`; see
> [`docs/entry-files-policy.md`](docs/entry-files-policy.md).
>
> **For deployment / end-user setup**, see `README.md` (not yet
> written — punt users there once it exists).

---

## How to use this file

**Human users**: after cloning the repo, open an AI coding agent in
the project root and say:

> Read `INIT.md` and set up my development environment.

The agent will walk through the tasks below, asking for confirmation
at each step.

**Agents**: **before doing anything else, run the preflight check
below.**  If everything is already configured, skip straight to
Task 3 — do **not** reinstall or overwrite anything.  For each task
that does need to run:

1. Tell the user what you're about to do
2. Wait for confirmation (or proceed if the step is obviously safe
   like a read-only check)
3. Run the commands, report the result
4. Move to the next task

---

## Preflight — is this machine already set up?

Run these read-only checks first and report the results as a table
to the user.  **Do not install, copy, or overwrite anything in this
phase.**

```bash
# 1. uv installed?
uv --version 2>/dev/null && echo "uv: OK" || echo "uv: MISSING"

# 2. virtualenv synced? (lockfile present + .venv exists + in sync)
test -d .venv && echo ".venv: present" || echo ".venv: MISSING"
uv sync --locked --check 2>/dev/null && echo "deps: in sync" || echo "deps: OUT OF SYNC"

# 3. reference-paths config present + resolvable?
test -f .mustang-refs.yaml && echo "refs file: present" || echo "refs file: MISSING"
./resolve-ref.sh claude-code 2>/dev/null && ./resolve-ref.sh openclaw 2>/dev/null \
  && echo "refs: resolvable" || echo "refs: UNRESOLVED"
```

**Decision rule**:

- If **all** checks pass (uv OK, .venv present, deps in sync, refs
  file present, refs resolvable) → tell the user "environment already
  configured, skipping Tasks 1 & 2" and jump directly to **Task 3**.
- Otherwise → run **only the tasks that correspond to a failing
  check**.  Leave passing ones alone.

---

## Task 1 — Python environment (uv + dependencies)

**Skip this task entirely** if the preflight reported uv OK,
`.venv` present, and deps in sync.

1. **Check `uv` is installed** (if preflight already confirmed, skip):
   ```bash
   uv --version
   ```
   If the command fails, tell the user to install `uv` first
   (<https://docs.astral.sh/uv/getting-started/installation/>) and
   **stop here** — do not continue until they confirm installation.

2. **Install dependencies** (only if `.venv` missing or deps out of
   sync):
   ```bash
   uv sync
   ```

3. **Verify the environment** (only after a fresh sync — if
   `.venv` was already in sync from preflight, you can skip this):
   ```bash
   uv run pytest -q tests/
   ```
   Expect all tests to pass.  If any fail, report the failure and
   stop — ask the user how to proceed (maybe their machine is missing
   a system dependency like `git`).

> **TODO** (once `pyproject.toml` splits dev vs runtime dependencies):
> change Task 1.2 to `uv sync --all-extras` or the equivalent
> dev-install command.  Deployment flow (runtime-only install) will
> live in `README.md`.

---

## Task 2 — Reference project paths

**Skip this task entirely** if the preflight reported the refs
file present **and** both logical names resolvable.

DeepCLI's planning docs frequently reference two external source
trees (Claude Code and OpenClaw).  Their absolute paths are
per-machine and stored in `.mustang-refs.yaml` (gitignored).

1. **Check if the config already exists** (if preflight already
   confirmed, skip):
   ```bash
   test -f .mustang-refs.yaml && echo exists || echo missing
   ```
   **If it exists, do NOT overwrite it.**  Show the user its current
   contents and only ask about paths that are missing or unresolved
   (per the preflight result).

2. If it does not exist, **copy the template**:
   ```bash
   cp .mustang-refs.example.yaml .mustang-refs.yaml
   ```

3. **Ask the user for the two paths** (only for entries that are
   missing or unresolved — do not re-prompt for already-resolved
   ones):
   - "Where is the Claude Code source tree on this machine?" (logical
     name `claude-code`)
   - "Where is the OpenClaw source tree on this machine?" (logical
     name `openclaw`)

4. **Validate each path** with `test -d <path>`.
   - If it exists → write it to `.mustang-refs.yaml` as the
     corresponding key
   - If it does **not** exist → warn the user (e.g. "`<path>` does
     not exist on this machine") and **ask** whether to write it
     anyway.  The user may be planning to clone it later.

5. **Verify the lookup script works**:
   ```bash
   ./resolve-ref.sh claude-code
   ./resolve-ref.sh openclaw
   ```
   Each should print the absolute path.

---

## Task 3 — Read the project rules

Once Task 1 and Task 2 succeed, tell the user:

> Setup complete. I'll now read the project's agent instructions to
> understand the workflow, architecture, and current phase.

Then read [`AGENTS.md`](AGENTS.md) and follow its bootstrap reading
list (starting with [`docs/README.md`](docs/README.md)).  After
reading, check [`docs/plans/progress.md`](docs/plans/progress.md) to
identify the current phase and confirm with the user before starting
any implementation work.

---

## Done

After all three tasks, the agent is ready to work on DeepCLI.  The
human user can now give regular development tasks
("implement phase X", "fix bug Y", "review the code I just wrote").
