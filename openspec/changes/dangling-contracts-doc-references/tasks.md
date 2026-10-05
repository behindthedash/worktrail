## 1. Resolve every dangling contracts citation in place

- [ ] 1.1 Drop or replace, in place, each dangling root-relative `contracts/*.md` citation,
      leaving every site's prose self-contained and behavior untouched; do not create a
      `contracts/` directory anywhere. In `src/worktrail/orchestrator/coordinator.py` (~:117)
      drop the wrapped `(contracts/frontier-external-deps-gate.md)` parenthetical and rewrap
      the comment, keeping the rule it states (`external_deps_ok` is precomputed by `live.py`
      because resolving it needs a filesystem read this module must not do). In
      `src/worktrail/orchestrator/live.py` drop `(contracts/precheck-external-deps-report.md)`
      (~:1383), `(contracts/frontier-external-deps-gate.md)` (~:1741),
      `(contracts/worker-context-worktree-stacking.md Part A)` (~:2001), and
      `(contracts/worker-context-worktree-stacking.md Part B)` (~:2104), rewrapping each
      comment. In `src/worktrail/orchestrator/dispatch.py` (~:620) drop
      `(contracts/worker-context-worktree-stacking.md Part A)` from `build_worker_prompt`'s
      docstring. In `src/worktrail/router/run_record.py` (~:75) replace the `active-conflicts`
      entry's stale "prints a JSON array (see contracts/active-conflicts-cli.md)" with the
      shape `cmd_active_conflicts` actually prints -- the `{"live": [...], "stale": [...]}`
      partition -- matching that command's own docstring. In
      `src/worktrail/router/spec_sync_sweep_brief.py` (~:17) drop the citation to
      `contracts/drift-brief.event.md`, keeping the clause naming `work_queue.py`'s
      own pre-success validation convention. In
      `src/worktrail/router/spec_sync_sweep_checkbox_brief.py` (~:20) drop the citation to
      `../contracts/checkbox-drift-brief.event.md`, keeping the clause naming the
      parent `file_drift_brief()`'s validation convention. In
      `src/worktrail/taskformats/devkit/source.py` (~:355) drop the sentence pointing at
      `contracts/cross-spec-resolution.md`'s "Cross-Spec Resolution Result", letting the
      `Returns {"ref", "resolved", "satisfied", "status", "reason"}` clause stand as the field
      contract. In `src/worktrail/workqueue/dist_tag_watch.py` (~:11) drop the
      `../../../../../docs/specs/015-.../contracts/watch-field.event.md` path (it resolves
      outside this repository), keeping the `watch:` grammar listing that follows it. In
      `tests/orchestrator/test_precheck.py` (~:794) drop "per
      contracts/precheck-external-deps-report.md" from `TestPrecheckExternalDeps`' docstring.
      Leave the three legitimate uses untouched: the devkit spec-folder mention in
      `dispatch.py:667`, the `contracts/*.md` glob in `router/classify_handoff.py:79`, and
      `drain/summary_contract.py:9`'s `.fixtures/contracts/nightly-drain-summary-v1.json`.
      (Requirements: Root-relative contracts doc citations resolve; The active-conflicts
      description matches its command)
      files: src/worktrail/orchestrator/coordinator.py, src/worktrail/orchestrator/dispatch.py, src/worktrail/orchestrator/live.py, src/worktrail/router/run_record.py, src/worktrail/router/spec_sync_sweep_brief.py, src/worktrail/router/spec_sync_sweep_checkbox_brief.py, src/worktrail/taskformats/devkit/source.py, src/worktrail/workqueue/dist_tag_watch.py, tests/orchestrator/test_precheck.py

## 2. Structural guard against dangling contracts citations

- [ ] 2.1 Add `tests/test_no_dangling_contracts_doc_refs.py`, in the
      `tests/test_no_bare_head_ctx_default.py` shape: a checker callable that takes a root,
      reads every `*.py` under its `src/` and `tests/` trees, and reports each root-relative
      `contracts/<name>.md` citation whose path does not exist at that root, with file and
      line. A citation is a token-start `contracts/` (not preceded by a path character)
      followed by a Markdown filename, including a filename split across a comment
      continuation -- both wrapped sites in this repository broke mid-token -- which must be
      reconstructed and checked the same way. Not citations: prefixed paths
      (`docs/specs/<id>/contracts/x.md`, `../contracts/x.md`), the glob `contracts/*.md`, a
      bare `contracts/` directory mention, and non-Markdown targets
      (`.fixtures/contracts/*.json`). The live assertion runs the checker over the real
      repository and requires no citation, and fails loudly if either scan tree is missing
      rather than passing on an empty walk. Fixture cases, each against a temporary tree: a
      single-line dangling citation is reported with file and line; a comment-wrapped
      dangling citation is reported; a citation whose target exists at the fixture root is
      not reported; and each excluded shape above is not reported. The module docstring
      states the defect class, that the cited documents never existed in this repository
      (developer-kit path remnants), and that the remedy for a future need is to state the
      contract in place or cite the owning OpenSpec spec -- never to author a root
      `contracts/` tree.
      (Requirements: Root-relative contracts doc citations resolve)
      files: tests/test_no_dangling_contracts_doc_refs.py
      depends: 1.1

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src pytest -q tests/test_no_dangling_contracts_doc_refs.py
      tests/orchestrator/test_precheck.py`, then `PYTHONPATH=src pytest -q`, then
      `python3 scripts/ci/ruff_pinned.py check .`, `python3 scripts/ci/ruff_pinned.py format
      --check .`, `python3 scripts/ci/check_shebang_exec_bits.py`, and `PYTHONPATH=src
      python3 -m worktrail.orchestrator.orchestrate check`. Confirm by hand that a repo-wide
      grep for the cited basenames (`frontier-external-deps-gate`,
      `worker-context-worktree-stacking`, `cross-spec-resolution`,
      `precheck-external-deps-report`, `drift-brief.event`, `checkbox-drift-brief.event`,
      `active-conflicts-cli`, `watch-field.event`) over `src/` and `tests/` reports nothing,
      that the three legitimate `contracts/` uses still read as before, and that the
      repository root has no `contracts/` directory. Run `openspec validate
      dangling-contracts-doc-references --strict` and `worktrail-compile
      openspec/changes/dangling-contracts-doc-references`.
      depends: 1.1, 2.1
