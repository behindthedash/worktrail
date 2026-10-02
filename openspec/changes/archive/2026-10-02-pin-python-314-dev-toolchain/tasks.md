## 1. Pin the local installer

- [x] 1.1 Change `scripts/dev-install.sh` so every install and post-install
      metadata-verification invocation uses `python3.14` (`python3.14 -m pip`
      for pip), without changing the canonical-checkout guard or its
      externally-managed-environment fallback. Update
      `scripts/ci/test_dev_install.sh`'s PATH stubs and assertions to prove
      the canonical path uses only the Python 3.14 module invocation and the
      linked-worktree path invokes neither tool.
      (Requirements: Local development installation uses Python 3.14 explicitly)
      files: scripts/dev-install.sh scripts/ci/test_dev_install.sh

## 2. Pin policy and developer instructions

- [x] 2.1 Make `.worktrail/policy.yaml` invoke Python 3.14 for its pre-PR and
      pre-commit gates, with pytest called as `python3.14 -m pytest`; update
      `AGENTS.md`'s Development commands to use the same interpreter. Extend
      the existing policy self-check or drift coverage to assert the committed
      policy command values and documented command block cannot silently
      revert to bare `pytest`, `pip`, or `python3`.
      (Requirements: Repository-managed local gates use Python 3.14 explicitly)
      files: .worktrail/policy.yaml AGENTS.md tests/router/test_policy_drift_selfcheck.py

## 3. Verification

- [x] 3.1 [e2e] Run `bash scripts/ci/test_dev_install.sh`, the focused policy
      drift tests, `PYTHONPATH=src python3.14 -m pytest -q`,
      `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`,
      `python3.14 scripts/ci/ruff_pinned.py check .`, and
      `python3.14 scripts/ci/ruff_pinned.py format --check .`. Then run
      `openspec validate pin-python-314-dev-toolchain --strict` and
      `worktrail-compile openspec/changes/pin-python-314-dev-toolchain`.
      depends: 1.1, 2.1
