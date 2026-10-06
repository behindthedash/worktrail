## 1. Compile: a declaration outranks the model's answer

- [ ] 1.1 In `src/worktrail/conductor/compile.py`, make `_validate()`'s payload row loop carry
      a declaring task's parsed `files:` list into the compiled plan verbatim and leave the
      model's `files` value for that row unconsulted: not empty-displacing, not merged, and
      not path-checked, because a value that is discarded must not be able to reject a
      payload whose used parts are sound. Tasks that declare nothing keep today's inference
      and its absolute/parent-directory rejection exactly. Restate the rule in the docstring
      beside the plan construction's `kind` carry-through, where the artifact already
      outranks the model so a seed and a compile agree. (Requirement: Declared scope
      satisfies compilation without a model call)
      Add `tests/conductor/test_compile.py` coverage: a partially declared change whose model
      answer returns `[]` for the declaring task still compiles with the declaration intact,
      `needs_compile()` on the merged tasks reports no gap, and the CLI path exits 0; a
      differing model list does not displace the declaration; an accepted out-of-repo
      declaration (a `../run-x.json` journal-style path) reaches the compiled plan verbatim
      on the compiled path as it already does on the seeded and baseline paths; the
      repo-escape rejection keeps rejecting what the model supplies for undeclared tasks; and
      `test_partial_declared_scope_is_kept_and_gaps_fall_back_when_spawning_fails` passes
      unchanged.
      files: src/worktrail/conductor/compile.py, tests/conductor/test_compile.py

## 2. Fold: an out-of-worktree brief declares its kind

- [ ] 2.1 In `src/worktrail/workqueue/queue_triage.py`, add the kind rule for the appended
      task as its own pure helper beside `_fold_task_file_scope()` — the shape
      `_fold_task_kind(worktree_dir, derived_scope, *texts)` returning `e2e` only when the
      derived scope is empty and at least one cited path resolves to an existing file outside
      the worktree while none resolves inside — and render the checklist item with the leading
      `[e2e]` tag and no `files:` line when it returns a kind, so a quarantine/selfcheck
      fold's task stops depending on the model's per-compile inference for a scope it cannot
      have. The helper takes the derived scope as input rather than re-deriving the admission
      boundary: taking the scope as empty while outside citations resolve is exactly the state
      the fold's no-out-of-worktree-declaration boundary produces (in flight as change
      `run-journal-fold-scope-chain-cap`), where this rule is the answer to that change's own
      "emits no `files:` line" consequence. Keep the focus/evidence derivation, the
      `src/`-to-sibling-test glob, `exclude`, and the tag-free paths (a non-empty derived scope
      keeps its declaration and no tag; nothing cited existing keeps today's undeclared
      behavior) unchanged. (Requirement: A folded task whose evidence leaves the worktree
      declares its tail kind)
      Add `tests/workqueue/test_queue_triage.py` coverage, driven through the helper and the
      fold rendering: an empty derived scope with a journal citation that exists beside the
      fixture worktree yields `e2e`, and the appended item renders `- [ ] N.1 [e2e]
      <instruction>` with no `files:` line; a non-empty derived scope yields no kind; a brief
      citing only non-existent paths yields no kind and stays undeclared.
      files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage.py

## 3. Operator reference

- [ ] 3.1 In `.claude/skills/workqueue/skill.md`, extend the fold files-scope bullet with the
      kind boundary: a brief whose evidence is all machine-local state beside the worktree
      gives the folded task the `[e2e]` kind, not a scope, because its work is not in the
      shared tree the change's workers commit into and compile exempts tail kinds by kind
      alone, so the fold's compile no longer depends on model inference for it.
      (Requirement: A folded task whose evidence leaves the worktree declares its tail kind)
      files: .claude/skills/workqueue/skill.md

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q tests/conductor/test_compile.py
      tests/workqueue/test_queue_triage.py`, then the full `PYTHONPATH=src python3.14 -m
      pytest -q` and `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`,
      then `python3.14 scripts/ci/ruff_pinned.py check .`, `python3.14 scripts/ci/ruff_pinned.py
      format --check .` and `python3.14 scripts/ci/check_shebang_exec_bits.py`. Then exercise
      the fix end to end against the change this brief unblocks: with a scratch cache dir
      captured in a shell variable, run `worktrail-compile --force
      openspec/changes/quarantine-recovery-command --cache-dir "$scratch"` on the unmodified
      change and confirm exit 0 with none of the four declaring tasks (5.1, 6.1, 7.1, 8.1) in
      any scope-gap report — those four are the regression this task exists to check. The one
      undeclared task (9.1) is the inference pass's job: a gap report naming 9.1 alone is that
      pass declining a genuinely undeclared task, not this defect. The scratch dir keeps the
      shared plan entry untouched, whose healthy copy a shared-cache `--force` overwrites; a
      second plain `worktrail-compile openspec/changes/quarantine-recovery-command --cache-dir
      "$scratch"` serves the cache hit. Finally `openspec validate folded-task-compile-scope
      --strict` and `worktrail-compile openspec/changes/folded-task-compile-scope` pass.
      depends: 1.1, 2.1, 3.1
