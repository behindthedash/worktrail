## 1. Stage 1 — default flip + freeze (v1.0)

- [x] 1.1 Flip `full-real` CLI default to the pipelined engine; keep `--pipeline` as no-op affirmation
- [x] 1.2 Add DEPRECATED `--sequential` escape hatch with a loud runtime warning
- [x] 1.3 Update GO/sdd-workflow skill docs to reflect the pipelined default
- [x] 1.4 Record the no-new-fixes freeze policy for the serial path

## 2. Stage 2 — deletion (v1.1)

- [ ] 2.1 Delete the serial branch of `_full_real_inner` (tick loop + INTEGRATE/VERIFY tail); `--sequential` becomes a hard error naming this change
- [ ] 2.2 Migrate/delete tests pinning serial seams (test_live_pipeline_flag, SequentialTailDispatchTest, test_quarantine_journal_persistence serial legs, ...)
- [ ] 2.3 Decide `integrate.finish_real` disposition (live-unused after 2.1): remove with tests, or document the external consumer that keeps it
- [ ] 2.4 [e2e] Drop the lifecycle harness's sequential matrix leg and confirm the full suite + golden replay green
