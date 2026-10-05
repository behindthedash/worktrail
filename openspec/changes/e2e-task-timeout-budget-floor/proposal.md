## Why

The orchestrator already knows a run-wide worker timeout is the wrong budget for a tail
verification task, and says so -- but only after killing one, and in this repo's own authoring
format the remedy it names cannot be expressed.

**1. The advisory names a remedy that OpenSpec-format changes cannot use.**

`live.py:5257` branches on `task.get("kind") in coordinator.TAIL_KINDS and not
task.get("timeout")` and, for a tail task with no per-task budget, prints (`:5264-5270`):
*"Size its budget to the suite with a `timeout: <seconds>` field on the task itself; --timeout
raises it for every worker in the run."*

For devkit format that field is real: `taskformats/devkit/schema.py:61` declares
`"timeout": {"type": int, "required": False}` and `taskformats/devkit/source.py:267-275`
parses it off task frontmatter, normalising a non-positive value to `None` so
`task.get("timeout") or run_default` falls back correctly. The resolution that consumes it is
likewise real and format-agnostic -- `live.py:2978` (`effective_timeout = task.get("timeout")
or self.timeout`), re-read for the start banner at `:5190` and for the timeout message at
`:5212` -- and `taskformats/base.py:30` already types `timeout: int | None` on `TaskDict`.

The OpenSpec adapter never populates it. `taskformats/openspec/schema.py` recognises exactly
three indented continuation lines -- `FILES_RE` (`:64`), `REVIEW_RE` (`:74`), `DEPENDS_RE`
(`:83`) -- and `taskformats/openspec/source.py:111-124` builds each task dict from that parse
with no `timeout` key. `grep -rn timeout src/worktrail/taskformats/openspec/` returns nothing.

So for every change authored in this repo's own format -- `openspec/changes/<id>/tasks.md` --
`task.get("timeout")` is structurally `None`, the per-task override at `live.py:2978` is
unreachable, and the one remedy the orchestrator prints is inert. The advisory fires on exactly
the runs where it cannot be obeyed.

**2. The cost of that, reproduced.** Work-queue brief
`20261004-110450-e2e-task-timeout-too-low`: run `go-20261004-093132` (change
`model-tier-routing-env-profile-error-provenance`) had its `2.1` `[e2e]` verification task
killed at `WORKER_TIMEOUT_DEFAULT` (`live.py:58`,
`int(os.environ.get("ORCH_WORKTIMEOUT", "1800"))` -- 1800s) and marked failed, on a green
suite. `PYTHONPATH=src python3.14 -m pytest -q` alone measured 770.79s (7147 passed) on this
host; the worker also runs the targeted suites, the golden `orchestrate check` and the pinned
lint wrappers the task text asks for, plus agent overhead. ~1800s of worker time and one
false-failed task bought a message that named an unexpressible field.

The brief's own option (b) -- "add a per-task `timeout: <seconds>` to verification tasks
authored in this repo and document the convention where tasks.md is drafted" -- therefore needs
the mechanism to exist first. Options (a) and (c) are already satisfied or unfounded: the
run-wide `--timeout` / `ORCH_WORKER_TIMEOUT` lever exists and is named in the same message, and
a derived historical floor has no duration store to derive from (`getattr(spawn, "timeout")`
is the only budget signal in the run path).

## What Changes

- **`tasks.md` gains a `timeout:` continuation line.** The OpenSpec checklist parser recognises
  an indented `timeout: <seconds>` line in the same continuation window as `files:`, `review:`
  and `depends:`, parses it to a positive integer of seconds, and surfaces it as the task's
  `timeout`. Malformed declarations degrade the way the sibling lines already do -- a warning
  plus treatment as undeclared, never a hard parse error.
- **The adapter carries it onto the task dict.** `OpenSpecTaskSource.load()` emits
  `"timeout": t.timeout` so the existing per-task override resolution picks it up unchanged:
  a declared budget outranks `--timeout`/`ORCH_WORKER_TIMEOUT` for that one task, and a tail
  task that declares one no longer trips the run-wide-default advisory at `live.py:5257`.
- **The convention is documented where `tasks.md` is drafted.** The bundled
  `openspec-propose` skill's tasks-artifact guidance gains the `timeout:` syntax alongside
  `files:`, `review:` and `depends:`, and states the rule: a task whose body predictably
  exceeds the run-wide worker default -- notably an `[e2e]` task that runs the repo's whole
  suite and its lint/golden wrappers -- declares its own budget.

Deliberately unchanged: `WORKER_TIMEOUT_DEFAULT` stays at 1800s (a global bump taxes every
repo and every task for one repo's suite length, and is not the lever the orchestrator's own
message points at); the advisory text at `live.py:5264-5270` stays as written (once the field
is expressible, "a `timeout: <seconds>` field on the task itself" is accurate for both
formats); no new pre-run warning, no automatic budget extension, no CLI flag.

## Capabilities

### New Capabilities

- `openspec-task-timeout-declaration`: an OpenSpec `tasks.md` task may declare the headless
  worker's timeout budget inline, and the authoring guidance states when it must.

### Modified Capabilities

## Impact

- `src/worktrail/taskformats/openspec/schema.py`: a `TIMEOUT_RE` beside the three existing
  continuation-line patterns, a `timeout: int | None` field on `ParsedTask`, and the
  declaration branch in `parse_tasks_md`'s follow-line window.
- `src/worktrail/taskformats/openspec/source.py`: `"timeout": t.timeout` in the task dict
  built by `load()`.
- `skills/openspec-propose/SKILL.md`: the tasks-artifact guidance and its pre-handoff re-check
  list.
- `tests/taskformats/openspec/test_openspec_schema.py` and
  `tests/taskformats/openspec/test_openspec_source.py`.
- No change to `live.py`, `compile.py`, the devkit adapter, `TaskDict`, the run journal schema,
  or any CLI. (Work-queue brief `20261004-110450-e2e-task-timeout-too-low`.)
