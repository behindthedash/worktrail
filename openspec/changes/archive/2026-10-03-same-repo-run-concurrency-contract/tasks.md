## 1. Repo-wide active-conflicts scan

- [x] 1.1 In `src/worktrail/router/run_record.py`: `_active_conflicts()` gains an optional
      specification (`specification: str | None`); when it is None, every non-terminal record
      under the repo's run-record directory is classified, whatever its `specification`, and
      every entry in both partitions carries the record's own `specification` value (None when
      the record never got one) alongside the fields it already carries (`run_id`, `path`,
      `started_at`, `request_summary`, `agent`). Everything else is unchanged: the same
      live/stale rule via `_is_stale()`, the same malformed-record skip into `warnings`, the
      same `exclude` path semantics. `cmd_active_conflicts` accepts `--specification` as
      optional and passes None through, and its docstring says the scan is repo-wide in that
      case.
      In `tests/router/test_run_record.py`, following the existing `TestActiveConflicts` setup
      helpers, add cases asserting: a scan with `specification=None` returns non-terminal
      records for two different specifications plus one with no specification (that entry's
      `specification` is None) in the same `live` partition; a recorded stale record (worktree
      gone, `files_changed` resolving on its `base_branch`) lands in `stale` under the repo-wide
      scan; a malformed record is still skipped into `warnings` without aborting the repo-wide
      scan; the specification-filtered scan still omits records for other specifications; and
      the `active-conflicts` CLI invoked without `--specification` exits 0 and prints both
      partitions. (Requirement: Non-terminal run records are partitioned into live and stale)
      files: src/worktrail/router/run_record.py, tests/router/test_run_record.py

## 2. Launch-time same-repo detection and width back-pressure

- [x] 2.1 In `src/worktrail/orchestrator/live.py`: add a `_same_repo_live_runs(repo, spec_id)`
      helper returning the repo's live run-record entries for specifications other than
      `spec_id` -- resolve the records root the way the pipeline's other consumers do (the
      policy's `run_record_dir` when set, else `worktrail_home()/runs`, via `load_policy()`),
      then look in `<root>/<repo.name>` and call the repo-wide `_active_conflicts()`.
      In `_pipeline_scheduler`, right after `taskformats.load_spec()` yields `spec_id` and
      before the `max_workers = _resolve_max_workers(...)` call, call it; when the result is
      non-empty, print a warning line naming the repo and the count, plus one line per run
      naming its `run_id`, `specification`, and record `path` (a stable prefix like
      `WARN same-repo concurrency:` keeps it greppable). `_resolve_max_workers()` gains a
      keyword-only `same_repo_live: int = 0`: when it is positive, the width resolved by the
      existing explicit/policy/plan rules is capped at `max(1, width // 2)` before returning,
      and the width line it prints names the capped value, the count, and the same-repo
      concurrency reason; with the default 0, both the width and the printed line are exactly
      what they are today. The per-spec `RunLock` acquisition stays where it is: a second run
      for the same spec still aborts before this scan runs.
      In `tests/orchestrator/test_same_repo_run_concurrency.py`, add cases asserting: the
      detection helper returns a live record for another specification while omitting the
      launching run's own record (same `specification` as `spec_id`) and a stale record, and
      returns an empty list for a repo with no records directory; `_resolve_max_workers` caps 4
      to 2 and 3 to 1 with one other live run, halves only once when the count is larger (2 from
      a base of 4), never returns less than 1 (base 1), and reproduces today's exact widths with
      a count of 0; and one end-to-end launch reusing the lifecycle harness fixtures
      (`tests/orchestrator/lifecycle/test_lifecycle_harness.py`'s real-`_full_real_inner` runner,
      its repo builder and env, imported cross-module as this suite already reuses fixtures)
      with a foreign live run record seeded for the same repo under the test's isolated
      `WORKTRAIL_HOME`: stdout carries the warning naming that run id plus the halved width with
      its reason, and the run still completes and merges its group.
      (Requirements: Same-repo live runs are detected before fan-out; A detected same-repo run
      is reported loudly; Same-repo concurrency halves the effective fan-out width)
      depends: 1.1
      files: src/worktrail/orchestrator/live.py, tests/orchestrator/test_same_repo_run_concurrency.py

## 3. Doctrine alignment in the skill reference

- [x] 3.1 [docs] In `skills/worktrail-go/references/subagent-prompts.md`, the
      `#active-conflicts-scan` section states that the scan is available repo-wide
      (`--specification` omitted scans every non-terminal run for the repo, each entry naming
      its own specification), and that an orchestrator launch performs that scan itself before
      fan-out: a live run on the same repo under a different specification prints a warning
      naming the run and halves the launcher's effective fan-out width. The per-spec claim and
      hard-stop procedure in the section stays as-is, since same-spec exclusivity is unchanged.
      Keep the wording procedural (no design history), and name no `worktrail-*` command that is
      not a real console script.
      files: skills/worktrail-go/references/subagent-prompts.md

## 4. Verification

- [x] 4.1 [e2e] Run `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` (the golden
      record/replay regression) and confirm both pass, then run
      `openspec validate same-repo-run-concurrency-contract --strict` and
      `worktrail-compile openspec/changes/same-repo-run-concurrency-contract`, confirming the
      compile writes the `.compile-ok` marker. Run this after 1.1, 2.1 and 3.1; verification
      only, no file changes expected.
