## 1. Collect and forward Bash command text (`suggest_next_step` hook)

- [x] 1.1 In `hooks/suggest_next_step.py`, add a helper that extracts the raw Bash tool-call
      `command` text from one transcript entry (mirroring `durable_artifact_paths_from_entry`'s
      shape), and have `scan_transcript` collect these during its existing single pass, returning
      them as a fourth value (`bash_commands: list[str]`) alongside `has_work`, `run_record_paths`,
      and `durable_artifact_paths`. Update `substantive_work` (discards the extra value) and
      `main`'s call site accordingly. Update `check_dedup_gate` to accept the collected commands
      and forward each one to the checker binary as a separate `--bash-command` argument; wire
      `main` to pass them through.
      (Requirement: Merged Docs-Only Spec PR Detection Is Transcript-Local — Scenario "Hook
      forwards collected Bash commands to the checker")
      files: hooks/suggest_next_step.py
- [x] 1.2 [depends: 1.1] Update `hooks/test_suggest_next_step.py`'s existing `scan_transcript`
      call sites that unpack its return tuple (3-tuple assertions and the `missing ==
      (False, [], [])` equality) for the new fourth value. Add a test that `scan_transcript`
      collects Bash command text in transcript order and a test that `check_dedup_gate` forwards
      it via `--bash-command`. Add a hook-level end-to-end test — through `main()` against the
      real `worktrail-check-durable-artifact-capture-gate` shim (mirroring
      `test_main_hit_appends_dedup_gate_block_naming_artifact`) — asserting a transcript with a
      durable-artifact edit plus a `gh pr merge` Bash command produces a `merged_docs_only_spec_pr`
      dedup-gate block, closing the "In-session spec merge detected" scenario's implementation
      gap end to end.
      (Requirement: Merged Docs-Only Spec PR Detection Is Transcript-Local — Scenario "In-session
      spec merge detected")
      files: hooks/test_suggest_next_step.py

## 2. Verification

- [x] 2.1 [depends: 1.2] [e2e] Run `PYTHONPATH=src pytest -q hooks/test_suggest_next_step.py`,
      then `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate stop-hook-bash-command-passthrough --strict` and
      `worktrail-compile openspec/changes/stop-hook-bash-command-passthrough`.
