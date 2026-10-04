## 1. Carry a run-record failure's stdout message into the landing detail

- [ ] 1.1 Give `_run_record_main` the same stdout fallback its sibling already has, restoring
      conformance with the capability's *Run record is completed with a real state* requirement
      ("its detail contains the run-record tool's failure message", whether the tool wrote it
      to stdout or to stderr).
      Repro first: in `tests/router/test_land_pr.py`, add a `RunRecordMainCaptureTests` class
      (alongside the existing `RunPreflightAndLabelsTests`) whose tests drive
      `land_pr._run_record_main(["finish", "runs/x.yaml"])` with
      `mock.patch.object(land_pr.run_record_module, "main", ...)` covering:
      (a) main prints "run-record finish failed" to stdout and raises `SystemExit(1)` -- assert
      the returned `detail` is that stdout message; run it against the CURRENT code and
      confirm it FAILS for the right reason (detail comes back empty), before touching the
      helper;
      (b) main raises `SystemExit("scope_completeness_gate: ...")` -- assert `detail` is the
      string verbatim (the precedence must survive: a caught string code is never printed to
      either stream, so the string is its only carrier);
      (c) main raises `SystemExit(0)` with no output -- assert `(0, "", "")`;
      (d) main writes to stderr and raises `SystemExit(1)` -- assert `detail` is the stderr
      message (stderr keeps preference over stdout);
      (e) main prints to stdout and returns 1 without raising -- assert `detail` is the stdout
      message; this non-`SystemExit` non-zero return reaches the same final expression, so the
      same one-line fix must cover it too.
      Then in `src/worktrail/router/land_pr.py`: change `_run_record_main`'s final return to
      `return exit_code, out.getvalue(), detail or err.getvalue().strip() or out.getvalue().strip()`
      -- exactly the chain `_preflight_main` (`land_pr.py:437`) returns -- and update its
      docstring to state the new composition (a string `SystemExit` code becomes the detail,
      else the captured stderr, else the captured stdout, else `""`), mirroring
      `_preflight_main`'s docstring wording. Do not change the string-code precedence, the
      second tuple element (raw stdout, which `_ensure_run_record` parses `start`'s JSON path
      line from), or any call site. Run `PYTHONPATH=src python3.14 -m pytest -q
      tests/router/test_land_pr.py` to see the new tests pass.
      (Requirement: Run record is completed with a real state.)
      files: src/worktrail/router/land_pr.py, tests/router/test_land_pr.py

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q
      tests/router/test_land_pr.py`, then `PYTHONPATH=src python3.14 -m pytest -q` and
      `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check` -- all green.
      Run `python3.14 scripts/ci/ruff_pinned.py check .`,
      `python3.14 scripts/ci/ruff_pinned.py format --check .` and
      `python3.14 scripts/ci/check_shebang_exec_bits.py`. Run `openspec validate
      pr-landing-pipeline-run-record-failure-detail --strict` and `worktrail-compile
      openspec/changes/pr-landing-pipeline-run-record-failure-detail`. (`python3.14` is a PATH
      lookup; `export PATH="$PWD/.venv/bin:$PATH"` if it resolves to an interpreter without
      the dev extras.)

## Notes

- `pr-landing-pipeline` under `openspec/specs/` is updated only by archive/sync, never edited
  by hand from this change directory.
