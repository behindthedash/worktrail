## 1. Structural guard against biased merge strategy options (`biased-merge-strategy-guardrail`)

- [ ] 1.1 Implement requirements: Biased merge strategy options fail the build;
      Documenting the prohibition does not trip the guard; The guard blocks
      merges through an existing required check.
      Add `tests/test_no_biased_merge_strategy.py`, modelled on the existing
      `tests/test_no_bare_head_ctx_default.py` (module docstring stating the
      defect class and citing PR #414,
      `docs/specs/research/integrate-one-dep-branch-gone-fallback-root-cause.md`
      and `docs/specs/research/carry-squash-merged-dependencies-x-ours-risk.md`;
      `REPO_ROOT` derived from `Path(__file__).resolve().parent.parent`).
      Walk every `*.py` under `src/worktrail/` with `ast.parse`, collect
      `ast.Constant` string nodes, and skip the module/class/function docstring
      position so the explanatory prose at `integrate.py:682`,
      `integrate.py:1409` and `live.py:2273` keeps passing; let a `SyntaxError`
      fail the test rather than being swallowed (design.md Decision 2 and its
      Risks note). Flag a literal that is exactly `-X`, is `-Xours`/`-Xtheirs`,
      starts with `--strategy-option`, or is one of the `-s ours` /
      `--strategy=ours` / `--strategy ours` forms and their `theirs` variants;
      do not flag a bare `ours`/`theirs` literal (design.md Decision 2). Scan
      the whole package, not only `orchestrator/` (design.md Decision 4), and
      provide no allowlist or opt-out (design.md Decision 3). Assert with an
      offender list of `<path>:<line>: <literal>` entries and a message
      explaining the prohibition.
      The test file is itself both the implementation and its own coverage:
      assert the clean-baseline scenario by running the scan over the real
      `src/worktrail/` tree, and assert the detection and
      documentation-tolerance scenarios by running the same scan helper over
      small in-test source snippets written to `tmp_path` — one containing a
      biased-option call (expected to be reported), one containing only a
      comment and a docstring naming `-X ours` plus a bare `ours`/`theirs`
      literal (expected to be clean). No `src/` change is part of this task:
      the guarded baseline is already clean and this change adds only the
      enforcement.

## 2. Verification

- [ ] 2.1 [e2e] Run `pytest -q tests/test_no_biased_merge_strategy.py` and then
      the full `pytest -q`, and confirm both are green — in particular that the
      new guard passes against the current `src/worktrail/` tree unmodified.
- [ ] 2.2 [e2e] Run `openspec validate ci-guardrail-biased-merge-strategy
      --strict` and confirm it passes.
