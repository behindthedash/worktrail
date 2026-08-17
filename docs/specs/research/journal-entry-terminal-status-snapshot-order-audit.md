# Investigation: journal-entry-construction sites for PR #496's snapshot-before-compute bug shape

**Triggered by:** work-queue brief `20260817-073955`. PR #496 (2026-08-16/17) fixed
a bug where `_commit_step`'s two copies (`live_run_real`, `full_real`'s
`_pipeline_scheduler`) built the journal entry's `report` fields from raw report
data **before** calling `dispatch.apply_report()` to compute the state transition —
so the review entry that actually tripped the 3-strikes circuit breaker
(`dispatch.transition` returning `"escalated"`) never carried
`report.terminal_status="escalated"`. `clear_tasks()`'s literal `terminal_status`
match then found nothing for that entry and refused with "nothing to clear",
forcing `--fresh` (full journal discard) as the only workaround.

**Question:** does the same snapshot-before-compute shape exist at any other
journal-entry-construction site in `src/worktrail/orchestrator/live.py`?

## Verified Observations

Every place `live.py` appends a journal entry (`entries.append(...)` / a `"report":
{...}` dict literal), enumerated by grep:

| Site | Function | Status |
|---|---|---|
| `_apply_step_commit` (was two duplicated `_commit_step` closures pre-#498) | `live_run_real`, `_pipeline_scheduler` (via `_commit_step`) | **Already fixed** — PR #496 fixed both closures independently; PR #498 (same day) deduped them into this one shared helper. Calls `dispatch.apply_report()` first (line 2718), then stamps `terminal_status="escalated"` onto `report_fields` before building the entry (lines 2719–2725). |
| `_journal_failure_entry` — 8 call sites (timeout, report-parse-failure, drive-crash, dependency-gate, manual-skip) | `live_run_real`, `_pipeline_scheduler`, `skip_tasks` | **Not affected.** `terminal_status` is a direct parameter computed synchronously at each call site (e.g. `terminal_status = "retryable" if role == ROLE_IMPLEMENT else "failed"` immediately before the call) — there is no separate, later status-determining call whose result could be missed. |
| `live_run`'s inline entry dict (`entries.append({"task": ..., "report": {k: rep.get(k) for k in orchestrate._REPORT_FIELDS}, ...})`) | `live_run` (the cassette/demo-recording path behind `orchestrate live-run` / `orchestrate full`) | **CONFIRMED — same bug shape.** See below. |

### `live_run()` — confirmed match

`live_run()` is a separate, independent implementation from `live_run_real()` (not
a caller of it, not migrated onto `_apply_step_commit` by #498 — see its own
docstring: "the cassette/demo recording path (`live_run`), not a production run
path"). Before this fix, its `drive()` closure built the entry dict directly from
`{k: rep.get(k) for k in orchestrate._REPORT_FIELDS}` and only called
`dispatch.apply_report(tasks, rep, role)` **afterward**, with no `terminal_status`
stamping logic at all — strictly worse than the pre-#496 `_commit_step`, which at
least stamped `terminal_status` onto *downstream* dependency-gate entries even
though it missed the originating one.

`_REPORT_FIELDS` does not include `terminal_status` (confirmed:
`orchestrate._REPORT_FIELDS` lists only response-echoed fields — `status`,
`head_sha`, `tests`, `review_status`, `critical_issues`, `major_issues`, `notes`,
`context_quality`, `missing_context`), so a `live_run()`-produced journal's
escalated review entry would never carry `terminal_status` under any code path
that reaches this file.

Reproduced with a regression test (`AlwaysFailReviewSpawn` against `live_run`,
`only=["TASK-001"]`, no dependencies) that naturally trips the 3-strikes circuit
breaker: before the fix, `live_run()` could not even be exercised this way — a
second, unrelated pre-existing bug (`_annotate_external_deps(repo, tasks,
spec_rel)` referencing an undefined `spec_rel` name; the function has no such
parameter, only `SAMPLE_SPEC_REL`) crashed every call with `NameError` before
reaching a second tick. Fixed alongside the terminal_status ordering (same
function, blocking verification of the primary fix) — see
`tests/orchestrator/test_live_run_circuit_breaker_terminal_status.py`.

### Blast radius of the `live_run()` finding

`live_run()` is reachable only via `orchestrate.py`'s `live-run` and `full` CLI
subcommands, both scoped to `SAMPLE_TEMPLATE`/`DEFAULT_DEST` — the demo/golden-
cassette-recording path documented in this repo's `AGENTS.md` as `python3 -m
worktrail.orchestrator.orchestrate check` (record/replay regression), not the
production fan-out path (`live_run_real`/`full_real`, reachable via `live-run-real`
/`full-real`, driven by real specs and real `clear_tasks()` calls). No user-facing
`clear_tasks()` workflow depends on a `live_run()`-produced journal today, so this
was latent rather than an observed production symptom (unlike #496, "observed live
on datalena change 077-..."). Still the identical bug class the brief asked to
audit for, in the same file, so fixed rather than left as a lower-priority
follow-up — the fix is a direct mirror of `_apply_step_commit`'s already-reviewed
pattern.

## Confirmed Root Cause

`live_run()`'s `drive()` closure snapshotted the journal entry's `report` fields
from raw report data before calling `dispatch.apply_report()`, and never stamped
`terminal_status="escalated"` at all — the same "assemble before the
status-determining call" shape as pre-#496 `_commit_step`, independently present
because `live_run()` was never migrated onto the shared `_apply_step_commit`
helper.

## Fix

Reordered `live_run()`'s `drive()` to call `dispatch.apply_report()` first, then
build `report_fields` and stamp `terminal_status="escalated"` when the transition
escalates — verbatim mirror of `_apply_step_commit`'s already-established pattern.
Also fixed the pre-existing `spec_rel` `NameError` in the same function (unrelated
to the terminal_status bug, but blocked exercising `live_run()` at all).

No other journal-entry-construction site in `live.py` carries this bug shape.
