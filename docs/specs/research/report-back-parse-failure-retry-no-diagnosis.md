# Investigation — report-back parse failures are diagnosable but not diagnosed

Route I (investigation) note. Source brief:
`20260821-184929-report-back-parse-failures-are` (repo unset — resolved to `worktrail`,
the owner of `src/worktrail/orchestrator/live.py`/`dispatch.py`).

Status: **investigation only — no code changes.**

## Problem, as reported

> Report-back parse failures are diagnosable but not diagnosed: `_journal_failure_entry`
> records only the exception text, and a retry re-sends the identical prompt with no
> corrective nudge.

## Verified observations

1. **`parse_report_back` already distinguishes four specific failure reasons**, each a
   distinct `ValueError` message (`dispatch.py:747-766`): no fenced ` ```json ` block or
   trailing `{...}` found at all; the extracted blob is invalid JSON
   (`json.JSONDecodeError`); the parsed JSON is missing one of `task`/`step`/`status`; or
   `status` is not `"success"`/`"failed"`. This is structured, diagnosable information at
   the point of failure.

2. **`_journal_failure_entry` (`live.py:1088-1118`) stores only the free-text exception
   string** in `report.notes` (e.g. `f"{task_id}/{role} report parse failed: {e}"`) — the
   four distinct reasons from (1) are flattened into one unstructured sentence with no
   machine-readable category field.

3. **For `ROLE_IMPLEMENT`, an unparseable report-back that `salvage_report` cannot recover
   (`live.py:2705-2726` — only fires when the worker left a new git commit) is journaled
   with `terminal_status="retryable"`; every other role gets `"failed"`**
   (`live.py:3464` and the duplicate call site `live.py:4544`).

4. **Both live in-process failure sites unconditionally set `task["status"] = "failed"`
   and `break` immediately after journaling** (`live.py:3481`, `live.py:4557`), regardless
   of whether `terminal_status` was `"retryable"` or `"failed"`. No re-dispatch happens
   within the same orchestrator process/run.

5. **The actual "retry" only happens on a fresh orchestrator invocation (a resume).**
   `reconcile_from_journal` (`live.py:751-799`) special-cases `terminal_status ==
   "retryable"` entries at line 786-789: it `continue`s without assigning any status to
   the task, leaving it at whatever status the freshly reloaded `TaskSource` reports —
   i.e. still runnable. Tasks are reloaded from the `TaskSource` (task frontmatter /
   `tasks.md`) on every resume, not restored from the journal's own state.

6. **`build_worker_prompt` (`dispatch.py:371-551`) has exactly one channel for injecting
   corrective context into a prompt: the `extra_reads` parameter**, rendered as `"widened
   context: prior worker reported this missing — read before fixing"` lines
   (`dispatch.py:482-486`). The caller (`LiveSpawn.__call__`, `live.py:2236-2261`) wires
   this **only for `role == dispatch.ROLE_FIX`**, popped from `task["_extra_reads"]`.

7. **`task["_extra_reads"]` is itself only ever set from a REVIEW report's
   `context_quality == "insufficient"` signal** (`live.py:3489-3492`, duplicated at
   `~4561-4564`) — never from a report-parse failure. No code path sets `_extra_reads` (or
   any other prompt input) from the `ValueError` raised in (1).

8. **Even if `_extra_reads` were set, it would not survive a resume**: `_journal_failure_entry`'s
   entry dict (observation 2) carries no such field, and tasks are reloaded fresh from the
   `TaskSource` on resume (observation 5) — `_extra_reads` is a plain in-memory dict key
   with no persistence path.

9. **Net effect: the retried IMPLEMENT worker's prompt is byte-for-byte identical to the
   failed attempt's.** Same task dict, same `ctx`, `extra_reads=[]` (role is IMPLEMENT, not
   FIX) — `build_worker_prompt` is a pure function of those inputs, and none of them differ
   between the failed attempt and the resumed retry. This confirms the brief's claim
   literally, not just in spirit.

