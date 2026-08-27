## 1. Shared delta-section parsing helper

- [ ] 1.1 Extract the section-walking loop currently inline in `_openspec_delta_reconciled`
      (`src/worktrail/router/dashboard.py`) into a shared `_iter_openspec_delta_sections(text)`
      helper that yields `(kind, body)` per `## ADDED|MODIFIED|REMOVED|RENAMED Requirements`
      section, reusing the existing `_OPENSPEC_DELTA_SECTION` regex. Update
      `_openspec_delta_reconciled` to use it so the loop is not duplicated.
- [ ] 1.2 Add a small helper that extracts, from a `(kind, body)` pair, the set of requirement
      names it declares as "current" for drift-matching purposes: `_OPENSPEC_REQUIREMENT` names
      for `ADDED`/`MODIFIED` bodies, and `TO:` values (via `_OPENSPEC_RENAME` filtered to
      `TO`, run through `_rename_requirement_name`) for `RENAMED` bodies.

## 2. Git timestamp helpers

- [ ] 2.1 Add `_git_last_commit_time(repo, path) -> Optional[int]` wrapping
      `git log -1 --format=%ct -- <path>`, returning `None` on empty output or a non-zero git
      exit (uncommitted-only file).
- [ ] 2.2 Add `_git_added_commit_time(repo, path) -> Optional[int]` wrapping
      `git log --diff-filter=A -1 --format=%ct -- <path>`, falling back to the earliest entry of
      `git log --follow --format=%ct -- <path>` when the add-filtered query returns nothing.

## 3. Drift detection function

- [ ] 3.1 Add `_openspec_delta_drift(change_dir, repo) -> List[Dict[str, str]]` in
      `src/worktrail/router/dashboard.py`: for each `specs/<capability-path>/spec.md` delta file
      under `change_dir`, collect requirement names declared `MODIFIED` or `RENAMED ... TO:` (via
      the task 1 helpers).
- [ ] 3.2 For each such requirement name, glob
      `repo/openspec/changes/archive/*/specs/<capability-path>/spec.md` for archived changes at
      the same capability path, and collect the requirement names each declares `ADDED`,
      `MODIFIED`, or `RENAMED ... TO:`.
- [ ] 3.3 For each name present in both sets, compare `_git_last_commit_time` on the open change's
      delta file against `_git_added_commit_time` on the archived change's delta file; when the
      open-change timestamp is `None`, or the archived timestamp is not strictly greater, skip
      (no drift). Otherwise append `{"requirement": name, "capability": <relative-path-str>,
      "archived_change_id": <archived-dir-name>}` to the result.
- [ ] 3.4 Wrap the whole function body in `try/except Exception: return []` so a git/filesystem
      error degrades to "no drift" rather than propagating.

## 4. Wire into the dashboard scan

- [ ] 4.1 In `_safe_detect_openspec`, call `_openspec_delta_drift(change_dir, repo)` (repo already
      resolved as `change_dir.parent.parent.parent` in this function's sibling helpers) and set
      `info["delta_drift"] = <result>` only when the result is non-empty, mirroring the existing
      `stale_task_ids` convention. Do not change `stage` or `next_action` based on the result.

## 5. Tests

- [ ] 5.1 In `tests/router/test_dashboard.py`, using the repo's existing temp-git-repo fixture
      pattern (see the `git init` / incremental-commit tests already in that file): build a repo
      with an open change's delta declaring `MODIFIED Requirement: Foo`, commit it, then add and
      commit an archived change under `openspec/changes/archive/` declaring the same capability
      path and `MODIFIED Requirement: Foo` (or `ADDED`, or `RENAMED ... TO: Foo`) — assert
      `_safe_detect_openspec` (or `_openspec_delta_drift` directly) reports the drift with the
      correct `archived_change_id`.
- [ ] 5.2 Add a test where the archived change is committed *before* the open change's delta's
      last commit — assert no drift is reported.
- [ ] 5.3 Add a test where the open change's delta file is uncommitted (working-tree only, no
      prior commit) — assert no drift is reported even though a matching archived change exists.
- [ ] 5.4 Add a test where no archived change exists for the capability path at all — assert no
      drift is reported and the function does not raise.
- [ ] 5.5 Add a test asserting that a drift finding does not change the reported `stage` for a
      change that is otherwise `ready-to-implement`, `stale-bookkeeping`, or `complete` — the
      `delta_drift` field is additive only.
- [ ] 5.6 Run `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` and confirm both are
      green.
