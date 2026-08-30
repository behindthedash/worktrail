# Investigation: full-real tears down $WT/branch before tail tasks complete, and silently wipes the run journal on a corrupt resume

Route I investigation, brief `20260829-025103-worktrail-full-real-orchestrator-tears`.
Confirmed two distinct root causes; **stopping at `investigation_complete`** rather than
auto-continuing into Route F, because both fixes fall under this repo's own Route J scope
(`routes.md` §J: "Changes to GO, skills, plugins, agent prompts, **orchestration**, cassettes
— this is production code", `routing_cassette_required` gate) — Route I only auto-continues
into Route F, and neither fix is F-shaped. Recommending Route J for both, as two separate PRs
(different files, different mechanisms, independently landable).

## Verified Observations

### Bug 1 — `$WT` + `chg/<change-id>` branch removed while tail-kind tasks are still pending

- The brief's incident path (`/home/briank/projects/worktrail-worktrees/tail-task-auto-reconciliation-chg-verify-tail-prs`)
  matches exactly the change-spec worktree naming convention in
  `skills/worktrail-go/references/subagent-prompts.md#change-spec-worktree-setup`:
  `WT="$REPO-worktrees/$SPEC_ID-chg-$CHANGE_SLUG"`, branch `chg/$CHANGE_ID` — i.e. this is
  the Route F/G `modify` pipeline's change-spec worktree, not a per-task worktree.
- `skills/worktrail-sdd-workflow/references/pipeline-details.md#modify-pipeline` step 7 reads:
  "teardown per `../../worktrail-go/references/subagent-prompts.md#worktree-lifecycle`
  (change-spec worktree only after sync completes)" — the `modify` pipeline reuses the **same**
  teardown section as the `new` pipeline; there is no separate "teardown after modify pipeline"
  section.
- `subagent-prompts.md`'s `### Teardown after \`new\` pipeline completes` section's only
  documented gate before removing `$WT` and the branch is:
  > **All group PRs auto-merged green:** ... `git -C "$REPO" worktree remove "$WT"` /
  > `git -C "$REPO" branch -D "spec/$SPEC_ID"`
  followed by `#worktree-deletion-liveness-guard` (checks for a live *owning run*, not for
  outstanding *tasks*). Nothing in this gate condition reads `pending_tail_tasks`,
  `pending_tail_reason`, or the dashboard's `tail-pending` stage.
- The same file's own "Tail tasks (E2E / cleanup) are not auto-run" note (earlier in the same
  document) documents that `full-real` intentionally sets `integrate_complete: true` while
  `kind: e2e`/`kind: cleanup` tasks remain unrun, recording them as `pending_tail_tasks` — i.e.
  "all group PRs merged" and "the change is actually done" are explicitly different states by
  design, but the teardown gate only checks the former.
- `integrate.py`'s own module comment (`_pending_tail_task_ids`, line ~588) independently
  documents "Why `integrate_complete` can be `True` while tail-kind tasks remain unrun" —
  further confirming this is a known, named design distinction that the teardown gate simply
  doesn't consult.
- `verify.py`'s `cleanup_group()` (the actual Python code that deletes worktrees/branches) only
  ever tears down **per-task and per-group** worktrees/branches for `delivered` tasks of one
  group — it never touches the top-level `$WT`/`spec/`/`chg/` worktree or branch. The `$WT`
  removal is agent-driven (a shell command an SDD-workflow session runs by following
  `subagent-prompts.md` prose), not a Python-code teardown path. The brief's framing ("which
  teardown/sync code path in live.py or worktree.py") undershoots this — `worktree.py`'s
  `WorktreeManager.remove()`/`delete_branch()` are correctly task-scoped; the gap is in the
  **procedure document** an agent follows for the outer worktree, not in either module.
