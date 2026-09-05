## Context

See `proposal.md - Why` for the live incident (`go-20260904-153010`) and the
mechanism, already traced against the current worktree:

- `coordinator.py:49` — `TAIL_KINDS = {"e2e", "cleanup"}`; both are held out
  of the fan-out on `kind` alone.
- `compile.py:167` (`needs_compile`) — both are exempted from the file-scope
  requirement identically.
- `live.py:4429-4438` and `:5710-5714` — the live-run driver's per-tick loop
  derives `role` from the task's current status via `ROLE_BY_STATUS`
  (`orchestrate.py:60-67`) and, whenever `role == dispatch.ROLE_CLEANUP`,
  calls `cleanup_task_in_python(wt, task_id)` (`live.py:3723`) instead of
  spawning a worker. This fires for *every* task once it reaches status
  `cleaning` — including ordinary `impl` tasks, for whom it is intentionally
  a no-op replacement for what used to be a real formatting/lint pass (see
  that function's own docstring: writing task status into the spec tree on
  every task branch was the reason `integrate._strip_spec_folder_to_base()`
  had to exist).
- The defect is specific to a `[cleanup]`-tagged **tail task** (`kind:
  cleanup`, no `files:`), which starts at `status: pending` like any other
  task and is dispatched through `ROLE_IMPLEMENT` first
  (`dispatch.py:627-632`'s `is_noop_tail` branch renders its prompt as
  "verification-only; expect zero file changes"). For an `[e2e]` task, that
  `ROLE_IMPLEMENT` spawn is where the actual verification work — running
  tests, running `openspec validate`, etc — happens. For a `[cleanup]` task,
  the same `ROLE_IMPLEMENT` spawn happens too, but if the author instead
  wrote the imperative body as the task's whole content expecting it to run
  during the *final* `cleaning` step (matching the more common informal
  intuition of "cleanup = the last thing that happens to this task"), it
  never does: the task still passes through `ROLE_IMPLEMENT` once, but there
  is no reason to expect a worker asked to do "verification-only, zero file
  changes, kind: cleanup" to independently decide to run the commands in its
  own title rather than reporting immediate success — and the incident's
  0-second `done` transition shows that is exactly what happened.
- `compile.py:800`'s remediation text ("give them a tail kind
  (docs/e2e/cleanup)") is the contributing cause: it lists the three as
  interchangeable with no distinction of what each executes.
- `tail-dispatch-noop-and-pr-discovery-guard` (open, unmerged) touches
  `ROLE_CLEANUP` but only inside `dispatch.py:build_worker_prompt` — the
  prompt-rendering function. `live.py`'s short-circuit above never reaches
  that function for `role == ROLE_CLEANUP`, so the two changes are adjacent
  but disjoint (see proposal.md - Why for the full reconciliation).

## Goals / Non-Goals

**Goals:**
- Make it structurally impossible to author a `[cleanup]` task whose body
  is an imperative verification command without a compile-time rejection —
  catch the class at authoring time, before a run ever starts.
- Fix the compile guidance that currently presents `docs`/`e2e`/`cleanup` as
  interchangeable, so a future scope-gap resolution is steered toward the
  kind that matches what the task actually needs to do.
- Add regression coverage proving a verification-bodied `[cleanup]` task
  cannot pass compile.

**Non-Goals:**
- Changing `cleanup_task_in_python` or the `live.py` `ROLE_CLEANUP`
  short-circuit itself. That behavior is correct and deliberate for its
  actual purpose (the final no-op housekeeping step for a task whose real
  work already finished, and the reason `integrate._strip_spec_folder_to_base()`
  exists at all) — for both ordinary `impl` tasks and correctly-authored
  `[cleanup]` tail tasks (e.g. "remove debug logging left in tasks 1-4",
  which genuinely executes nothing and is journaled as `tests: "none"`
  correctly). This change stops the *mismatch* from being authored, not the
  no-spawn mechanics of a task correctly using it.
