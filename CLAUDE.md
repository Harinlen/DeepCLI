# DeepCLI — Claude Code Entry

> **READ-ONLY ENTRY FILE.  DO NOT EDIT TO CHANGE BEHAVIOR.**
>
> This file exists only to direct Claude Code to the real agent
> instructions in [`AGENTS.md`](AGENTS.md).  All project rules live
> under `docs/`.  To change agent behavior, edit the appropriate file
> in `docs/` — **never this file**.
> See [`docs/entry-files-policy.md`](docs/entry-files-policy.md).
>
> **First time on this machine?** See [`INIT.md`](INIT.md) first.

## Read [`AGENTS.md`](AGENTS.md) now

`AGENTS.md` is the single agent-neutral entry file.  It contains:

- Project summary
- Bootstrap reading list (points into `docs/` — start with
  `docs/README.md`)
- Ground rules (progress tracking, discussion-phase rules, mandatory
  code-quality checklist)
- Monorepo structure + architectural rules
- Reference projects

Follow the instructions there.  Both Claude Code and Codex use the
same entry point; any difference in behavior between them is a bug in
the entry files.

## Why two files?

`CLAUDE.md` exists because Claude Code auto-loads it at session start.
`AGENTS.md` is the standard entry file for other AI coding agents
(Codex, etc.).  Both point at `docs/` so that **rules never drift**
between agents.
