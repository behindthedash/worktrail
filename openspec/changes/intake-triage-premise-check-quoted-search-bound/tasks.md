## 1. Bound and guard the quoted-string search

- [x] 1.1 In `src/worktrail/workqueue/premise_check.py`, close both unbounded-search defects
      in the quoted-string search. (a) Thread the existing `timeout_s` through
      `_git_grep_whole_string()` and `_git_grep_fragments()`: each takes `timeout_s` and
      passes `timeout=timeout_s` to its `subprocess.run()` call, and `run_premise_check()`'s
      quoted branch passes its own `timeout_s` into `_check_quoted()`. (b) Add the empty-line
      guard at the top of `_check_quoted()`, before either grep helper runs: when splitting
      the needle's text on `\n` yields an empty line (internal blank line, leading newline,
      or trailing newline), return `{"confirmed": false, "detail": ...}` with a detail
      stating the needle was not searched because its text contains an empty line. (c) Catch
      `subprocess.TimeoutExpired` in `_check_quoted()` around both helpers and return
      `{"confirmed": false, "detail": ...}` with a timeout detail, mirroring
      `_check_command()`'s existing handling. Restate both rules in the module docstring
      with their why: an empty-line-containing fixed pattern degenerates in `git grep` (the
      empty alternative matches every line), so it costs unbounded CPU in a large tree and,
      were it to finish, would return rc=0 over every line and spuriously confirm the
      needle; the timeout is the same bound the command needles already run under.
      (Requirement: Mechanical premise check precedes evaluation)
      Add `tests/workqueue/test_premise_check.py` coverage, in the file's existing
      real-`git init`'d-`repo` / monkeypatched-`subprocess.run` style: an empty-line quoted
      needle (e.g. `'short\n\nshort2'` from a focus spanning a blank line) spawns no
      `git grep` subprocess and is recorded `confirmed: false` with the empty-line detail
      even when the repo's tracked files contain the needle's non-empty text; a
      `subprocess.TimeoutExpired` raised by the quoted search's `git grep` yields
      `confirmed: false` with a timeout detail (mirroring
      `test_timeout_expired_is_unconfirmed_with_timeout_detail`); and every `git grep`
      subprocess call from both the whole-string search and the fragment fallback carries
      the caller's `timeout_s` (assert the captured `timeout` keyword equals a non-default
      value passed to `run_premise_check()`).
      files: src/worktrail/workqueue/premise_check.py, tests/workqueue/test_premise_check.py

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q
      tests/workqueue/test_premise_check.py`, then `PYTHONPATH=src python3.14 -m pytest -q`
      and `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`, then
      `python3.14 scripts/ci/ruff_pinned.py check .`, `python3.14 scripts/ci/ruff_pinned.py
      format --check .` and `python3.14 scripts/ci/check_shebang_exec_bits.py`. Then
      exercise the defect end to end: run `run_premise_check()` on brief `20261005-105710`'s
      focus (from `$WORK_QUEUE_DIR/picked/`) against this checkout and confirm all eight
      extracted needles return in seconds rather than minutes -- needle #0 (the 1124-char
      apostrophe-paired needle with a blank middle line) is `confirmed: false` with the
      empty-line detail and spawns no `git grep`, and the remaining seven single-line
      needles are recorded as before. Run `openspec validate
      intake-triage-premise-check-quoted-search-bound --strict` and `worktrail-compile
      openspec/changes/intake-triage-premise-check-quoted-search-bound`.
      depends: 1.1