- Confirmed live: PRs #806 (base) and #807 (feature-1) — cited in the brief — correspond
  exactly to `git log` on `main` (`4d76fc6`, `c16d3c7`), i.e. group PRs did merge, satisfying
  the documented (incomplete) gate condition. The change
  `openspec/changes/archive/2026-08-29-tail-task-auto-reconciliation-verify-tail-prs` is
  archived on `main` today, consistent with the brief's account of manual recovery via PR #808.

### Bug 2 — journal silently reset to empty entries on a corrupt/unreadable resume

- `live.py:read_or_create_run_id()` is called **unconditionally, before** the pipeline
  scheduler's own resume-read, at `live.py:5658`
  (`run_id = read_or_create_run_id(Path(journal_path))`, immediately followed by
  `return _pipeline_scheduler(...)`).
- Its logic: if the journal file exists but `json.loads()` raises `(OSError,
  json.JSONDecodeError)`, execution falls through past the `if p.exists():` block to the
  "No journal or unreadable: write a fresh one" branch, which calls
  `progress.atomic_write_text(p, json.dumps({"run_id": run_id, "entries": []}, ...))` —
  **an unconditional, silent, ON-DISK overwrite** of the existing (merely unparseable) file
  with an empty-entries journal. There is no distinction between "file absent" and "file
  present but corrupt" — both hit the same destructive branch.
- This runs *before* `_pipeline_scheduler`'s own resume-block (`live.py:4845-4852`), which has
  its own non-destructive `except (OSError, json.JSONDecodeError)` fallback that only prints
  `"PIPELINE RESUME: journal unreadable (...); starting fresh"` — by the time that block runs,
  `read_or_create_run_id` has *already* rewritten the file on disk to `{"run_id": ...,
  "entries": []}`, so that second read actually succeeds (on the now-empty file) rather than
  raising. The observable symptom — a resumed invocation producing
  `{"entries": [], <new run_id>}` with no groups — is explained entirely by
  `read_or_create_run_id`'s destructive fallback, not by the later block.
- Directly reproduced against the live artifact from this exact incident: the on-disk journal
  at `/home/briank/projects/worktrail-worktrees/tail-task-auto-reconciliation-chg-verify-tail-prs-worktrees/run-tail-task-auto-reconciliation-verify-tail-prs.json`
  (856 lines, real content — group PR URLs, per-task entries) is currently **malformed JSON**:
  a trailing comma after the last `entries` array element before its closing `]`
  (`"task": "3.4"\n    },\n  ],`). `json.loads()` on this file raises exactly
  `json.JSONDecodeError: Expecting value: line 839 column 3` — the same exception class
  `read_or_create_run_id` catches and treats as "unreadable, discard and start fresh."
  (Note: the directory name itself, `...-chg-verify-tail-prs-worktrees`, is one level deeper
  than `journal_path_for()`'s documented `repo.parent / f"{repo.name}-worktrees"` scheme would
  produce for `repo=$REPO` — consistent with `repo` having been `$WT` at the call site that
  created this particular journal, a second, separate naming inconsistency worth confirming
  against the actual call site before any fix, not asserted as root cause here.)
- The already-established pattern for "journal integrity problem" elsewhere in the same
  function (`live.py:4852-4858`, the `foreign_ids` check) is to fail loud with an actionable
  `RuntimeError` ("Re-run with --fresh to discard this journal and start clean") rather than
  silently discarding — i.e. there is already local precedent for the correct failure mode,
  `read_or_create_run_id` just doesn't follow it.

## Unknowns / Missing Evidence

- **How the journal actually became malformed** in the first place is not confirmed. Two
  candidate mechanisms, both plausible, neither verified against the specific write that
  produced this file:
  - A pre-existing, already-documented gap: the docstring immediately above the resume block
    (`live.py` "TASK-006 note") states journal writes inside `integrate_one` (via
    `_write_group_journal`) "should be made atomic under concurrent interleaved writes" —
    i.e. non-atomic concurrent writes are a known, named risk for exactly this kind of
    corruption.
  - The brief itself states the file was "manually reconstructed from a prior Read of the
    file" during incident recovery — a hand reconstruction is an independently plausible way
    to introduce a stray trailing comma, unrelated to any orchestrator code path.
  - Not distinguished here; either way, `read_or_create_run_id`'s destructive fallback is a
    real defect regardless of which mechanism produced the corruption.
