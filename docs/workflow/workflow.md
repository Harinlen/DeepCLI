# Development Workflow

Every implementation step follows these phases in order, no skipping.

## Phase 1 — Plan

1. Read the relevant design doc under `docs/kernel/subsystems/`
   or `docs/plans/backlog.md` for deferred features.
2. **Use Claude Code as the blueprint.**  Before designing, look up
   the corresponding feature in Claude Code's source
   (`./resolve-ref.sh claude-code`).  Understand its module
   layout, interfaces, and data flow.  Adapt to DeepCLI's
   architecture — don't invent from scratch.
3. Explain the plan to the user: which Claude Code modules inspired
   it, how it maps onto DeepCLI.
4. **Discussion phase: no code.** Describe behavior, inputs, outputs,
   branches in prose. Function signatures / class definitions /
   implementation details wait for Phase 2. Code distracts from
   evaluating the plan.
5. Iterate with the user on the plan.
6. **Wait for explicit "start coding" from the user** before writing
   code.

## Phase 2 — Implement

7. Follow the agreed plan.  **When the implementation diverges from
   the plan** (different interface, renamed module, dropped/added
   step, changed data flow), update the relevant doc **immediately,
   in the same phase** — don't defer to Phase 6.  Docs that commonly
   drift:
   - The active plan file itself (mark what changed and why)
   - `docs/kernel/` (module boundaries, data flow)
   - `docs/kernel/` subsystem docs (interfaces, contracts)
   - Docstrings / inline references to design docs
8. If this implementation changes shipped behavior, increment
   `kernel.__version__` in `src/kernel/kernel/__init__.py` during the
   same workstream.  Do not leave version bumping to a later cleanup;
   probes and user reports need the version that actually contains
   the implementation under test.

## Phase 3 — Unit tests

9. **Does it run?** — no import/syntax errors, kernel starts cleanly.
   Debug until it does.
10. **Boundary defence** — unit-test edge cases: bad input, missing
   files, network failure, empty collections, concurrent access.
   Each boundary has a test that asserts the correct behaviour rather
   than a crash.
11. **Every plan module has a test file.**  Cross-check your plan's
    module list against `tests/`.  If the plan says "new file X",
    there must be a corresponding `test_X.py`.  Missing test files
    mean missing coverage — not "tested elsewhere".
12. **Every public function's return type is asserted.**  If a
    function returns a dataclass / dict / object, the test must
    construct a real call and assert the returned fields — not just
    that it "doesn't crash".  This catches missing constructor
    arguments (like a required `display` field) that mypy may miss
    when the caller uses `Any`.

## Phase 4 — End-to-end verify (**mandatory, do not skip**)

13. **Feature correctness** — for every requirement in the plan, trace
    the full path: given a concrete input, does the system produce the
    expected output?  Verify against the actual running code, not just
    a reading of it.
    - **Use `probe` for end-to-end verification.**  Drive the live
      kernel through its real ACP interface (see
      `tests/e2e/test_kernel_e2e.py` for the canonical
      pattern).  Ad-hoc REPL pokes don't count.
    - **Must go through a real kernel subprocess.**  E2e tests start
      the kernel via ``subprocess.Popen`` (not by directly
      instantiating subsystems in-process) so the full ``app.py``
      lifespan executes — startup order, ConfigManager binding,
      signal wiring, and subsystem dependencies are all exercised.
      Tests that bypass the lifespan by constructing subsystems
      directly are **integration tests**, not e2e, and do not
      satisfy this phase.
    - **Preserve the check as a script.**  Add a new test function to
      `tests/e2e/` (or a new `test_<feature>_e2e.py` file)
      covering each plan requirement.  These files are the regression
      suite — the next feature re-runs them to prove it didn't break
      anything already shipped.  Never delete an e2e test just because
      the feature is "done".
    - **When e2e fails, fix both sides.**  If a probe e2e test
      exposes a kernel bug (e.g. startup order, missing signal
      wiring), fix the kernel code and re-run the e2e until it
      passes.  If the probe client is missing capability to test a
      feature (e.g. cannot handle a new event type), extend probe
      first.  The e2e must pass against the real kernel — not be
      weakened to match a broken implementation.
    - **E2E must exercise the actual code path, not just "no crash".**
      A test that sends a prompt and only asserts
      ``stop_reason == "end_turn"`` does not verify the feature.
      E2E tests must assert on **observable output**: returned text
      contains expected content, tool calls appear in the event
      stream, specific errors are returned for invalid input.
      "Kernel didn't crash" is a smoke test, not an E2E test.
    - **E2E coverage checklist** — for each new feature, the E2E
      suite must cover at minimum:
      1. **Happy path**: the feature works end-to-end with valid input.
      2. **Error path**: invalid input produces the expected error
         (not a crash).
      3. **Integration path**: the feature interacts correctly with
         adjacent subsystems (e.g. SkillTool → SkillManager →
         PromptBuilder).
    - **Gate rule:** Do not proceed to Phase 5 until all e2e tests
      pass.  A feature that cannot run end-to-end is not implemented.
    - **TodoWrite gate:** When constructing the initial TodoWrite
      checklist at the start of Phase 2, E2E test items **must** appear
      as explicit entries positioned **before** the quality-check entry.
      E2E is not a sub-bullet of "quality checks" — it is its own
      top-level deliverable.  If the TodoWrite list does not contain
      E2E items, add them before writing any code.

