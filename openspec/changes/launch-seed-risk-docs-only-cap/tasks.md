## 1. Seed the classifier's verdict, not a capped low

- [ ] 1.1 In `src/worktrail/router/pre_pr_gate.py`, make the docs-only risk cap opt-out-able by
      an explicit, caller-declared difference: add a `docs_only_cap: bool = True` keyword to
      `resolve_pr_labels()` and apply the cap (`pre_pr_gate.py:422-423`) only when it is true,
      add the CLI flag `--no-docs-only-cap`, and thread it as `docs_only_cap=False` from the
      `--labels-only` branch. Accept the flag **only** with `--labels-only`: on the `--risk`
      path (the in-worktree gate, whose inspected diff *is* the PR's own) print an error and
      return `UNCONFIGURED_EXIT` without printing labels, so the cap cannot be silently disabled
      where it is correct. When the cap does lower the risk, print one line to `sys.stderr`
      naming the lowering (`risk <from> -> low`) and the `docs_only_paths` source; print nothing
      when it does not lower the risk, and leave stdout (the label set callers parse) unchanged
      byte for byte. Extend the `resolve_pr_labels()` docstring and the module docstring with
      the why: the cap's premise is real diff ground truth, so it may only fire where the
      inspected diff is the labeled change's own; the launch seed's `--repo` is the
      change-authoring worktree, whose committed diff is the change's own spec docs
      (`openspec/**` / `docs/**`), so the cap would force every seeded run to `low` regardless
      of the classifier's verdict, and the seed is every group PR's only risk input
      (`integrate.py:1902` extracts it; `land_pr.py:636` recomputes labels from it where the cap
      cannot fire).
      (Requirements: The docs-only risk cap applies only to the labeled change's own diff; The
      launch seed never applies the docs-only risk cap; A risk lowered by the docs-only cap is
      reported)
      Extend `TestDocsOnlyRiskCap` in `tests/router/test_pre_pr_gate.py`, on the class's existing
      docs-only fixture repo (`_init_repo`/`_write`/`_commit` + patched `required_checks_gate`):
      a docs-only checkout with `--risk high --labels-only --no-docs-only-cap` prints
      `go:risk-high go:no-automerge`; the identical invocation without the flag still prints
      `go:risk-low` (the default is unchanged); `--no-docs-only-cap` without `--labels-only`
      exits `UNCONFIGURED_EXIT` with empty stdout; and on the capped run, captured stderr names
      the `high` to `low` lowering while an uncapped run and an already-`low` run each print
      nothing there.
      files: src/worktrail/router/pre_pr_gate.py, tests/router/test_pre_pr_gate.py

- [ ] 1.2 Make the launch seed use it and lock the pair together. In
      `skills/worktrail-go/references/subagent-prompts.md:738`, add `--no-docs-only-cap` to the
      seed invocation (`worktrail-pre-pr-gate --repo "$SPEC_ROOT" --risk "$RISK_LEVEL" --gates
      "$GATES" --route "$ROUTE" --target-branch "$BASE" --labels-only`) and a comment above it
      recording the why: `$SPEC_ROOT` is the change-authoring worktree for the `new`/`modify`
      pipelines, its committed diff is the change's own spec docs (all matched by
      `docs_only_paths`), so the cap would seed `go:risk-low` for every run regardless of
      `RISK_LEVEL` — and that seed is what every group PR's label is recomputed from, where the
      cap can never fire. State the fail-loud trade in the same comment: an elevated verdict on
      a run that turns out to be docs-only seeds the elevated tier and gets a hand merge,
      which is the intended direction.
      (Requirements: The launch seed never applies the docs-only risk cap; The docs-only risk
      cap applies only to the labeled change's own diff)
      In `tests/router/test_skill_prose_enforcement_coverage.py`, register
      `worktrail-go/references/subagent-prompts.md` in `FILE_CONSUMERS` — the edit put the
      `go:risk-` literal in that file, which the marker scan in
      `extract_label_correction_mentions()` turns into a build failure until the file has a
      proof — with a new `_proves_launch_seed_skips_docs_only_cap` that asserts all three links:
      the seed invocation in the skill text still passes `--no-docs-only-cap`; the flag is a
      real option of `pre_pr_gate.main()`'s parser; and `main()`'s `--labels-only` branch
      actually reaches `resolve_pr_labels()` with the cap disabled — a structural check on
      `main()` (its source AST, or the names its code object references, as the other proofs in
      this file do). Dropping the flag from either side then fails this test rather than
      silently re-arming the misfire.
      (Requirement: The launch seed never applies the docs-only risk cap)
      depends: 1.1
      files: skills/worktrail-go/references/subagent-prompts.md, tests/router/test_skill_prose_enforcement_coverage.py

## 2. Operator-facing notes

- [ ] 2.1 In `.worktrail/policy.yaml`, extend the `docs_only_paths` comment block (lines 34-39)
      so it no longer states the cap unconditionally: the cap is applied by callers whose
      `--repo` diff is the labeled change's own (the in-worktree pre-PR gate and preflight's
      marker), while the launch seed passes `--no-docs-only-cap` because its repo is the
      change-authoring worktree whose committed diff is the change's own spec docs — record the
      incident shape in one line (run `go-20261003-211946`, `risk_level: medium`, seeded
      `go:risk-low` and PR #1415, which changed `src/worktrail/orchestrator/live.py`, merged at
      that tier) so the next reader does not have to re-derive it from source.
      (Requirement: The docs-only risk cap applies only to the labeled change's own diff)
      files: .worktrail/policy.yaml

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q tests/router/test_pre_pr_gate.py
      tests/router/test_skill_prose_enforcement_coverage.py tests/router/test_pre_pr_gate_parity.py
      tests/router/test_preflight.py tests/router/test_land_pr_resume.py`, then the full
      `PYTHONPATH=src python3.14 -m pytest -q` and
      `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`, then
      `python3.14 scripts/ci/ruff_pinned.py check .`, `python3.14 scripts/ci/ruff_pinned.py
      format --check .` and `python3.14 scripts/ci/check_shebang_exec_bits.py`. Then reproduce
      the incident end to end against a scratch repo shaped like a change worktree (policy with
      `docs_only_paths` including `openspec/**` on `main`; a `chg/`-style branch committing only
      `openspec/changes/<id>/proposal.md`): confirm `--risk medium --labels-only` (the misfiring
      shape, and the default this change must leave alone) prints `go:risk-low`, while
      `--risk medium --labels-only --no-docs-only-cap` (the launch seed's shape) prints
      `go:risk-medium`, with stderr on the capped run naming the lowering and empty on the
      uncapped run; confirm the
      same command against a checkout with an empty diff and against a mixed docs+`src/` diff
      prints `go:risk-medium` with no stderr line in both cases. Run
      `openspec validate launch-seed-risk-docs-only-cap --strict` and `worktrail-compile
      openspec/changes/launch-seed-risk-docs-only-cap`.
      depends: 1.1, 1.2, 2.1
