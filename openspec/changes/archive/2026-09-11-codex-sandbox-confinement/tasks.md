## 1. Shared sandbox helper

- [x] 1.1 Add `src/worktrail/shared/codex_sandbox.py` with `codex_sandbox_args(cwd, repo=None,
      extra_roots=())` returning `-s workspace-write`, the
      `sandbox_workspace_write.network_access=true` override, and one `--add-dir` per root
      from design D3 (cwd, git common dir, operator state dir, work-queue root,
      `<repo>-worktrees`, extras, `WORKTRAIL_CODEX_EXTRA_WRITABLE_ROOTS`), de-duplicated in
      stable order, plus a public `git_common_dir(cwd)`; `WORKTRAIL_CODEX_SANDBOX_MODE`
      handling per design D5 (stderr notice for `danger-full-access`, `ValueError` naming
      the variable otherwise). Tests cover the default root set for a plain dir and a linked
      worktree, the repo sibling root, extras merged, de-dup/stable order, both env vars,
      and that a home directory is never emitted.
      Implements "Writable roots come from one shared helper" and "Operator escape hatches
      are explicit and loud".
      files: src/worktrail/shared/codex_sandbox.py, tests/shared/test_codex_sandbox.py

## 2. Skill dispatch and orchestrator worker launch sites

- [x] 2.1 In `src/worktrail/router/skill_dispatch.py`, replace the hardcoded
      `["-s", "danger-full-access"]` in `build_command` with the shared helper called with
      the child cwd and caller `add_dirs` as extras, keep `-C cwd`, and correct the
      docstring's loopback justification to describe the network-access override. Tests
      assert the `workspace-write` shape, absence of `danger-full-access`, `--add-dir X`
      merged with the default roots, and stable `--dry-run --json` output. Flip the Codex
      write gate in the lifecycle fake agent to `-s workspace-write` (docstring updated) and
      update the lifecycle test's expected Codex argv, asserting the `--add-dir` set includes
      the child cwd's git common dir and the run-record directory.
      Then in `src/worktrail/orchestrator/spawnlib.py` (which imports the dispatch
      module, so the two edit together), add a `cwd` keyword to `build_cmd` (passed at its
      three in-module call sites and by `check_agent_contract.py`), replace the hardcoded
      flag with the shared helper, and switch the opencode permission config to the
      helper's `git_common_dir`. Tests assert `workspace-write` plus an `--add-dir` for the
      worker cwd's common dir, no `danger-full-access`, and unchanged opencode
      `external_directory` roots.
      Implements "Codex children launch under workspace-write" and "Lifecycle harness gates
      Codex writes on the sandbox flag".
      depends: 1.1
      files: src/worktrail/router/skill_dispatch.py, tests/router/test_skill_dispatch.py, tests/orchestrator/lifecycle/fake_propose_agent.py, tests/router/test_internal_dispatch_lifecycle.py, src/worktrail/orchestrator/spawnlib.py, src/worktrail/orchestrator/check_agent_contract.py, tests/orchestrator/test_spawnlib.py, tests/orchestrator/test_check_agent_contract.py

## 3. Drain launch site

- [x] 3.1 In `src/worktrail/drain/drain.py`, drop `danger-full-access` from `BASE_CMDS` and
      `build_command`, add `repo_sandbox_roots(repos_root, repo)` enumerating `<child>/.git`
      and `<child>-worktrees` per git checkout under `--repos-root` (only `--repo` when
      given), and pass the shared helper's args for the scratch cwd with those roots at the
      one-shot spawn. Tests cover the Codex argv shape and `repo_sandbox_roots` over a temp
      repos root with two checkouts and one non-git dir, with and without the filter.
      Implements "Codex children launch under workspace-write" and "Drain grants per-repo
      git and worktree roots, not checkouts".
      depends: 1.1
      files: src/worktrail/drain/drain.py, tests/drain/test_drain.py

## 4. Verification

- [x] 4.1 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`; then a live smoke: a Codex dry-run dispatch
      from a linked worktree shows the common dir and `-worktrees` roots, and one real Codex
      dispatch in a scratch worktree commits successfully under the sandbox.
