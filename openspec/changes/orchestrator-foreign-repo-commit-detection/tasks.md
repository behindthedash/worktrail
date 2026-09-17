## 1. Foreign-repo detection at the empty-diff guard

- [ ] 1.1 In `src/worktrail/orchestrator/integrate.py`, add
      `QUARANTINE_FOREIGN_REPO_TARGET = "foreign_repo_target"` beside `QUARANTINE_EMPTY_DIFF`
      and a helper that, given `repo` and the group's deliverable tasks, returns the foreign
      repos behind their declared `files` (absolute, `~`, or `..` paths resolving outside
      `repo`; git top-level via `git rev-parse --show-toplevel`, else the path) plus, per
      git repo, a read-only unmanaged-commit finding (checked-out branch equals the default
      branch from `origin/HEAD`, else `main`/`master`, and `rev-list --count @{u}..HEAD` > 0);
      any git failure yields no finding. In `integrate_one()`'s empty-diff branch, when the
      helper returns foreign repos, quarantine with the new reason and a message naming them
      and any unmanaged default-branch commits; otherwise keep `empty_diff` unchanged.
      (Requirements: Empty-diff quarantine distinguishes foreign-repo targets; Unmanaged
      default-branch commits in a foreign repo are reported.)
      In `tests/orchestrator/test_integrate.py`, add tests for all six spec scenarios using
      real temp git repos for the sibling.
      files: src/worktrail/orchestrator/integrate.py, tests/orchestrator/test_integrate.py

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/orchestrator`, then
      `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate orchestrator-foreign-repo-commit-detection --strict` and
      `worktrail-compile openspec/changes/orchestrator-foreign-repo-commit-detection`.
