## 1. Gate-interpreter check and CLI

- [ ] 1.1 Add `src/worktrail/router/check_gate_interpreter.py` (stdlib only, read-only):
      `gate_interpreter_tools(policy)` splits each resolved `pre_pr_cmd` / `pre_commit_cmd` /
      `integrate_smoke_cmd` value on shell separators (`&&`, `||`, `;`, `|`), skips leading
      `VAR=value` assignments while recording them as the segment's env prefix, takes the segment's
      command word, keeps segments whose command-word basename matches a Python interpreter
      (`python`, `python3`, `pythonX[.Y]`), and for each kept segment collects every `-m <module>`
      target (both the separated form and an attached `-m<module>`), reduced to its top-level name;
      returns one entry per (interpreter word, tool) carrying the env prefix, the policy key, and
      the command text that named it. `probe_tool(path, module, env, cwd)` runs
      `<path> -c "<snippet using importlib.util.find_spec>"` with the segment's env prefix merged
      into the environment and `cwd=repo` under a bounded timeout, and returns found / missing /
      undetermined — the snippet locates the top-level name and never imports it, and a timeout, a
      non-zero exit other than the missing sentinel, or unreadable output is undetermined.
      `check_gate_interpreter(repo, policy=...)` assembles a report with `ok`, `repo`,
      `requires_python`-style context omitted (this check is about tools, not versions), and per
      finding `interpreter`, `path` (or that PATH provides none), `tool`, `sources` (policy key +
      command), `problem`; an interpreter word PATH does not resolve is skipped, not reported (the
      interpreter-floor check owns missing and below-floor interpreters). `main(argv)` implements
      `worktrail-check-gate-interpreter --repo PATH [--json]`: it prints one `FAIL:` line per
      finding plus the remediation — naming `<repo>/.venv/bin` as the fix when that directory
      exists and provides a matching interpreter, else installing the tool into the interpreter the
      command resolves or pointing the command at one that provides it — prints an OK line naming
      the checked interpreters and tools otherwise, exits 0 (including when no gate command names a
      Python `-m` target), 1 on findings, 2 on usage errors, and never writes to the repo. Register
      `worktrail-check-gate-interpreter = "worktrail.router.check_gate_interpreter:main"` in
      `[project.scripts]`.
      (Requirements: The check verifies the tools each gate command invokes are available to the
      interpreter that command resolves; Availability is determined without executing gate commands
      or repository code; Findings are actionable, complete, and side-effect free)
      Add `tests/router/test_check_gate_interpreter.py` covering: `-m` extraction (env-assignment
      prefix, `&&`/`||`/`;`/`|` chaining, `uv`/`bash`/bare `pytest` words and a `python script.py`
      path target ignored, attached `-m<module>`, dotted target reduced to its top-level name,
      multiple policy keys naming one tool); probe behaviour against a stubbed PATH built from
      temporary executable scripts (a resolved `python3.14` whose locate probe reports the tool
      missing, one that reports it present, one that exits non-zero, one that times out, one that
      emits unreadable output — each yielding missing or undetermined, never a silent pass); the
      skip behaviour for an interpreter word PATH does not resolve; an all-compliant policy and a
      policy naming no `-m` target both exiting 0; the remediation text naming `<repo>/.venv/bin`
      when it exists and its fallback wording when it does not; CLI exit codes and `--json` report
      shape. Hermetic: temporary repos and PATH stubs, no dependence on the host's real
      interpreters, no writes outside `tmp_path`.
      files: pyproject.toml src/worktrail/router/check_gate_interpreter.py tests/router/test_check_gate_interpreter.py

## 2. Refuse the launch before workers start

- [ ] 2.1 In `src/worktrail/orchestrator/live.py`, run the check for the resolved target repo at
      the top of `full_real()` — before the `RunLock`, any worktree, the run journal, or any spawn —
      and raise a dedicated `GateInterpreterError` carrying the report when it fails; catch that
      error in `main()`'s `full-real` branch to print the report and return 1. Leave every other
      path (a compliant environment, a repository whose gate commands name no verifiable tool,
      `_full_real_inner`, `precheck`) untouched.
      (Requirement: Orchestrated launches refuse a gate environment that cannot run its own gates)
      Add `tests/orchestrator/test_live_gate_interpreter_guard.py`: with PATH stubbed so the
      policy's `integrate_smoke_cmd` interpreter cannot provide `pytest`, `full_real()` against a
      temporary repo raises `GateInterpreterError` and creates no worktree or journal, and the
      `full-real` CLI exits 1 having printed the finding and its remediation; a compliant PATH stub
      and a repo declaring no gate commands naming a Python `-m` target both leave `full_real()`
      behaving exactly as before.
      files: src/worktrail/orchestrator/live.py tests/orchestrator/test_live_gate_interpreter_guard.py
      depends: 1.1

## 3. Make the documented launch path resolve the repository's own interpreter

- [ ] 3.1 In `skills/worktrail-go/references/subagent-prompts.md`'s `#orchestrator` block, before
      the `worktrail-detach launch` call, prepend the repository-local interpreter to PATH in the
      block's own shell when the directory exists — `$SPEC_ROOT/.venv/bin` then `$REPO/.venv/bin`
      — so the detached orchestrator inherits a PATH whose `python3.14` is the repository's own, and
      run `worktrail-check-gate-interpreter --repo "$SPEC_ROOT"` so a still-incapable environment is
      reported before the detach rather than after the fan-out. State in the block's own comment
      voice why the prepend is safe (the check immediately verifies the resolution it produced, so
      a stale or broken `.venv` becomes a pre-fan-out finding, not a silent choice) and that
      `full-real` itself refuses such a launch, so a caller that bypasses this block fails loudly
      rather than quarantining. The block shall name only `worktrail-check-gate-interpreter` and
      already-documented `worktrail-*` commands; the entry point it names is registered by 1.1.
      (No new capability requirement: this task changes the skill's documented launch procedure,
      not the check's contract.)
      files: skills/worktrail-go/references/subagent-prompts.md
      depends: 1.1

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q
      tests/router/test_check_gate_interpreter.py
      tests/orchestrator/test_live_gate_interpreter_guard.py`, then `PYTHONPATH=src python3.14 -m
      pytest -q` and `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`, then
      `python3.14 scripts/ci/ruff_pinned.py check .`, `python3.14 scripts/ci/ruff_pinned.py format
      --check .`, `python3.14 scripts/ci/check_shebang_exec_bits.py`, and `python3.14 -m build`.
      Run `PYTHONPATH=src python3.14 -m worktrail.router.check_gate_interpreter --repo .` and
      confirm it exits 1 with a finding naming `python3.14`, `pytest`, `integrate_smoke_cmd`, and
      the `<repo>/.venv/bin` remediation on a host whose resolved `python3.14` lacks the dev extras
      (the motivating state), and exits 0 once that directory is first on PATH. Confirm the launch
      block's prepend-and-check sequence on a scratch copy of the block executed under `sh -e` with
      a stubbed `.venv`. Run `openspec validate interpreter-dev-extras-preflight --strict` and
      `worktrail-compile openspec/changes/interpreter-dev-extras-preflight`. No file changes are
      expected from this task.
      (Requirements: Orchestrated launches refuse a gate environment that cannot run its own gates;
      Findings are actionable, complete, and side-effect free)
      depends: 1.1, 2.1, 3.1
