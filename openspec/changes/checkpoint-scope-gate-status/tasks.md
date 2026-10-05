## 1. Checkpointed merged-PR status

- [ ] 1.1 Add focused regression coverage for a caller-supplied,
      scope-review-incomplete run whose checkpointed landing observes an
      already merged PR: assert `completed_and_merged` is appended as a
      decision, no terminal finish or implicit scope review is attempted, and
      retain non-checkpoint merged completion behavior. Make the smallest
      `land_pr` correction only if that coverage exposes a mismatch.
      (Requirement: Checkpointed merged landing preserves an active run record)
  files: src/worktrail/router/land_pr.py tests/router/test_land_pr_checkpoint_status.py

## 2. Verification

- [ ] 2.1 [e2e] Run the focused checkpoint-status tests, the router test
      suite, and the full test suite. Run `openspec validate
      checkpoint-scope-gate-status --strict` and `worktrail-compile
      openspec/changes/checkpoint-scope-gate-status`.
