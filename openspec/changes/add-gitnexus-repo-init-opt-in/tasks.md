## 1. `enable_gitnexus` and CLI wiring

- [x] 1.1 Add `enable_gitnexus(repo: Path) -> Tuple[bool, Optional[str]]` to
      `src/worktrail/onboarding/repo_init.py`, placed near `enable_aspens`: return
      `(False, None)` immediately if `(repo / ".gitnexus").is_dir()`; otherwise run
      `gitnexus analyze --embeddings --index-only <repo>` via the existing `_run()` helper,
      swallow `subprocess.TimeoutExpired`/`OSError`, and verify success by checking
      `(repo / ".gitnexus").is_dir()` afterward rather than trusting the return code, returning
      a warning string on failure. (Requirement: Idempotent bootstrap indexing) (Requirement:
      Best-effort indexing with postcondition verification) (Requirement: Bootstrap indexing
      skips AGENTS.md/skills file injection)
- [x] 1.2 Add a `--with-gitnexus` boolean flag to the `propose` subparser in `build_parser`
      (`main()`), matching `--with-aspens`'s `action="store_true"` style and help-text tone.
      (Requirement: `--with-gitnexus` opt-in flag on `propose`)
- [ ] 1.3 In `cmd_propose`, after the existing `--with-aspens` block, call
      `enable_gitnexus(repo)` when `args.with_gitnexus` is set, appending to `written` (e.g.
      `".gitnexus/ (gitnexus analyze)"`), `skipped` (e.g. `.gitnexus/ (already indexed)"`), or
      `warnings` exactly like the `--with-aspens` block does. Do not touch
      `default_policy_yaml()` or add an `add_ons.gitnexus` key anywhere. (Requirement:
      `--with-gitnexus` opt-in flag on `propose`) (Requirement: No per-task add-on wiring)

## 2. Tests

- [ ] 2.1 Add `EnableGitnexusTests` to `tests/onboarding/test_repo_init.py`, mirroring
      `EnableAspensTests`: a noop-when-already-indexed test (`.gitnexus/` dir pre-created,
      `subprocess.run`/`repo_init._run` mocked and asserted not called), a
      successful-indexing test (mocked subprocess call creates `.gitnexus/` as a side effect,
      asserting `configured=True` and no warning), and a failed/timeout test (mocked
      subprocess call does not create `.gitnexus/`, asserting `configured=False` and a warning
      mentioning the failure). (Requirement: Idempotent bootstrap indexing) (Requirement:
      Best-effort indexing with postcondition verification)
- [ ] 2.2 Add propose-level tests mirroring `test_with_aspens_writes_add_ons_block_and_runs_configure`,
      `test_without_aspens_flag_leaves_policy_file_bare`, and
      `test_aspens_warning_surfaces_without_failing_propose`: with `enable_gitnexus` mocked via
      `mock.patch.object(repo_init, "enable_gitnexus", ...)`, assert `--with-gitnexus` wires
      through to `cmd_propose`'s `written`/`skipped`/`warnings` lists, that the flag being
      omitted invokes no GitNexus behavior at all, and that a warning from `enable_gitnexus`
      surfaces in `result["warnings"]` without failing `propose` (`rc == 0`). Also assert the
      written `.worktrail/policy.yaml` never contains a `gitnexus` key regardless of the flag.
      (Requirement: `--with-gitnexus` opt-in flag on `propose`) (Requirement: No per-task
      add-on wiring)
- [ ] 2.3 [e2e] Run `PYTHONPATH=src pytest -q` and confirm the full suite passes, then run
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` and confirm the
      golden record/replay regression still passes.

## 3. Documentation

- [ ] 3.1 Update `skills/worktrail-repo-init/SKILL.md`: replace the "There is no equivalent
      flag for GitNexus..." sentence (Step 2, around the `--with-aspens` guidance) with
      guidance to also ask the user whether to pass `--with-gitnexus`, and add a
      `--with-gitnexus` bullet to the "Best Practices and Constraints" section alongside the
      existing `--with-aspens` bullet, describing the idempotency check, `--index-only`
      rationale, and best-effort/warning-on-failure behavior. (Requirement: `--with-gitnexus`
      opt-in flag on `propose`)
