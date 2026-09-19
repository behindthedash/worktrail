## 1. Gate path needles on shape and strip node-ids

- [ ] 1.1 In `src/worktrail/router/brief_probes.py`, strip a pytest node-id suffix before path
      classification: in `extract_probes`, after `_strip_punct`, reduce a `::`-bearing token to
      its portion before the first `::` and run the existing `_is_path_token` test on that
      portion, emitting it as the probe when it passes. A token whose portion before `::` fails
      `_is_path_token` emits no path probe and still falls through to the symbol branch
      unchanged (`Foo::bar`). Document the rule in `_is_path_token`'s comment block and in
      `extract_probes`'s docstring paragraph on the negative rules.
      (Requirements: Path probes drop a pytest node-id suffix.)
      In `src/worktrail/workqueue/premise_check.py`, add `_is_filename_shaped(candidate: str)
      -> bool` (last segment matches an extension pattern, or the token ends in `/`) and have
      `_check_path` return an extra `"skip": True` key alongside its existing keys when the
      target does not exist and the candidate is not filename-shaped, in *both* the `absence`
      branch and the presence branch. In `run_premise_check`, skip appending a result row when
      the path outcome carries `skip`. Leave every other branch of `_check_path` as is, and note
      the rule in the module docstring.
      (Requirements: A non-existent, non-filename-shaped path needle produces no verdict; Real
      path needles keep their existing verdicts.)
      Add `tests/router/test_brief_probes.py` covering node-id stripping (plain,
      class-qualified, and `Foo::bar` emitting no path probe) plus a guard that
      `claude/codex/opencode` and `stub/disable` are still extracted (the extractor is
      repo-blind; the drop happens in `premise_check`). Extend
      `tests/workqueue/test_premise_check.py` with cases against a real temporary repo (reuse the
      file's existing repo fixture): `claude/codex/opencode` and `stub/disable` produce no row;
      the same token inside an absence window produces no row; an existing directory path still
      confirms; a missing `.py` path still returns an unconfirmed "path does not exist" row; a
      `:LINE` needle keeps its line-count detail; a node-id needle confirms against its file; and
      quoted/command rows are unchanged.
      files: src/worktrail/router/brief_probes.py, src/worktrail/workqueue/premise_check.py, tests/router/test_brief_probes.py, tests/workqueue/test_premise_check.py

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/router/test_brief_probes.py
      tests/workqueue/test_premise_check.py`, then `PYTHONPATH=src pytest -q` and `PYTHONPATH=src
      python3 -m worktrail.orchestrator.orchestrate check`. Run `openspec validate
      premise-check-path-needle-shape-filter --strict` and `worktrail-compile
      openspec/changes/premise-check-path-needle-shape-filter`.
