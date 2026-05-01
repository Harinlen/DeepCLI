# Code Review Flow

When the user says "Code Review", run this **full-repo audit** —
not just recent changes.

## Phase 1 — Audit

Scan every module under `src/` across four axes:

### 1.1 Design soundness
- **Architecture consistency**: matches
  `docs/kernel/architecture.md` + `docs/reference/decisions.md`?
- **Module boundaries**: clean responsibilities? Frontends don't
  import kernel internals directly?
- **Interface design**: public APIs make sense from callers? Args
  count / return types reasonable?
- **Abstraction level**: under- or over-abstracted?

### 1.2 Potential bugs
- **Edge cases**: empty lists, `None`, zero-length input, huge
  input, concurrent access
- **Never trust external input**: WS messages, file contents,
  external API responses, user input — always parse/validate with
  explicit exception handling.  Even "our own" kernel/CLI can send
  corrupt data.
- **Error handling**: exceptions caught correctly, none swallowed
- **Resource cleanup**: file handles, WS connections, asyncio tasks
- **Type safety**: `Any` creep? mismatched types that pass at runtime?
- **Races**: shared mutable state across async tasks?

### 1.3 Reuse and cohesion
- **Duplication**: same logic across modules?
- **Cohesion**: functions in one module serve one purpose?
- **Coupling**: minimal cross-module references?
- **Shared utilities**: existing helpers used, or reinvented?

### 1.4 Code quality
- **Naming**: clear, consistent
- **Dead code**: unused imports/vars/functions/classes
- **Comments**: accurate, non-stale, present where complex
- **File organization**: <300 lines, sensible `__init__.py` exports

## Phase 2 — Refactor & Fix

Priority order:

- **P0 — bugs**: fix immediately, add regression test
- **P1 — design deviations**: either refactor to match design OR
  update the design doc to reflect the better implementation
- **P2 — reuse**: extract duplication, unify patterns, raise cohesion
- **P3 — quality**: naming, dead code, comments

**Rules**:
- Every change needs a clear reason — no pointless polishing
- Keep existing tests passing through refactors
- New fixes ship with new tests
- Large refactors: split into steps, each step keeps the tree
  runnable

## Phase 3 — Quality Check

Run the full [`code-quality.md`](code-quality.md) checklist +
tool chain.

## Phase 4 — Report

Tell the user:

1. **Findings summary** by P0–P3
2. **Fixed items**: problem → root cause → fix
3. **Deferred items** with reasons
4. **Metrics delta**: test coverage, comment density, etc.
5. Update `docs/plans/progress.md`
6. **Do NOT commit or push.  Wait for instruction.**
