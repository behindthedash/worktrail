## 1. Neutral cwd for the implement pipeline (skill text)

- [x] 1.1 In `skills/worktrail-sdd-workflow/references/pipeline-details.md` `#implement-pipeline`,
      add a step `0. **Neutral cwd**` before 1a: set `NEUTRAL_CWD="$(dirname "$REPO")"`, and if
      `git -C "$NEUTRAL_CWD" rev-parse --is-inside-work-tree` succeeds use `NEUTRAL_CWD=$(mktemp -d)`
      instead; then `cd "$NEUTRAL_CWD"`. State that no later step may `cd "$REPO"`, `cd "$SPEC_ROOT"`
      or `cd` into any worktree (everything addresses the repo by path), that this is what keeps the
      host worktree write guard from denying the `mktemp`/`tee`/`rm -f`/detach calls in
      `#orchestrator`, and that a linked worktree must not be used as `SPEC_ROOT` because
      `<repo>-worktrees/`, `<repo>-integrate/` and the checkbox dirs are derived from `SPEC_ROOT`'s
      name (run state would land beside the linked worktree, invisible to resume/dashboard, plus the
      `expected 'main'` warning). Keep steps 1b and 2 on `SPEC_ROOT=$REPO`.
      In `skills/worktrail-go/references/subagent-prompts.md` `#orchestrator`, add one sentence to the
      prose above the bash block: the block assumes the shell's cwd is outside every checkout
      (`implement` step 0 / `new`'s workspace cwd); never prefix it with `cd "$SPEC_ROOT" &&`.
      (Requirement: Implement pipeline runs from a neutral shell cwd.)
      In `tests/test_plugin_surface.py`, add `test_implement_pipeline_runs_from_neutral_cwd`
      following the section-slicing style of `test_orchestrator_invocation_branches_spec_ref_on_detected_format`:
      slice `## \`implement\` pipeline {#implement-pipeline}` up to the next `---`; assert
      `NEUTRAL_CWD` and `cd "$NEUTRAL_CWD"` appear before `#precheck-gate`, that `cd "$REPO"` and
      `cd "$SPEC_ROOT"` do not appear, and that `SPEC_ROOT=$REPO` still appears for `#orchestrator`.
      files: skills/worktrail-sdd-workflow/references/pipeline-details.md, skills/worktrail-go/references/subagent-prompts.md, tests/test_plugin_surface.py

## 2. Throwaway checkout parent-dir cleanup

- [x] 2.1 In `src/worktrail/orchestrator/integrate.py`, add a module-level
      `_rmdir_if_empty(path: Path) -> None` that calls `os.rmdir(path)` and swallows `OSError`
      (non-empty, missing, or permission). Call it on `wt.parent` at the three teardown sites,
      inside the existing `with lock:` block right after the leaf's `git worktree remove` and a
      `shutil.rmtree(wt, ignore_errors=True)` (move the divergence helper's and
      `_integration_worktree`'s existing post-lock `rmtree` inside the lock so parent removal
      follows leaf removal atomically): `_integration_worktree`'s `finally`,
      `detect_checkbox_status_divergence`'s `finally`, and `sync_checkbox_status`'s `finally`.
      No other behaviour changes; never touch `<repo>-worktrees/`.
      (Requirement: Throwaway checkout parent directories are removed when empty.)
      In `tests/orchestrator/test_integrate.py`, beside the existing `_integration_worktree`
      real-git tests, add: after the context exits, `<repo>-integrate/` does not exist; and when a
      sibling dir is pre-created under `<repo>-integrate/`, both it and the parent survive.
      In `tests/orchestrator/test_checkbox_status_divergence.py`, add a test that after
      `detect_checkbox_status_divergence` returns, `<repo>-checkbox-check/` does not exist and a
      pre-existing `<repo>-worktrees/` marker file is untouched.
      files: src/worktrail/orchestrator/integrate.py, tests/orchestrator/test_integrate.py, tests/orchestrator/test_checkbox_status_divergence.py

## 3. Verification

- [ ] 3.1 [depends: 1.1, 2.1] [e2e] Run `PYTHONPATH=src pytest -q tests/test_plugin_surface.py
      tests/orchestrator/test_integrate.py tests/orchestrator/test_checkbox_status_divergence.py`,
      then `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate implement-pipeline-spec-root-neutral-cwd --strict` and
      `worktrail-compile openspec/changes/implement-pipeline-spec-root-neutral-cwd`.
