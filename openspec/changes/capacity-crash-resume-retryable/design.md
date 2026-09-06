## Context

Traced against this worktree:

- `live.py:1371-1392` `_journal_failure_entry(task, role, reason, t0, t1, terminal_status="failed", blocked_by=None)`
  builds the journal entry; `terminal_status` lands at `entry["report"]["terminal_status"]`.
- `live.py:912-918` (replay): `if task and terminal_status == "retryable": continue` -- the task
  keeps whatever status prior entries gave it (`pending` for a first-attempt crash). Otherwise
  `if terminal_status in orchestrate.TERMINAL: task["status"] = terminal_status`.
- `live.py:4612` (`live_run_real`) and `live.py:5851` (pipeline scheduler) hold the two
  `_safe_drive` bodies. They differ cosmetically (`drive` vs `_drive`, `record` vs `_record`, the
  scheduler's has no `_publish_actives()`), but the journaling call is identical.
- `live.py:4528` / `:5781` already choose `"retryable" if role == dispatch.ROLE_IMPLEMENT else
  "failed"` for an unsalvageable report-back parse failure -- the existing precedent for
  "journal it, but let resume try again".
- `runtime/selection.py:49` `class NoExecutionTarget(SelectionError)` -- "Every compatible
  configured candidate/cell is capacity gated"; its message already lists each attempted cell
  with its gate class and retry time.

## Goals / Non-Goals

- **Goal**: a run interrupted by capacity exhaustion resumes cleanly once capacity returns, with
  no `--fresh` and no surgical `clear_tasks`.
- **Goal**: exactly one place decides the mapping, so the two `_safe_drive` copies cannot drift.
- **Non-goal**: in-run recovery (parking/retrying a gated task inside the same run). Capacity
  windows are typically longer than a run; resume is the right granularity.
- **Non-goal**: broadening `retryable` to other exception types. Anything unrecognized keeps
  today's terminal `failed`, so an unclassified bug cannot silently loop forever across resumes.

## Decisions

### D1: One classifier helper, keyed on the exception type

Add at module level in `live.py`:

```python
def _crash_terminal_status(exc: BaseException) -> str:
    """Journal terminal_status for an exception escaping a task's drive loop.

    Capacity exhaustion (every cell in the row gated) is not a task failure: replay
    bakes any non-"retryable" status back onto the task on every resume, so recording
    it as "failed" strands the task until --fresh or clear_tasks. Everything else keeps
    the terminal "failed" default.
    """
    from ..runtime.selection import NoExecutionTarget

    return "retryable" if isinstance(exc, NoExecutionTarget) else "failed"
```

The import is function-local, matching the existing lazy `from ..runtime.selection import
NoExecutionTarget as _NoExecutionTarget` at `live.py:2908` (`live.py` deliberately does not
import `runtime.selection` at module scope).

`isinstance` rather than an exact type check: any future capacity-exhaustion subclass inherits
the right behavior. It is deliberately *not* widened to `SelectionError` -- `InvalidCandidate`
(an override naming a model absent from the catalog) is an operator configuration error that a
resume would hit identically, so it must stay terminal.

### D2: Both `_safe_drive` bodies call it; only the journal changes

In each wrapper:

```python
except Exception as e:  # noqa: BLE001 -- isolate one worker's failure
    now = time.time()
    terminal_status = _crash_terminal_status(e)
    if terminal_status == "retryable":
        print(f"{_ts()}   !! {task['id']} no capacity: {e} -- will re-dispatch on resume")
    else:
        print(f"{_ts()}   !! {task['id']} drive crashed: {e!r} -- marking failed")
    with state_lock:
        task["status"] = "failed"
        entries.append(
            _journal_failure_entry(
                task, "drive", f"drive crashed: {e!r}", now, now,
                terminal_status=terminal_status,
            )
        )
        ...
```

The journal `notes` text stays `drive crashed: {e!r}` verbatim in both branches:
`test_live_manual_recovery.py` matches on that prefix, and `clear_tasks`' diagnostics read it.
Only the console line and `terminal_status` differ.

### D3: In-run status stays `failed`

`task["status"] = "failed"` is unchanged. Within the run, every cell that could serve the task is
gated, so leaving it `pending` would only have `runnable_frontier` hand it straight back to a
worker that raises again in a tight loop; and dependents must stay blocked rather than be
dispatched into the same gate. The journal is the resume contract, and it now says `retryable`.

The consequence is intentional and worth stating: a capacity-gated task's *dependents* are
recorded by the normal dependency-gate path for this run, and become pending again on resume
once the gated task itself re-runs.

## Risks / Trade-offs

- **A permanently-gated cell resumes into the same crash.** If the operator resumes while the row
  is still gated, the task crashes again and re-journals `retryable` -- an idempotent no-progress
  resume, not a loop within a run. Accepted: the alternative (a failure that needs `--fresh`) is
  strictly worse, and `agent-capacity-gate-liveness-reprobe` addresses gate staleness itself.
- **`isinstance` against a lazily-imported class.** Safe: `runtime.selection` is a plain module,
  imported once and cached, so the class object is identical to the one the raiser used.
