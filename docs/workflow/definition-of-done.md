# Definition of Done

> **One-page checklist.  Do not claim an implementation is complete
> until every box here is ticked.**

This document exists because "done" has been misreported three times
in this project (Skill / Task / Phase-1-CC).  Each failure was the
same shape: unit tests passed, the implementation report went out,
and a real-system probe caught a bug the mocks could not see.  This
checklist is the last line of defence.

---

## The five gates

An implementation is **complete** when and only when:

1. **Unit tests pass** — `uv run pytest tests/kernel/ -q` returns
   green.  Required but not sufficient.

2. **Every closure seam has a real-system probe.**  A closure seam is
   any callable wired across subsystem boundaries (`_make_X_closure`,
   `ctx.Y = fn`, `deps.Z = fn`, callbacks threaded through
   OrchestratorDeps / ToolContext / HookEventCtx).  For each seam
   introduced or modified by the change, there is a
   `scripts/probe_<name>.py` or `tests/e2e/test_<name>_e2e.py` that
   **invokes the real subsystem** — not a mock.
   See [`workflow.md`](workflow.md#phase-45--closure-seam-inventory-mandatory-hardest-to-catch-bugs-live-here)
   Phase 4.5 for the enumeration procedure and worked examples.

3. **Each probe has been run and its output pasted into the
   completion report.**  Output must show the assertion passed
   against the real dependency (e.g. `post_processed=True` from
   Bedrock, `backend=hook` from a real HookManager).  "Tests pass"
   as a claim without output is not accepted.

4. **Existing E2E suites still pass.**  `uv run pytest tests/e2e/
   -q -m e2e` (for the suites touching changed subsystems) returns
   green.  Pre-existing unrelated failures should be called out by
   name so the reviewer can confirm they are not regressions.

5. **Docs are in sync with code.**  If the implementation diverged
   from the plan, the relevant files under `docs/` are updated in
   the **same** commit — `docs/plans/progress.md`, subsystem docs,
   any design docs referenced from code comments.

If ANY of these five is missing, the change is not complete.  Reporting
it as complete is a bug.

---

## Mock vs. probe — a disambiguation

**Mock test** (unit test):

```python
from unittest.mock import AsyncMock
summarise_mock = AsyncMock(return_value="SUMMARISED")
ctx = ToolContext(..., summarise=summarise_mock)
await tool.call(...)
summarise_mock.assert_awaited_once()       # ← proves call shape
```

Proves: "my closure calls its dependency with these args in this order".
Does NOT prove: "the real dependency accepts these args in this order".

**Probe test** (end-to-end or integration):

```python
# scripts/probe_webfetch_compact.py
llm_mgr = LLMManager(mt)
await llm_mgr.startup()                     # ← real subsystem
summarise = _make_summarise_closure(llm_mgr)
ctx = ToolContext(..., summarise=summarise)
async for ev in tool.call({...}, ctx):
    result = ev
assert result.data["post_processed"] is True  # ← real Bedrock replied
```

Proves: "this closure, against the real dependency, produces the
observable outcome we expect".  This is what Phase 4 / Phase 4.5
require.

**If your test file imports `AsyncMock` / `MagicMock` to stand in
for the target subsystem, it is not a probe.**  No exceptions.

---

## Anti-patterns that produced the three violations

1. **"The mock test is my probe."**  No.  Rename it `test_<feature>.py`
   under `tests/kernel/`, and write a separate
   `scripts/probe_<feature>.py` against the real thing.

2. **"The existing e2e suite covers this."**  Verify.  Grep the e2e
   suite for the new seam.  If nothing matches, existing coverage
   does not include your change.

3. **"Running the probe needs real API keys / external config, so
   I'll skip it."**  The user's machine has the keys.  Write the
   probe as a standalone script that reads user config, run it
   interactively once, paste the output.  If the probe cannot run
   in CI, that is fine — its job is to gate completion, not to run
   continuously.

4. **"It's just a one-line wiring change, probing it is
   overkill."**  The three Phase-1-CC bugs were each a one-line
   wiring issue.  The riskiest changes in a codebase are the ones
   the author thinks are too small to verify.

---

## Practical workflow

When wrapping up an implementation:

```
1. List closure seams I wrote or changed
2. For each: does a real-system probe exist?
   - If yes: run it, paste output
   - If no: write one, then run it, then paste output
3. Post the probe output verbatim in the report
4. Only then say "done"
```

If you catch yourself about to skip step 2 because "the mock test
passed" — **stop**.  That thought pattern has caused every
violation of this rule so far.  Write the probe.
