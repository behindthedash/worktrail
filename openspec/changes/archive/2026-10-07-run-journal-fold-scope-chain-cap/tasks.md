## 1. Fold scope boundary

- [x] 1.1 In `src/worktrail/workqueue/queue_triage.py`, make `_fold_task_file_scope()` decide
      each probe's admission by path identity instead of string spelling: strip the
      line-number suffix, normalize with `os.path.normpath`, keep the probe only when it
      resolves inside `worktree_dir` and exists there, compare `exclude` in the same
      normalized repo-relative form, and emit the canonical repo-relative spelling. A probe
      that resolves outside the worktree -- a triage evidence's `../run-<spec>.json` journal
      citation -- and a path the fold writes itself (the target change's
      `proposal.md`/`tasks.md`, however the evidence spells them) can no longer enter the
      derived `files:` scope; a fold whose citations all leave the worktree emits no `files:`
      line, leaving compile's own inference as before; the focus/evidence derivation and the
      `src/`-to-sibling-test glob are otherwise unchanged. Restate the rule in the docstring
      with its why: a citation is evidence that the journal was read, not a declaration that
      the task writes it; a declared file outside the worktree can never be the task's
      committed work; journal writes are serialized by the orchestrator's RunLock, not the
      plan; and the compile shape gate's same-file chain budget (default
      `compile_max_same_file_chain` = 2) otherwise caps how many folds citing one run journal
      can land before compile refuses with `(3 > 2)`.
      (Requirement: Fold and propose are applied as a pull request, fail-closed)
      Add `tests/workqueue/test_queue_triage.py` coverage, beside the existing fold-scope
      tests: an evidence citing `../run-<spec>.json` that exists beside the fixture worktree
      derives a scope minus the journal; a path cited with a `./` prefix or an interior `..`
      that resolves inside the worktree is admitted in canonical spelling; a respelled
      citation of the fold's own `proposal.md`/`tasks.md` is still excluded; a fold citing
      only out-of-worktree paths emits no `files:` line; and three such derived scopes fed
      through `conductor.parallelism.shape_problems()` produce no same-file chain problem,
      where the pre-fix derivation is refused with `(3 > 2)`.
      files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage.py

## 2. Operator reference

- [x] 2.1 In `.claude/skills/workqueue/skill.md`, extend the fold `files:`-scope bullet and
      the critical rule that keeps the change's own docs out of the folded task's scope with
      the worktree-boundary rule: the derived scope names only canonical repo-relative paths
      that resolve inside the worktree, so the run journal an evidence reads beside the
      worktree (`../run-<spec>.json`) is never declared (a citation is a read, not a write
      declaration; the RunLock already serializes journal writes), `exclude` matches by path
      identity so a respelled citation of the change's own docs is still excluded, and fold
      depth is no longer capped at two per journal by the same-file chain budget.
      (Requirement: Fold and propose are applied as a pull request, fail-closed)
      files: .claude/skills/workqueue/skill.md

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q
      tests/workqueue/test_queue_triage.py`, then `PYTHONPATH=src python3.14 -m pytest -q` and
      `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`, then
      `python3.14 scripts/ci/ruff_pinned.py check .`, `python3.14 scripts/ci/ruff_pinned.py
      format --check .` and `python3.14 scripts/ci/check_shebang_exec_bits.py`. Then exercise
      the boundary end to end against a scratch repo whose change has two tasks declaring the
      journal beside the worktree: confirm a third fold citing that journal derives a scope
      without it and `worktrail-compile` on the folded change succeeds, while the pre-fix
      derivation is refused with `same-file chain: ... (3 > 2)`. Run `openspec validate
      run-journal-fold-scope-chain-cap --strict` and `worktrail-compile
      openspec/changes/run-journal-fold-scope-chain-cap`.
      depends: 1.1, 2.1
