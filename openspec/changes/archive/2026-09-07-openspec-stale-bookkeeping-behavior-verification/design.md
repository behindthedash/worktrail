## Context

`_pending_openspec_stale` (`src/worktrail/router/dashboard.py:711-748`) is the OpenSpec arm of
stale-bookkeeping detection. It is deliberately cheap and cache-only: fingerprint the change,
`load_cached` a RunPlan, `apply_to_tasks`, then ask `_task_files_are_shipped` whether every
declared file of each pending non-tail task is git-tracked, on disk, and has a commit at or after
the change directory's creation baseline (`_dir_creation_timestamp`).

That baseline test was itself a hardening pass — it replaced bare "the file exists and is
tracked". But it is still a *file*-level proxy: on a file that many changes touch
(`src/worktrail/orchestrator/verify.py` in the reported incident), "committed since the change
directory appeared" is satisfied by unrelated traffic. The detector's conclusion ("the change's
work shipped") does not follow from its evidence ("the file moved").

Devkit's `_pending_impl_stale` shares `_task_files_are_shipped` and has the same theoretical gap,
but it reads an author-declared `files:` frontmatter scope, so its file lists are tighter and
were not implicated by the brief. This change scopes itself to the OpenSpec arm.

## Goals / Non-Goals

**Goals:**
- Make a false "already merged" verdict require more than file churn: check that something the
  task actually claims is present in the code.
- Stay strictly deterministic and offline — no model call, no `gh`, no network. The capability's
  existing "cached RunPlan only, never a model call" requirement is not relaxed.
- Fail conservative: uncertainty means "not stale", which routes to the orchestrator (which
  re-checks anyway), never "close this, it's done".

**Non-Goals:**
- Semantic verification. Grepping for an identifier does not prove the behavior is correct; it
  only refutes the specific false positive where the claimed symbol is nowhere in the file.
- Changing the devkit `_pending_impl_stale` / `_pending_tail_stale` paths.
- Reclassifying the tail-kind OpenSpec path (tail tasks are already excluded from
  `_pending_openspec_stale`'s candidate set).

## Decisions

### Decision 1: Identifier evidence is extracted from the task's own text, deterministically

Each OpenSpec task's text is the checklist line(s) already loaded by `taskformats.openspec`.
Extraction takes backtick-quoted spans and keeps tokens matching `[A-Za-z_][A-Za-z0-9_]{2,}`
(after stripping a trailing `()`), dropping any token that contains `/` or `.` and any token that
merely names one of the task's own declared files. This yields things like `superseded_names`,
`CANCELLED`, `build_worker_prompt` — the vocabulary authors already use in worktrail's tasks.md
files — and yields nothing for a purely prose task.

*Alternative rejected:* parsing the change's delta specs for symbols. Delta specs are written in
requirement/scenario prose keyed to capabilities, not files, and cannot be attributed to a
particular task, which is the granularity the stale check operates at.

### Decision 2: All extracted identifiers must be present, in the task's own declared files

Presence is a plain substring search over the current on-disk text of the task's declared files
(the same merged file scope the file-level check already used). Requiring *all* rather than *any*
is the conservative direction: a partially-shipped task stays orchestrator-eligible. Files that
cannot be read as text are treated as containing nothing, which again yields "not stale".

The incident is refuted directly: `superseded_names`/`CANCELLED` were absent from `verify.py`
before #1075, so the task would not have been stale despite `verify.py` being tracked and freshly
committed.

### Decision 3: No extractable identifiers keeps file-only classification, but is reported as such

Dropping stale detection entirely for prose-only tasks would regress the capability into
re-dispatching genuinely merged work. Instead those tasks keep today's file-level verdict, and
the *report* carries the evidence level: `stale_evidence: "behavior"` when every stale task had
identifiers and they were all found, `"files-only"` otherwise. In the `files-only` case the
`next_action` says the evidence is file-level only and asks the operator to confirm the behavior
shipped, rather than asserting "files already merged on base".

The `stale-bookkeeping` stage string itself is unchanged — the router's premise checks,
`spec_sync_sweep`'s stale-bookkeeping brief path, and the drain stage table all key on that
string, and splitting it into a second stage would ripple through every consumer for no gain.
The `stale_evidence` field is additive.

## Risks / Trade-offs

- **False negatives:** an identifier renamed between authoring and implementation makes a
  genuinely-shipped task look pending, routing to the orchestrator. The orchestrator re-derives
  state from the tasks and the code, so the cost is a wasted routing decision, not wrong work —
  strictly cheaper than the false positive being fixed.
- **Substring matching is coarse** (a comment mentioning the identifier passes). Accepted: the
  gate exists to catch the "symbol is nowhere at all" case, and a tighter parser would be
  language-specific.
