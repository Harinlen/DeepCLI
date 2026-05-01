# Code Quality Checklist

Run these **5 steps in order** after every implementation task.
No exceptions.  A task isn't done until all five pass.

## 1. Verify Against Design

- Re-read the relevant design doc (`docs/kernel/subsystems/*` or
  `docs/reference/*`).
- Confirm interface, data flow, module boundaries match.
- If the implementation deviated, **update the doc** to match reality.
  Don't leave docs and code out of sync.

## 2. Write Tests

- Every new module gets a corresponding test file in `tests/`.
- Test path mirrors source:
  `src/kernel/kernel/orchestrator/orchestrator.py` →
  `tests/kernel/orchestrator/test_orchestrator.py`.
- Cover public interfaces, edge cases, error paths.
- Integration tests for module interactions (orchestrator → provider
  → tool).
- `pytest + pytest-asyncio` for async.
- Aim for **meaningful** coverage — test behavior, not line count.

## 3. Root-Cause Bug Fixing

When fixing bugs:

1. **Reproduce first** — write a failing test before touching code.
2. **Trace to root cause** — not just the symptom.
3. **Fix the cause** — never band-aid around the real bug.
4. **Check siblings** — does the same pattern exist elsewhere?
5. **Keep the failing test** — it's now a regression test.

If the fix feels like a band-aid, dig deeper.

## 4. Refactor Pass

- **DRY**: is there duplicated logic to extract?
- **Interfaces**: do public APIs make sense from the caller's POV?
- **Naming**: clear, consistent across the codebase?
- **Imports**: clean module boundaries, no circular deps, CLI still
  doesn't import kernel internals directly?
- **Dead code**: unused imports/vars/functions gone?
- **Readability**: would a newcomer understand this without
  extra explanation?

Refactor only when it makes things genuinely simpler.  Don't
over-refactor.

### Readability Pass

Run the standalone
[`Readable Code Guide`](readable-code.md)
when a change touches non-trivial control flow, names, comments,
tests, or module boundaries.  Keep the detailed guidance there so this
checklist remains short.

## 5. Comment Density & Docs

**Target: 20–25% comments+docstrings of non-blank lines.**
Check with `cloc src/ --by-percent c`.

**Docstrings** (Google style):
- Every module: one-line purpose at the top
- Every public class: purpose + key behavior
- Every public method/function: Args, Returns/Yields, Raises
- ABC abstract methods: contract for implementers

**Inline comments** — only `why`, never `what`:
- Non-obvious logic, workarounds (with reasoning), edge-case
  rationale

**Never**:
- Comments that restate code (`counter += 1  # increment counter`)
- Commented-out code (git has history)
- Bare `TODO`s without context — write `TODO(why): what to do`

## Coding Conventions

- **Formatter**: `ruff format`
- **Linter**: `ruff check`
- **Types**: `mypy --strict`, annotate all public signatures
- **Async**: all I/O is async
- **Pydantic v2** for all structured data
- **ABC** for interfaces (Provider, Tool, Hook)
- **Specific exceptions** — define domain types (`ProviderError`,
  `ToolExecutionError`, `ConfigError`), never catch bare `Exception`
  at boundaries
- **One primary class per file** matching filename
- **< 300 lines per file** — split if longer
- **`__init__.py`** exports package public API

## Repo Hygiene

- Remove temp scripts, throwaway configs, empty dirs after use.
- No stray commented-out code.
- Verification-only scripts: delete when done.

## Language

All responses in Chinese or English.  Quote foreign-language source
material in original, then translate.
