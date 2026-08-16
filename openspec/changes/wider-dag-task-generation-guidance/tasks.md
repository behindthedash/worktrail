## 1. Guidance edit

- [ ] 1.1 Add the per-phase hot-file ownership-bias guidance to `skills/openspec-propose/SKILL.md`'s tasks-artifact authoring step, as a new sub-bullet alongside the existing requirement-coverage and file-less-task rules: bias decomposition so a file recurring across more than one phase is owned by at most one task per phase (Requirement: Per-Phase Hot-File Ownership Bias)
- [ ] 1.2 In the same guidance addition, direct that an additive/composable hot file (e.g. registry or data-table entries) be split into separate per-phase files, each owned by one task, composed by a single later task (Requirement: Per-Phase File Split For Additive Hot Files)
- [ ] 1.3 State explicitly in the guidance addition that grouping-time collision-serialization (shared-file lane folding) is unchanged for whatever hot-file collision the bias does not avoid (Requirement: Collision-Serialization Preserved For Unavoidable Same-File Edits)
- [ ] 1.4 Add a short cross-reference note in `docs/design/conductor-lanes.md` §4.2 pointing at the new authoring-time guidance as a complementary mitigation to the grouping-time shared-file union, so the two are read as paired fixes for the same wide-fan-in-collapses-to-chain failure mode rather than redundant ones

## 2. Verification

- [ ] 2.1 [e2e] Confirm `skills/openspec-propose/SKILL.md` still passes `tests/test_plugin_surface.py` (no new skill directory or console script introduced by this change)
- [ ] 2.2 [e2e] Run `PYTHONPATH=src pytest -q && PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` and confirm both pass with no regressions