### Phase 4.5 — Closure-Seam Inventory (mandatory, hardest-to-catch bugs live here)

> **Added 2026-04-22 after the third repeated violation.**  This section
> exists because mock tests of closure wirings have shipped real bugs
> three times: Skill/Task/Phase-1-CC.  Read [`definition-of-done.md`](definition-of-done.md)
> before claiming Phase 4 complete.

A "closure seam" is any place where your change wires a callable across
subsystem boundaries.  High-risk patterns to enumerate:

- `_make_X_closure(...)` helpers that return async functions
- `ctx.Y = some_fn` / `deps.Z = some_fn` assignments
- Callback parameters threaded into OrchestratorDeps, ToolContext,
  HookEventCtx, or any other inter-subsystem dataclass
- Adapter functions bridging two subsystems' interfaces
- `_fire_hook`, `_summarise`, `_deliver_*` style factory functions

These are the highest-risk bugs in the codebase because:

- **Type checker cannot catch them.**  `async def` returning a
  generator vs. a regular async generator, sync vs. async handlers,
  arg-arity mismatches — all type-check green.
- **Mock tests pass even when the real dependency would reject
  the call.**  Mocks accept any payload shape; real subsystems do
  not (empty system text, missing suffix in model ID, required
  headers, auth tokens).
- **Protocol requirements live in the real dependency.**  You cannot
  discover them by reading your own code.

15. **Enumerate every closure seam introduced or modified in the change.**
    Write the list down before declaring the change complete — mentally
    or in the TodoWrite list.  Include the seam's caller subsystem and
    callee subsystem.

16. **For each seam, produce a probe against the real dependency.**
    Either:
    - A `scripts/probe_<name>.py` that boots enough of the kernel to
      invoke the real subsystem and prints observable evidence that
      the closure succeeded (e.g. `scripts/probe_webfetch_compact.py`
      hits real Bedrock and asserts `post_processed=True`); or
    - A `tests/e2e/test_<feature>_e2e.py` that drives the change via
      ProbeClient against a live kernel (e.g.
      `tests/e2e/test_web_fetch_compact_e2e.py`).

17. **Run the probe.  Paste the relevant output into the completion report.**
    Not pasting the probe output is the failure mode — the user does not
    believe "probe passed" without seeing it.

18. **Mock tests are NOT probes.**  If your test file imports
    `unittest.mock.AsyncMock` / `MagicMock` to stand in for the target
    subsystem, it is a unit test.  Unit tests are necessary but do not
    satisfy Phase 4.5 for closure seams — they only prove your mental
    model of the dependency is internally consistent.  They cannot
    prove the real dependency accepts what you send.

**Worked example — Phase 1 CC alignment bugs that mock tests missed:**

```
Seam 1: session._make_summarise_closure → LLMManager.stream()
  Mock test:   ✓ passes (mock accepts any iteration shape)
  Real probe:  ✗ failed — async for on a coroutine, must await first
  Fix:         stream = await llm_manager.stream(...); async for x in stream

Seam 2: same closure → Bedrock payload
  Mock test:   ✓ passes (mock accepts empty system text)
  Real probe:  ✗ failed — "system: text content blocks must be non-empty"
  Fix:         non-empty PromptSection text

Seam 3: tool_executor._fire_hook → HookManager.fire(ctx)
  Mock test:   ✓ passes (mock accepts any arg arity)
  Real probe:  ✗ failed — TypeError: fire() takes 1 arg, 2 given
  Fix:         pass ctx only; ctx.event is read internally
```

All three shipped with mock-only coverage.  All three were caught at
`scripts/probe_webfetch_compact.py` and `scripts/probe_worktree_hook.py`
the first time they ran.

## Phase 5 — Quality check

13. Run the tool chain:

    ```bash
    uv run ruff format src/
    uv run ruff check src/
    uv run mypy src/
    uv run pytest --cov=src tests/
    cloc src/ --by-percent c         # comment density 20–25%
    uv run bandit -r src/ -q
    ```

14. Run the full checklist in
    [`code-quality.md`](code-quality.md):
    verify-design → tests → root-cause fixes → refactor pass →
    comment density.

## Phase 6 — Report

15. Update `docs/plans/progress.md` with completed step, new kernel
    version, and any new
    findings.  Route gotchas to `docs/lessons-learned.md`.
16. **Ensure design doc is in its final location.**  The feature's
    design doc should live in `docs/kernel/subsystems/<name>.md`.
    - Update all references (grep for old paths across `docs/`
      and `src/`).
    - Update `docs/plans/roadmap.md` status if applicable.
    A design doc that stays in `pending/` after its code has shipped
    is a broken link waiting to confuse the next reader.
17. Report completion to the user.
18. **Do NOT commit or push.  Wait for user instruction.**
