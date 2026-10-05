## Context

`WORKER_TIMEOUT_DEFAULT` (`live.py:58`) is one number for every worker in a run, and the
per-task override that beats it is read at three sites (`live.py:2978`, `:5190`, `:5212`) off a
single key: `task["timeout"]`. The devkit adapter fills that key from task frontmatter; the
OpenSpec adapter -- the format every change in this repo is authored in -- does not fill it at
all, because `tasks.md` has no frontmatter and the parser's continuation-line vocabulary
(`files:`, `review:`, `depends:`) has no budget member. The mechanism is not missing from the
orchestrator; it is missing from one adapter, and the format whose adapter lacks it is the one
the orchestrator's own advisory message is addressed to.

The prior art for closing exactly this shape of gap is `openspec-task-file-declaration`: an
opt-in indented continuation line parsed tolerantly, carried onto the loaded task, with the
declaration syntax documented in the skill that drafts the artifact. `review:` and `depends:`
were added the same way and under the same constraints. This change is the third such line.

## Goals / Non-Goals

**Goals**

- A task authored in `openspec/changes/<id>/tasks.md` can declare its own headless-worker
  timeout budget, and that budget is what the run uses for that task.
- The convention for when to declare one is documented at the point `tasks.md` is written, not
  only in a message printed after the worker has already been killed.
- Both directions stay honest: absent or malformed declarations change nothing, and a
  `tasks.md` with no `timeout:` line compiles and runs exactly as it does today.

**Non-Goals**

- No change to `WORKER_TIMEOUT_DEFAULT`, `--timeout`, `ORCH_WORKER_TIMEOUT`, or the advisory
  text at `live.py:5264-5270`.
- No derived or automatic budget: no historical-duration floor, no automatic extension-and-retry
  for a tail task that timed out.
- No new pre-run warning for a tail task that declares no budget.
- No devkit-side change (`timeout:` frontmatter already works there), and no change to the run
  journal's recorded timeout.
- No audit of third-party repos whose e2e task bodies run a >10-minute suite (the brief's
  "same-class audit" suggestion): that names repos outside this one.

## Decisions

### D1. Teach the OpenSpec parser a `timeout:` continuation line, not the orchestrator a new knob

The alternative shapes the brief lists are all either already present or unfounded:

- **(a) run-wide `--timeout` for runs containing an e2e task.** The lever exists
  (`--timeout`, `ORCH_WORKER_TIMEOUT`) and the existing advisory already names it as the
  coarser option. Automatically applying it to any plan containing a tail task would silently
  raise every worker's budget in the run -- including the implementation workers that finish in
  minutes -- on a heuristic about the plan's shape.
- **(c) a floor derived from historical run durations.** There is no duration store to derive
  from. The run path's only budget signal is `getattr(spawn, "timeout")`; the journal records
  task events, not per-task wall-clock, and `parallelism.estimate_minutes` projects from task
  counts (`parallelism.journals_beside`), not from measured worker time. Deriving a floor would
  mean building the measurement infrastructure first, for a number the author of the change
  already knows and the orchestrator cannot.
- **(b) per-task convention.** The brief's option, and the one the code supports: the resolution
  it needs already exists and is format-agnostic. It is only unwritable in this format, which is
  the whole defect.

So the change makes (b) expressible. `timeout:` joins `files:`, `review:` and `depends:` as an
indented continuation line in the same window, and `source.py` carries it onto the task dict.

### D2. Parsed to a positive integer of seconds, normalised to `None` when it cannot be

The value is a count of seconds, matching the field's existing meaning in devkit frontmatter and
its unit at every read site (`live.py:2978`, `:5190`, `:5212`, and `spawnlib`'s
`subprocess.TimeoutExpired` handling). A non-integer value, a value `<= 0`, an empty value, or a
second `timeout:` line under the same task records a warning and leaves the task undeclared --
the same posture `files:`, `review:` and `depends:` already take, and the same normalisation
`devkit/source.py:274-275` applies to a non-positive frontmatter value. Silence is the same
outcome as absence on purpose: `task.get("timeout") or run_default` must keep falling back to
the run-wide default rather than to `0` (which `subprocess` reads as "no timeout at all").

### D3. `TaskDict` and the advisory are left alone

`taskformats/base.py:30` already declares `timeout: int | None` on `TaskDict`, so the adapter is
filling a typed field that was always part of the contract, not extending it. And the advisory's
guard -- `not task.get("timeout")` -- is already the correct predicate: once the field is
populated, a task that declared a budget stops receiving advice to declare one. That is the
observable end-to-end property this change is for, and it needs no edit to `live.py` to hold.

### D4. The convention goes in the propose skill, next to the sibling lines

The `openspec-propose` skill's tasks-artifact guidance is where `files:`, `review:` and
`depends:` are documented, and it is the step that produces a `tasks.md` (`pipeline-details.md`
step 1 routes OpenSpec drafting through it). A budget convention documented anywhere else is
documented where the author is not. The guidance states the trigger, not just the syntax: a task
whose body predictably exceeds the run-wide worker default -- an `[e2e]` task that runs the
repo's whole suite and its lint/golden wrappers -- declares a budget; a task that does not, does
not have to.

### D5. No pre-run warning for an undeclared tail budget

Considered and rejected. `precheck()` (`live.py:1289`) already prints `WARN:` lines that feed
the `#precheck-gate` ask, and it would be the natural place to catch the omission before a run
instead of after a 1800s kill. It was rejected because it cannot be made true: whether the
run-wide default is *adequate* depends on the repo's suite duration, which `precheck` -- a
non-spawning command run before the plan is executed and, in the pipeline, before the run-wide
`--timeout` is even chosen -- has no way to know. A warning on every tail task in every repo,
firing on runs where 1800s is perfectly sufficient, is noise the operator learns to skip, which
is worse than no warning. The post-timeout advisory remains the honest signal: it fires only
after a budget has actually proved too small for the work.

## Risks / Trade-offs

- **A typo'd `timeout:` line is silently advisory.** `timeout: 30m` warns and leaves the task on
  the run-wide default, so the author sees a 1800s kill and an advisory instead of the 30-minute
  budget they meant. Accepted: it is the posture `files:`/`review:`/`depends:` already have, and
  a hard parse error would refuse a `tasks.md` OpenSpec itself considers valid.
- **A declared budget can be smaller than today's effective budget.** A task declaring
  `timeout: 60` gets 60s where the run-wide default would have given it more. This is the point
  of a per-task field (and devkit already behaves this way), but it is a new way for an author
  to make a task fail earlier than it used to.
- **Reaching for the field when the real problem is the suite.** A budget is a ceiling, not a
  fix; a e2e task pushed to 3600s because the suite drifted from 770s to 1700s is now hiding that
  drift in a task artifact. Nothing here detects that -- it is not detectable from the
  artifacts at hand, and the alternative is the false-failed task the brief reports.

## Migration Plan

Additive and opt-in. No artifact must change for existing behaviour to hold: a `tasks.md` with no
`timeout:` line parses to `timeout: None` and takes the run-wide default exactly as it does
today, and an archived change is read from its own on-disk bytes as always. No migration step,
no version gate, no cache or journal invalidation -- the compile fingerprint covers the declared
file lists, and a `timeout:` line is not part of plan scope.
