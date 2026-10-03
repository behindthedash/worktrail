## 1. Interpreter-floor check and CLI

- [ ] 1.1 Add `src/worktrail/router/check_interpreter_floor.py` (stdlib only, read-only):
      `declared_floor(repo)` reads the target repo's `pyproject.toml` `[project]
      requires-python` and returns the highest `>=` lower bound as a version tuple, or None
      when there is no pyproject, no value, or no `>=` bound; `gate_interpreter_names(policy)`
      splits the resolved `pre_pr_cmd` / `pre_commit_cmd` / `integrate_smoke_cmd` values on
      shell separators (`&&`, `||`, `;`, `|`), skips leading `VAR=value` assignments, takes
      each segment's command word, and keeps those whose basename matches a Python interpreter
      (`python`, `python3`, `pythonX[.Y]`), recording which policy keys named it;
      `probe_interpreter(name)` resolves the word with `shutil.which` and, when found, runs it
      as `<path> -c "import sys; sys.stdout.write('%d.%d.%d' % sys.version_info[:3])"` under a
      bounded timeout, parsing `X.Y[.Z]` from stdout (missing / non-zero / timeout /
      unparseable each yield an undetermined result); `check_interpreter_floor(repo,
      policy=...)` assembles a report covering every gate-named interpreter plus the ambient
      `python3`, with `ok`, `requires_python`, `floor`, and per-interpreter `name`, `sources`
      (`ambient` and/or the policy keys), `path`, `version`, `ok`, `problem`. `main(argv)`
      implements `worktrail-check-interpreter-floor --repo PATH [--json]`: it prints one
      `FAIL:` line per finding plus the remediation (fix the ambient `python3`, install the
      missing interpreter, or pin the affected command), prints an OK line naming the floor
      and checked interpreters otherwise, exits 0 (including no enforceable floor), 1 on
      findings, 2 on usage errors, and never writes to the repo. Register
      `worktrail-check-interpreter-floor = "worktrail.router.check_interpreter_floor:main"` in
      `[project.scripts]`.
      (Requirements: The check enforces the target repository's declared Python floor; The
      checked interpreters are the ones the repository's commands and a shell will resolve;
      Version probing never fails at parse time and executes no repository code; Findings are
      actionable, complete, and side-effect free)
      Add `tests/router/test_check_interpreter_floor.py` covering: floor parsing (`>=3.14`,
      `>=3.10,<4`, absent key, non-`>=` specifier); interpreter-word extraction (env-assignment
      prefix, chained commands, `uv`/`pytest`/`bash` ignored, multiple policy keys per name);
      check/probe behaviour against a stubbed PATH built from temporary executable scripts
      (below-floor ambient `python3`, missing gate-named `python3.14`, undeterminable output,
      all-compliant, no ambient `python3` at all); the remediation text naming the floor; CLI
      exit codes and `--json` report shape. Hermetic: temporary repos and PATH stubs, no
      dependence on the host's real interpreters, no writes outside `tmp_path`.
      files: pyproject.toml src/worktrail/router/check_interpreter_floor.py tests/router/test_check_interpreter_floor.py

## 2. Refuse the launch before workers start

- [ ] 2.1 In `src/worktrail/orchestrator/live.py`, run the check for the resolved target repo at
      the top of `full_real()` — before the `RunLock`, any worktree, the run journal, or any
      spawn — and raise a dedicated `InterpreterFloorError` carrying the report when it fails;
      catch that error in `main()`'s `full-real` branch to print the report and return 1. In
      `precheck()`, run the same check first and, on findings, print them under a distinct
      `FAIL (interpreter-floor):` label and return 1 before any task-DAG diagnostic. Update
      `skills/worktrail-go/references/subagent-prompts.md`'s `#precheck-gate` section so that
      label is described as an environment abort — report it and stop, never "Proceed anyway",
      because `full-real` will refuse the launch.
      (Requirement: Orchestrator and worker launches refuse a below-floor environment)
      Add `tests/orchestrator/test_live_interpreter_floor_guard.py`: with PATH stubbed to a
      below-floor fake `python3`, `full_real()` against a temporary repo whose `pyproject.toml`
      declares `>=3.14` raises `InterpreterFloorError` and creates no worktree or journal, the
      `full-real` CLI exits 1 having printed the findings, and `precheck()` returns 1 with the
      interpreter-floor label ahead of DAG output; a compliant PATH stub, and a repo with no
      declared floor, leave both paths behaving exactly as before.
      files: src/worktrail/orchestrator/live.py tests/orchestrator/test_live_interpreter_floor_guard.py skills/worktrail-go/references/subagent-prompts.md
      depends: 1.1

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q
      tests/router/test_check_interpreter_floor.py
      tests/orchestrator/test_live_interpreter_floor_guard.py`, then `PYTHONPATH=src python3.14
      -m pytest -q` and `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`,
      then `python3.14 scripts/ci/ruff_pinned.py check .`, `python3.14
      scripts/ci/ruff_pinned.py format --check .`, `python3.14
      scripts/ci/check_shebang_exec_bits.py`, and `python3.14 -m build`. Run `PYTHONPATH=src
      python3.14 -m worktrail.router.check_interpreter_floor --repo .` and confirm it exits 0 on
      a host whose resolved interpreters all satisfy the floor, or exits 1 with the ambient
      `python3` as a finding on a host whose bare `python3` is below `>=3.14` (the motivating
      state). Run `openspec validate python-interpreter-floor-preflight --strict` and
      `worktrail-compile openspec/changes/python-interpreter-floor-preflight`.
      depends: 1.1, 2.1
