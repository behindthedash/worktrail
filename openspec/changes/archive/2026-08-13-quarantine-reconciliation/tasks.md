## 1. Group→file recomputation from the RunPlan cache

- [x] 1.1 In `src/worktrail/router/quarantine_selfcheck.py`, add
      `_group_files(repo: Path, spec_id: str, group_name: str) -> Optional[List[str]]`:
      glob `<repo>-worktrees/runplans/<spec_id>-*.json` (if none found, return
      `None`), load the newest match's `tasks` list, call
      `worktrail.orchestrator.coordinator.plan_groups(tasks)`, find the group
      whose `name == group_name`, and return the sorted, deduplicated union of
      `files` across that group's task ids. Return `None` if no matching group
      name is found in the recomputed partition (RunPlan/journal drift).

## 2. Base-branch reconciliation signal

- [x] 2.1 Add `_files_on_base(repo: Path, files: List[str], base: str = "") -> bool`
      (falsy `base` means "not passed explicitly" — Python forbids a required
      param after a defaulted one, so `files` must come before the defaulted
      `base`): for each path in `files`, run `git -C repo ls-tree <base> --
      <path>` (or equivalent) and return `True` only if every path resolves.
      When `base` is falsy, resolve it first via `git -C repo rev-parse
      --abbrev-ref HEAD` (the repo's current checked-out branch).

## 3. Merged-PR reconciliation signal

- [x] 3.1 Add `_merged_pr_matching(repo: Path, files: List[str]) -> Optional[str]`:
      run `gh pr list --state merged --json url,files --limit 50` from `repo`
      (mirror `reconcile_pr_labels.py`'s `_open_prs()` subprocess pattern:
      `capture_output=True, text=True, timeout=30, cwd=str(repo)`, return
      `None` on any `OSError`/`TimeoutExpired`/non-zero exit/JSON-decode
      failure — never raise). For each candidate PR (newest first), check
      whether its `files[].path` set is a superset of `files`; return the
      first match's `url`, or `None` if none match or the `gh` call failed.

## 4. Reconciliation orchestration inside check_repo()

- [x] 4.1 Add `reconcile_finding(repo: Path, finding: Dict[str, Any]) ->
      Optional[Dict[str, Any]]`: call `_group_files()`; if `None`, return
      `None` (unreconciled, RunPlan missing/drifted). Otherwise try
      `_files_on_base()` first (method `"base-branch-files"`, evidence = the
      file list); on failure try `_merged_pr_matching()` (method
      `"merged-pr-files"`, evidence = the matching PR URL). Return a
      reconciliation record dict (`spec_id`, `group`, `method`, `evidence`) on
      either signal succeeding, else `None`.
- [x] 4.2 In `check_repo()`, after building the raw findings list exactly as
      today, run `reconcile_finding()` per finding. Split into two lists:
      findings with no reconciliation record stay in `result["findings"]`
      (unchanged shape); findings that got one move to a new
      `result["reconciled"]` list holding their reconciliation record. Preserve
      the existing `findings`-only return shape for any caller that doesn't
      read `reconciled` (i.e. adding the key must not change existing
      `findings` semantics for anything already unreconciled).
- [x] 4.3 Update `sweep()` to include a repo's result whenever `findings` is
      non-empty (unchanged from today — `reconciled`-only repos are not
      "flagged").

## 5. Tests

- [x] 5.1 Add `tests/router/test_quarantine_selfcheck.py` cases for
      `_group_files()`: no RunPlan cache → `None`; RunPlan cache present,
      group name matches → correct file union; group name not found in
      recomputed partition → `None`.
- [x] 5.2 Add cases for `_files_on_base()`: all files present on base → `True`;
      one file missing → `False`; explicit `base` argument used over current
      branch.
- [x] 5.3 Add cases for `_merged_pr_matching()` (mock `subprocess.run`): a
      merged PR's files superset the group's files → returns that PR's url;
      no PR matches → `None`; `gh` call raises/non-zero/bad JSON → `None`
      (never raises).
- [x] 5.4 Add cases for `reconcile_finding()`/`check_repo()`: a QUARANTINED
      group reconciled via base-branch-files is excluded from `findings` and
      appears in `reconciled` with the right method/evidence; reconciled via
      merged-pr-files likewise; a group with no RunPlan cache stays in
      `findings` unchanged (byte-identical to pre-change behavior); a group
      that fails both signals stays in `findings` unchanged.
- [x] 5.5 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`; both must be green.