- Whether `journal_path_for()` is ever actually called with `repo=$WT` (producing the doubly-
  nested `...-chg-<slug>-worktrees` directory observed) versus always `repo=$REPO` — not
  traced to a specific call site in this pass; flagged above as a secondary naming question,
  not asserted as contributing to either confirmed root cause.

## Hypotheses

- H1 (unconfirmed): concurrent/non-atomic writes during `integrate_one` are what corrupted this
  specific journal (see TASK-006 note); alternative H2 (unconfirmed): the incident's own manual
  file reconstruction introduced the trailing comma. Does not change the confirmed fix for Bug 2
  (loud-fail instead of silent-discard), since that fix is correct regardless of which produced
  the corruption.

## Confirmed Root Cause

**Bug 1:** `subagent-prompts.md`'s single, shared `$WT` teardown gate ("All group PRs
auto-merged green") is consulted by both the `new` and `modify` pipelines and never checks for
outstanding tail-kind tasks (`pending_tail_tasks`/dashboard `tail-pending`) before an agent runs
`git worktree remove "$WT"` + `git branch -D`. Base+feature-1 group PRs merging is suf­ficient,
under the current documented procedure, for an agent to conclude teardown is safe — even though
`full-real` and `integrate.py` both explicitly design for `integrate_complete: true` co-existing
with unrun tail tasks. This is a procedure-document gap, not a Python code defect.

**Bug 2:** `read_or_create_run_id()` treats "journal file exists but fails to parse" identically
to "journal file absent," silently overwriting the existing file on disk with a fresh
`{"run_id": ..., "entries": []}` before the pipeline scheduler's own (non-destructive) resume
logic ever runs — permanently discarding all prior run history (including MERGED group PR URLs)
on any resume against a corrupted journal, with no error surfaced to the operator. Directly
reproduced: the actual journal from this incident currently fails to parse with exactly the
exception class this function silently swallows.

## Recommended Next Route

**Route J** (workflow evolution) for both — not Route F. Per this repo's own `routes.md` §J,
changes to `skills/` (Bug 1: `subagent-prompts.md`) and to `orchestration` code under
`src/worktrail/orchestrator/` (Bug 2: `live.py`) are explicitly J-scoped, `routing_cassette_required`.
Route I only auto-continues into Route F on a confirmed+small fix; neither fix here is F-shaped,
so this run stops here rather than silently taking a shortcut around J's gate.

Suggested fix shape for each (not applied in this run):

- **Bug 1** (`subagent-prompts.md`, `### Teardown after \`new\` pipeline completes`): add an
  explicit precondition alongside "All group PRs auto-merged green" — no `pending_tail_tasks`
  remain in the run journal (or the dashboard no longer reports the spec as `tail-pending`) —
  before running the `git worktree remove "$WT"` / branch-delete commands. Apply to both the
  `new`-pipeline wording and the `modify`-pipeline's shared reuse of the same section (fix the
  hardcoded `spec/$SPEC_ID` branch-name example to also show the `chg/$CHANGE_ID` case explicitly,
  since the modify pipeline reuses this section verbatim today).
- **Bug 2** (`live.py:read_or_create_run_id`): distinguish "no file" (current fresh-journal
  behavior, unchanged) from "file exists but `json.loads` failed" (fail loud with a
  `RuntimeError` naming the path and recommending `--fresh` to discard deliberately, mirroring
  the existing `foreign_ids` precedent a few lines below in the same resume path) instead of a
  silent destructive overwrite.