- Distinguishing "verified" from "state-transitioned" in the run journal or
  dashboard (the brief's suggested approach (c)) — a deeper observability
  change to `cleanup_task_in_python`'s report shape, independent of whether
  the authoring-time guard here exists, and explicitly called out in the
  triage brief as possibly its own change. Left as a follow-up; this change
  is scoped to preventing the mismatch from being authored at all, which
  the brief itself notes as the cheaper, narrower fix.
- Any change to `[e2e]` or `[docs]` task handling, dispatch, or guidance
  beyond the reworded scope-gap message.
- Touching `tail-dispatch-noop-and-pr-discovery-guard`'s files — confirmed
  disjoint (proposal.md - Why); no edits to that change's branch or specs.

## Decisions

### 1. New plan-shape rule inside `parallelism.shape_problems`, not a separate check function

`compile.py` already has exactly this shape of enforcement point: `shape_problems`
returns human-readable problem lines, `compile.py:499` raises `PlanShapeError`
when non-empty, and a rejected plan writes no `.compile-ok` marker
(`compile-plan-shape-gate`'s existing three rules — serial critical path,
same-file chain, missing test scope). A mismatched `[cleanup]` task is the
same category of problem: a plan shape that is superficially valid (compiles,
has no file-scope gap) but guarantees a wrong or misleading run. Adding a
fourth rule to the same function keeps one enforcement point and one error
surface instead of a second parallel gate with its own wiring into `main()`.

The existing three rules only ever look at `fanout = [t for t in merged if
t.get("kind") not in TAIL_KINDS]` — tail tasks are deliberately excluded from
that subset because the other three rules (critical path, same-file chains,
test-scope) don't apply to them. The new rule is the opposite: it only
applies *to* tail tasks (specifically `kind == "cleanup"`), so it iterates
`merged` directly rather than `fanout`.

Alternative considered: a new top-level check function in `compile.py`
itself (parallel to `needs_compile`/`unordered_file_collisions`), wired into
`main()` as a fourth error category alongside `gaps`/`collisions`/
`uncovered`. Rejected — those three are distinct failure *kinds* users can
independently resolve (missing scope, missing order, missing requirement
coverage); this is a plan-shape problem in the same sense the existing three
`shape_problems` rules are, and `PlanShapeError`'s existing "no marker
written, problems printed before any plan is returned" behavior is exactly
what's wanted here too.

### 2. Detection: `[cleanup]`-kind task whose title matches an imperative-run pattern

A task's `title` (the OpenSpec checklist line's text after tag-stripping,
already carried on the loaded task dict — `openspec/source.py:110`) is
checked against two independent patterns, both required to fire:

- an imperative verb — `\b(run|execute)\b`, case-insensitive
- a recognizable command — a backticked fragment (`` `[^`]+` ``), or one of
  the common test/validation invocations named in the brief's own incident
  example (`pytest`, `npm`, `yarn`, `jest`, `mocha`, `tox`, `openspec
  validate`, `python3? -m`)

Both must match so that an incidental use of "run" in prose (e.g. "cleanup
after the previous run") does not false-positive without also naming
something that looks like a command.

Alternative considered: reject any `[cleanup]` task whose title contains the
word "run" at all. Rejected as too blunt — the brief's own example of a
correctly-authored `[cleanup]` task ("remove debug logging left in tasks
1-4") does not use imperative-run language, but a plausible inert task like
"clean up after the review run" would false-positive on a bare keyword
match.

Alternative considered: parse the task body for a fenced code block instead
of a title regex. Rejected — OpenSpec checklist items are single lines with
no fenced-block convention (`taskformats/openspec/source.py`'s own docstring:
"Almost nothing else is [structured]... There is no per-task frontmatter");
a title-text heuristic matches what's actually available to inspect.

### 3. Guidance text: name what each tail kind executes, don't just list them

`compile.py:800`'s current line ("Give them a tail kind (docs/e2e/cleanup)
if they genuinely need none") is replaced with text naming the distinction:
`e2e` spawns a worker and runs commands; `cleanup` is a journal-only status
transition that executes nothing; `docs` likewise executes nothing. This is
advisory only (it fires on a *different* compile failure — a scope gap, not
a plan-shape rejection) but directly addresses the contributing cause the
brief identifies.

## Risks / Trade-offs

- [Decision 2: a title-regex heuristic] → A task phrased without any of the
  listed keywords or backticks (e.g. "confirm the build is clean") could
  still slip through untagged as a false negative. Accepted: the goal is to
  catch the class the incident demonstrated (an explicit `Run <tool>`
  imperative), not achieve perfect natural-language intent detection; a
  narrower true-positive rule that occasionally misses an obliquely-worded
  case is preferable to a broader one that blocks legitimate inert
  `[cleanup]` tasks.
- [Non-Goal: journal/dashboard observability (brief's (c))] → A `[cleanup]`
  task that slips past this guard (or one authored before this change
  merges) still reports indistinguishable "success" for "verified" and
  "state-transitioned only". Accepted per the brief's own framing — a
  separate, deeper change if pursued.

## Migration Plan

No data migration. Pure compile-time/text changes behind an existing gate
(`shape_problems` / `PlanShapeError`); no config, schema, or journal-format
changes. A change with a pre-existing, already-compiled `[cleanup]`
verification-bodied task will start failing `worktrail-compile --force` /
initial compile after this merges — the author retags it `[e2e]`, which is
the correct fix, not a regression. Rollback is a plain revert.