10. **No retry cap or circuit breaker applies to this failure mode.** The review/fix loop
    has `MAX_REVIEW_RETRIES = 3` with an escalation transition (`dispatch.py:78`,
    `transition()` at `dispatch.py:772-798`, called from `apply_report` at `dispatch.py:814`).
    `reconcile_from_journal` explicitly bypasses `dispatch.apply_report` for `"retryable"`
    entries (observation 5's `continue`), so `retry_count` is never incremented for a
    parse-failure retry — `grep -rn "retryable"` outside `live.py` returns nothing, and
    `retry_count` is touched at 6 other sites (`dispatch.py:789/814/816/833`,
    `live.py:2421/3132/4980/5685`), none of them on this path. An IMPLEMENT task that
    fails to produce a parseable report-back on every attempt can be resumed indefinitely
    with zero escalation.

11. **These failures are demonstrably root-cause diagnosable in practice, not just in
    theory.** A documented comment (`live.py:178-197`) records a concrete, already-fixed
    root cause: a user-level Stop hook forced an extra continuation turn that dropped the
    trailing ` ```json ` block, so `parse_report_back` saw a clean `end_turn` with no
    report-back at all. It was diagnosed (investigation `20260711-130900`) and fixed via
    `_LEAN_WORKER_FLAGS`'s `--setting-sources project,local`. The gap this brief reports is
    that this diagnosis-and-fix cycle happens only when a human notices and investigates —
    nothing in the retry path itself surfaces or acts on the distinguishable reason at the
    moment of each individual occurrence.

## Unknowns / missing evidence

- **Production frequency** of `"retryable"` IMPLEMENT parse failures since the Stop-hook
  fix landed — no journal corpus or telemetry was scanned in this investigation; observation
  10's "indefinite retry" risk is a structural finding, not a measured incident count.
- **Whether resume is currently manual or automated.** No code in this package auto-resumes
  a `full-real`/orchestrator run after a `"retryable"` outcome (observation 10's grep found
  no consumer of the string outside `live.py`) — resume today appears to require an external
  invocation (a human, or some other repo's automation), but this investigation did not
  search consuming repos (`datalena`, `gracefully-giving-back`, etc.) for a wrapper that
  does this.
- **Existing test coverage** of the retryable-resume path — not checked in this pass.

## Confirmed root cause

Report-back parse failures for `ROLE_IMPLEMENT` are already diagnosed with specific,
distinguishable reasons at the point of failure (`parse_report_back`'s four `ValueError`
messages), but that diagnosis is discarded down to an unstructured string in the journal
and never reaches the retried worker: `build_worker_prompt`'s only corrective-context
channel (`extra_reads`) is wired exclusively to the REVIEW→FIX `context_quality=insufficient`
signal, is never populated from a parse failure, and — even if it were — is an in-memory-only
field that would not survive the resume boundary where the actual retry happens (tasks are
reloaded fresh from the `TaskSource`, not restored from journal state). Consequently every
retry of a report-back parse failure re-sends an identical prompt with no indication that the
previous attempt failed to produce a parseable report-back, why, or what to do differently —
and, separately, this specific failure mode has no retry-count cap or escalation, unlike the
review/fix loop's 3-strike circuit breaker.

## Recommended next route

**Route F (defect-repair)** — as a separate follow-up, not continued in this run. This is not
"minimal diagnostics": a real fix requires deciding (a) how/where the parse-failure reason
persists so it survives a resume (the journal entry itself, since `_extra_reads`-style
in-memory fields don't survive; e.g. reading the most recent `"retryable"` journal entry for a
task back out in `reconcile_from_journal` or at prompt-build time and feeding it into
`build_worker_prompt` for `ROLE_IMPLEMENT` the way `_extra_reads` feeds `ROLE_FIX`), (b) what
corrective text to render for each of `parse_report_back`'s four distinct reasons, and (c)
whether to add a retry-count/circuit-breaker for this failure mode to match the review/fix
loop's behavior (observation 10) — three related but separable design decisions that exceed
Route I's "no code changes except minimal diagnostics" scope. Do not open a follow-up handoff
for this — the next action is fully specified above.
