## 1. Staleness classification in run_record.py

- [x] 1.1 Add `_extract_path_candidate(entry: str) -> str` helper: return the
  leading whitespace-delimited token of a `files_changed` entry (strips
  trailing free-text like `(data-model, contracts, KG, 28 tasks)`).
- [x] 1.2 Add `_is_stale(record: Dict[str, Any], repo_dir: Path, base_branch:
  str) -> bool`: `worktree` field non-empty and its path does not exist on
  disk, AND `files_changed` is non-empty with every extracted path candidate
  resolving via `git -C <repo> cat-file -e <base_branch>:<path>` (subprocess,
  non-zero exit == does not resolve). Returns `False` if `worktree` is
  missing/empty (never infer staleness from `files_changed` alone).
- [x] 1.3 Rewrite `_active_conflicts()` to return `{"live": [...], "stale":
  [...]}`, applying `_is_stale()` to each non-terminal, specification-matching
  record. Need the repo path and base branch as new parameters — resolve
  `base_branch` from the record's own `base_branch` field per-record (each
  run record already carries its own `base_branch`; do not assume one shared
  value across records).
- [x] 1.4 Update `cmd_active_conflicts` to print the partitioned dict.
  Update `run_record.py`'s module docstring (the `active-conflicts` entry) to
  describe the new `{"live": [...], "stale": [...]}` shape and drop the
  reference to the nonexistent `contracts/active-conflicts-cli.md`.
- [x] 1.5 Update `cmd_claim`'s internal re-check (currently calls
  `_active_conflicts(...)` and treats any non-empty result as a conflict) to
  read the `live` partition only.

## 2. reconcile subcommand

- [x] 2.1 Add `cmd_reconcile(args)`: load the run record at `args.run`,
  re-run `_is_stale()` against it directly (not via a fresh scan — this
  record specifically) using its own `repository`/`base_branch` fields. If
  still stale, call the same finish path `cmd_finish` uses (or its shared
  helper) with `--status completed_and_merged` and `merge_result` set to
  `args.note` if given, else a default message identifying the staleness
  reconciler as the source (include the record's own `run_id`). If no longer
  stale, print a `{"status": "not_stale", ...}` result and make no write.
- [x] 2.2 Register the `reconcile` subparser: `RUN_PATH` positional,
  `--note` optional, following the existing subparser patterns in this file
  (see `active-conflicts`, `finish`).

## 3. Wire the pipeline guard

- [x] 3.1 In `skills/worktrail-go/references/subagent-prompts.md`
  `#active-conflicts-scan`: parse the new `{"live": [...], "stale": [...]}`
  shape. For each entry in `stale`, call `worktrail-run-record reconcile
  <path> --note "auto-reconciled: active-conflicts-staleness-reconciliation"`
  before the hard-stop check. Hard-stop only when `live` is non-empty
  (existing `BLOCKED:`/`finish --status blocked_external_dependency` text
  applies to `live` entries only now).
- [x] 3.2 Re-read `#active-conflicts-scan`'s two existing citation sites
  (`#sibling-worktree-check`, `implement` pipeline step 1) to confirm no
  other text there assumes the old flat-array shape.

## 4. Tests

- [x] 4.1 Unit tests for `_extract_path_candidate` covering a clean path, a
  path with trailing descriptive text, and an empty string.
- [x] 4.2 Unit tests for `_is_stale` covering: worktree exists (live); no
  worktree field (live); worktree gone + all files resolve (stale); worktree
  gone + one file does not resolve (live); worktree gone + empty
  `files_changed` (live).
- [x] 4.3 Unit tests for `_active_conflicts`'s partitioning against a
  temp run-record directory with a mix of live/stale/non-matching/terminal
  fixture records.
- [x] 4.4 Unit tests for `cmd_reconcile`: stale-at-call-time closes the
  record with the expected `final_status`/`merge_result`; no-longer-stale
  leaves the record unmodified.
- [x] 4.5 Update the existing `claim` conflict test(s) if any assert against
  the old flat-list shape from `_active_conflicts`.

## 5. Verification

- [x] 5.1 [e2e] `PYTHONPATH=src pytest -q`
- [x] 5.2 [e2e] `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`
- [x] 5.3 [e2e] `PYTHONPATH=src pytest -q tests/test_plugin_surface.py` to
  confirm the `#active-conflicts-scan` anchor's citations still resolve
  after the subagent-prompts.md edit.
