## 1. Prompt states target_repo correctly per group (`Evidence-required verdict per brief`)

- [ ] 1.1 Implement requirement: In `src/worktrail/workqueue/queue_triage.py`, replace
      `EVALUATOR_PROMPT_TEMPLATE`'s single Step 2a `propose-change`/`target_repo` sentence
      (design.md Decision) with a `{propose_target_rule}` placeholder, and have
      `_evaluate_group()` compute its value using the same `repo == NO_REPO_KEY` branch
      already used for `known_repos`/`known_repos_str`: for a repo-bearing group, state that
      `target_repo` is that group's own repo with no known-repos allowlist; for the no-repo
      group, keep the existing "valid only when `target_repo` is one of these known repos:
      {known_repos}" sentence (including its "(none found)" case) unchanged. Add a regression
      test in `tests/workqueue/test_queue_triage.py` asserting the formatted prompt for a
      repo-bearing group states `target_repo` as that repo and does not contain the no-repo
      group's "valid only when ... one of these known repos" wording, and that the existing
      no-repo-group wording is unchanged.

## 2. Verification

- [ ] 2.1 [cleanup] Run `PYTHONPATH=src pytest -q` and confirm it is green, including the new
      test from section 1. Verification-only — no file changes expected.
- [ ] 2.2 [cleanup] Run `openspec validate fix-evaluator-prompt-propose-change-wording --strict`
      and confirm it passes. Verification-only — no file changes expected.
