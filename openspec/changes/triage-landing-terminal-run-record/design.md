## Context

`land_pr` is the single landing pipeline (spec `pr-landing-pipeline`). It either finishes the
caller's run record or starts one itself when the caller has none. `run_record.py finish` is
the code-enforced backstop for the scope-completeness gate: it refuses any
implementation-completion state on a record with no scope review. Those two contracts collide
exactly when the pipeline started the record: no context other than the pipeline ever touches
that record, so nobody can satisfy the gate, and the landing is classified as a ceiling with an
empty reason. Queue-triage's propose/fold apply paths are the only production callers that pass
`run=None`, so this is where it bites, but the defect is the pipeline's.

## Decisions

### D1. The pipeline records the scope review only for a record it started

When `_ensure_run_record` starts the record, `land_pr` appends one entry via the existing
`run_record.py scope-review PATH --item <request_summary> --status complete --evidence "..."`
subcommand right before `_finish_or_checkpoint`, on every branch that finishes with an
implementation-completion state (`completed_and_merged`, `blocked_product_decision`,
`completed_pr_open`). The evidence string names the pushed commit SHA, the branch, and the PR
URL -- what the pipeline actually verified, not a paraphrase of the request.

Rejected: recording a scope review whenever `scope_review` is empty, regardless of who owns the
record. A caller-supplied record with no review is a caller that skipped its own gate; the
whole point of `_enforce_scope_completeness_gate` is that `finish` refuses that. The pipeline
only vouches for work it did itself.

Rejected: having `queue_triage` record the entry. The record is created inside `land_pr` and
finished inside `land_pr`; there is no caller hook between the two, and adding one would give
the triage path a second copy of a rule the pipeline should own.

Implementation: `_ensure_run_record` already distinguishes the two cases (it returns the caller's
path unchanged or the path it started). `land_pr` keeps a `pipeline_owns_record: bool` from
that call and threads it into `_finish_or_checkpoint`, which performs the `scope-review` append
before `finish` when the flag is set and the mode is not checkpoint. If the append itself fails
(nonzero exit), `_finish_or_checkpoint` returns `False` the same way a failed `finish` does.

### D2. Ceiling outcomes carry the gate's own message

`_run_record_main` gains stderr capture and, on `SystemExit` with a string code, keeps that
string. `_finish_or_checkpoint` returns `(ok, detail)` and each of the three
`"... but run record could not be completed"` outcomes sets `LandOutcome.detail` to that text.
The `merge_result` strings are unchanged so existing consumers keep matching on them.

### D3. `_run_gh_cmd` drops `cwd` when the repository path is gone

`pr_labels._run_gh_cmd` passes `cwd=repo if Path(repo).is_dir() else None`. Every `gh` call in
this module either takes a full PR URL or an explicit `repos/<owner>/<repo>` REST path, so the
working directory only matters for `gh`'s implicit repo resolution, which none of them rely on.
`_owner_repo_number`'s bare-number branch still calls `owner_repo_from_git(Path(repo))`, which
fails and is reported by the existing "could not resolve owner/repo/number" warning; that path
is unchanged.

Rejected: rewriting the record's `repository` to the canonical checkout after the worktree is
removed. Other run-record consumers (`_is_stale`, close-stale sweeps, the dashboard) read that
field as the git repo the run was executed in; changing its meaning is a separate change.

## Testing

- `tests/router/test_land_pr.py` (fake `RunRecordSpy`): a `run=None` landing issues a
  `scope-review` call before `finish` on each of the merged / blocked / open branches and not in
  checkpoint mode; a caller-supplied run issues none; a failing `finish` yields a ceiling
  outcome whose `detail` contains the spy's message.
- `tests/router/test_land_pr_integration.py` (real `run_record` module against a temp record):
  a `run=None` landing ends with the record at `final_status=completed_pr_open` and a single
  `complete | <request_summary> | ...` scope-review entry -- the exact failure the live run hit.
- `tests/router/test_pr_labels.py`: `ensure_pr_risk_label` with a `repo` path that does not
  exist still adds the label, and the fake runner observes `cwd=None`; an existing directory
  still receives `cwd=<repo>`.
- `tests/workqueue/test_queue_triage.py`: `_worktree_pr_close`'s `LandRequest` has `run=None`
  and a non-empty `request_summary`, and `landing.final_status` echoes the pipeline's outcome.
