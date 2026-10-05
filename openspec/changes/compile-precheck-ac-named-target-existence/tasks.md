## 1. The precheck module

- [x] 1.1 Add `src/worktrail/conductor/ac_targets.py` exposing
      `find_missing_ac_targets(spec_dir, repo) -> list[str]`, shaped after
      `conductor/req_coverage.py` (same signature style, same "return `[]` when nothing applies"
      contract, module docstring stating the narrow extraction rule and why an absent path is not
      reported here). For each task loaded from the change directory, split its text into sentences
      and report a finding only when one sentence contains an update verb (`update`, `modify`,
      `amend`, `extend`, `replace`, `rename`, `remove`, `fix`, `correct`) whose direct object is a
      backticked entity needle that is not itself an existing file path, plus a distinct backticked
      repo-relative path whose file exists under `repo`; the finding fires when exactly one such
      path is possible and its text does not contain the needle. Other backticked tokens never
      count as needles. Return findings as display strings naming the task id, the needle and path.
      Add `tests/conductor/test_ac_targets.py` covering: the
      `canonical-checkout-drift-sweep.sh` / `scripts/README.md` case from decision
      `dec-openspec-changes-canonical-checkout-unbo-925c3e395fc2` reporting a finding; the same text
      against a README that does contain the entry reporting nothing; additive phrasing (`Add a ...
      entry to ...`) reporting nothing; unbackticked prose reporting nothing; a backticked path that
      does not exist reporting nothing; and a change whose tasks contain no update sentence at all
      returning `[]`.
      (Requirements: Compile refuses an acceptance criterion naming an absent update target; The
      precheck only fires on an unambiguous update claim.)
      files: src/worktrail/conductor/ac_targets.py, tests/conductor/test_ac_targets.py

## 2. Compile gate wiring

- [x] 2.1 In `src/worktrail/conductor/compile.py`, import `ac_targets` alongside
      `req_coverage` and call `find_missing_ac_targets(spec_dir, repo)` in `main()` next to the
      existing `uncovered` call. Fold its findings into the same three decisions the other gates
      already drive: the `.compile-ok` marker is only written when no gate reports anything, the
      findings print through a new `_print_ac_target_gap_error` (stderr only, same shape as
      `_print_req_coverage_gap_error`, remedy text telling the author to correct the AC to match the
      base tree or reword it as an addition), and the return value is 1 in both the `--json` and the
      plain output branch.
      Extend `tests/conductor/test_compile.py` with gate cases mirroring the existing
      requirement-coverage gate tests: a change with a phantom-target AC exits 1, prints the finding
      to stderr and leaves no `.compile-ok` marker; the same change with the AC corrected exits 0
      and writes the marker; and `--json` on the failing change still prints a parseable plan on
      stdout while exiting 1.
      (Requirements: Compile refuses an acceptance criterion naming an absent update target.)
      files: src/worktrail/conductor/compile.py, tests/conductor/test_compile.py
      depends: 1.1

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src pytest -q tests/conductor`, then `PYTHONPATH=src
      pytest -q`, `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`, `python3
      scripts/ci/ruff_pinned.py check .` and `python3 scripts/ci/ruff_pinned.py format --check .`.
      Then `openspec validate compile-precheck-ac-named-target-existence --strict` and
      `worktrail-compile openspec/changes/compile-precheck-ac-named-target-existence`.
