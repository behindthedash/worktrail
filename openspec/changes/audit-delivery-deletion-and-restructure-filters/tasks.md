## 1. Deletion-aware classification (`audit_delivery`)

- [ ] 1.1 In `src/worktrail/router/audit_delivery.py`, add `touched_files_with_status(repo, sha)
      -> dict[str, str]` backed by `git diff-tree --name-status -r --root`, and extend the
      per-file check so a path with status `D` counts as delivered iff `_blob_at(base_ref, path)`
      is `None`. Add `content_delivered_via_deletion(repo, base_ref, sha, files) -> bool`
      (True only when every file is a deletion absent on base). In `audit_repo`, add the
      `content_delivered_via_deletion` bucket and check it after `never_shipped_by_policy`
      and before `content_delivered_via_rewrite`. (Requirement: Deliberate deletion is
      classified as delivered.)
      In `tests/router/test_audit_delivery.py`, add real-git tests for the three deletion
      scenarios (pure deletion absent on base, mixed add+delete via squash, deletion that
      did not land).
      files: src/worktrail/router/audit_delivery.py, tests/router/test_audit_delivery.py

## 2. Restructured-path filter and reporting (`audit_delivery`)

- [ ] 2.1 In `src/worktrail/router/audit_delivery.py`, add
      `superseded_by_restructure(repo, base_ref, sha, files, globs) -> bool` (every file is
      glob-matched via `fnmatch` and absent on base, or individually content-verified; False
      when `globs` is empty). Thread a `restructured_paths: list[str]` keyword through
      `audit_repo` (default `()`), evaluate it after `identifiers_survive_elsewhere` and
      before `confirmed_dropped`, and add the `superseded_by_restructure` bucket. Add the
      repeatable `--restructured-path` argparse option in `main`, include both new buckets
      in the per-repo summary line and the final totals line, and leave the exit code driven
      by `confirmed_dropped` alone. (Requirements: Restructured paths are excused only when
      opted in; New buckets are reported but never fail the run.)
      In `tests/router/test_audit_delivery.py`, add tests for the three restructure
      scenarios and the exit-zero/summary scenario via `main`.
      files: src/worktrail/router/audit_delivery.py, tests/router/test_audit_delivery.py
      depends: 1.1

## 3. Documentation

- [ ] 3.1 In `docs/specs/research/fleet-wide-retroactive-delivery-audit.md`,
      update the "two false-positive categories" section to state that both are now
      automated (`content_delivered_via_deletion`, `superseded_by_restructure`) and record
      the `--restructured-path` globs that reproduce the datalena and worktrail clusters.
      files: docs/specs/research/fleet-wide-retroactive-delivery-audit.md

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src pytest -q tests/router/test_audit_delivery.py`,
      then `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate audit-delivery-deletion-and-restructure-filters --strict` and
      `worktrail-compile openspec/changes/audit-delivery-deletion-and-restructure-filters`.
