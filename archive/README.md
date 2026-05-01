# Archive — No Longer Under Active Development

These projects have been superseded by `src/kernel/` and are kept
**for reference only**.  Do not modify them.  Do not build new
features on top of them.

| Directory | What it was | Superseded by |
|-----------|-------------|---------------|
| `daemon/` | Original mustang daemon — FastAPI + custom protocol | `src/kernel/` (full rewrite) |
| `tests-daemon/` | Test suite for daemon/ | N/A |

## Why Archived, Not Deleted

- Historical reference for implementation patterns (session handling,
  tool rendering, permission prompts, streaming UX, etc.)
- The `daemon/` codebase documents features that need to be ported to
  the kernel

## What Replaces Them

All new development happens in `src/kernel/`.  New frontends
(web, Discord, etc.) will connect to the kernel via WebSocket + ACP
protocol.  See `docs/kernel/` for the kernel design docs.
