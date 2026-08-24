## 1. Task-Level Candidate Enumeration

- [x] 1.1 Investigate real `tasks.md` checkbox syntax across this repo's own active/archived
  OpenSpec changes (task id numbering shape, `[e2e]`/`[cleanup]` kind tags, `(Requirement: ...)`
  annotations, nested sub-bullets) to pin the parsing rule, then implement a per-task candidate
  function in `overlap_check.py` that, given an explicit target OpenSpec change id, returns one
  `{task_id, task_text, checked}` entry per unchecked task in that change's `tasks.md`. A
  devkit-shaped target or no target returns exactly today's whole-spec/whole-change candidates,
  with no per-task scan. (Requirement: Task-Level Candidate Enumeration Scoped To A Known Target)
  (Requirement: Task Candidate Shape) (Requirement: OpenSpec Per-Task Candidate Enumeration)
  files: src/worktrail/router/overlap_check.py
- [x] 1.2 Add unit tests: target change with unchecked tasks, target change fully checked (empty
  per-task list, no error), no target supplied (unchanged behavior), devkit-shaped target
  (per-task list not populated).
  files: tests/router/test_overlap_check.py

## 2. Dispatch-Time Guard Extension (Phase 5.5)

- [ ] 2.1 Extend `check_spec_collision.py` so a caller can pass an explicit target change id and
  receive task-level match candidates (via the 1.1 function) alongside the existing whole-spec
  `Implemented` verification path, returned in a shape that lets the caller distinguish a
  task-level match (open, unchecked) from a whole-spec match (Implemented, shipped) so a task
  match is never treated as grounds for the existing auto-close-on-Implemented behavior.
  (Requirement: Dispatch-Time Guard Distinguishes Task-Level Matches From Whole-Spec Matches)
  files: src/worktrail/router/check_spec_collision.py
- [ ] 2.2 Add unit tests: task-level match surfaced distinctly from whole-spec match; Route C/D
  does not auto-close on a task-level match; no task-level match leaves dispatch unmodified.
  files: tests/router/test_check_spec_collision.py
- [ ] 2.3 Update `spec-collision-check.md` (task-level match handling, redirect-not-auto-close
  wording) and `subagent-prompts.md`'s `#overlap-check` section (task-level candidates now
  possible when a target spec is known) to document the extended contract.
  files: skills/worktrail-go/references/spec-collision-check.md, skills/worktrail-go/references/subagent-prompts.md

## 3. Dashboard Advisory Extension

- [ ] 3.1 Extend `cluster_detect.py` so a queued brief carrying `target-spec:` can also match an
  open, unchecked task in that target change (via the 1.1 function), surfaced through the same
  cluster-reporting shape `duplicate-brief-detection` already uses for brief-vs-brief matches,
  and skipping the advisory when the brief already carries a matching `target-task:`.
  (Requirement: Dashboard Advisory Surfaces Brief-vs-Task Matches)
  files: src/worktrail/router/cluster_detect.py
- [ ] 3.2 Wire the extended cluster output into `dashboard.py`'s existing render path where
  `cluster_detect.compute_clusters` results are already merged into the rendered/JSON dashboard
  output.
  files: src/worktrail/router/dashboard.py
- [ ] 3.3 Add unit tests: two undispatched briefs sharing `target-spec:` and matching the same
  open task surface an advisory; a brief that already carries `target-task:` naming the matched
  task produces no new advisory.
  files: tests/router/test_cluster_detect.py

## 4. `target-task` Frontmatter Field

- [x] 4.1 Add `--target-task` to `create_handoff.py`, validated the same way `_validate_blocked_by`
  validates `--blocked-by` (non-empty, no characters that would make it ambiguous across
  changes), persisted alongside the existing `target-spec` frontmatter field, with no effect on
  briefs that omit it. (Requirement: `target-task` Frontmatter Field)
  files: src/worktrail/workqueue/create_handoff.py
- [x] 4.2 Add unit tests: brief created with both fields persists both; brief created with only
  `target-spec` is unchanged from before this change; empty/whitespace `target-task` is rejected
  before a brief is written.
  files: tests/workqueue/test_create_handoff.py
- [x] 4.3 Document `target-task:` in the handoff frontmatter reference, paired with the existing
  `target-spec:` documentation.
  files: skills/worktrail-handoff/references/handoff-template.md

## 5. Closure-Time Checkbox-Sync Check

- [ ] 5.1 Extend `work_queue.py`'s `done()` so that, when the closing brief carries both
  `target-spec:` and `target-task:` and closes with `--implementation-complete`, it looks up the
  referenced task's current checkbox state via the same cached-`RunPlan` lookup pattern
  `openspec-stale-bookkeeping-detection` already uses (`conductor.runplan.load_cached`/
  `fingerprint`) — no fresh model call, no second `tasks.md` parser. On an unticked checkbox,
  the result includes `checkbox_out_of_sync: true` and the closure note records the mismatch; the
  target spec's `tasks.md` is never written. A cache miss, unreadable `tasks.md`, or missing
  target spec directory degrades to no signal and closure proceeds unmodified.
  (Requirement: Closure-Time Checkbox-Sync Check)
  (Requirement: Checkbox-Sync Warns And Never Modifies The Target Spec)
  (Requirement: Checkbox-Sync Lookup Is Best-Effort And Never A Fresh Model Call)
  files: src/worktrail/workqueue/work_queue.py
- [ ] 5.2 Add unit tests: closing with both fields and an unticked task surfaces the warning and
  note without writing `tasks.md`; closing with both fields and an already-ticked task surfaces
  no warning; closing with no `target-task:` is unchanged; a cache miss/missing target spec
  degrades to no signal rather than blocking closure.
  files: tests/workqueue/test_work_queue.py

## 6. End-to-End Verification

- [ ] 6.1 [e2e] Build a throwaway fixture reproducing the discovery incident's shape (one active
  OpenSpec change with open tasks; two queued briefs both carrying that change's `target-spec:`
  and independently matching the same open task) and confirm both the dashboard advisory (3.1/3.2)
  and the Phase 5.5 guard (2.1) surface the task-level match, then run the full suite
  (`PYTHONPATH=src pytest -q`) and the orchestrator golden-record check
  (`PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`) to confirm no regression.
