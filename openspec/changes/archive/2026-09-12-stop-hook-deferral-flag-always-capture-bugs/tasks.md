## 1. Stop hook: always capture unfixed defects, and mark only real Bash writes (`stop-hook-deferral-flag`, `durable-artifact-dedup-gate`)

Both problems live in the same two files, so they are one test-first task rather than a serial
chain (compile rejected a 5-task critical path over `hooks/suggest_next_step.py` +
`hooks/test_suggest_next_step.py`). Write every failing test first, confirm each fails against
the current hook for the stated reason, then change the hook.

- [x] 1.1 Fix both Stop-hook defects test-first in `hooks/test_suggest_next_step.py` and
      `hooks/suggest_next_step.py`.

      **A. Failing tests for mandatory defect capture.** Extend
      `test_instruction_is_worktrail_native_and_value_gated` rather than adding a parallel test.
      Assert that `hook.INSTRUCTION`:
      - requires one `worktrail-handoff` brief per distinct verified defect discovered but not
        fixed;
      - says the EXCEPTIONAL-VALUE gate does not apply to defects;
      - applies the gate and its "Do NOT capture routine polish" exclusions only to
        forward-looking ideas;
      - allows "No handoff captured; no exceptional next step identified." only when no defect
        brief was captured;
      - sends in-scope defects to the completion audit ("fix it now") instead of a handoff.

      Add a test that `hook.build_dedup_gate_block(...)`:
      - limits its suppression to the follow-up the listed artifacts already track;
      - says it never suppresses capture of a distinct defect those artifacts do not track;
      - still contains "Do NOT auto-capture", "suggestion-only line naming the resume command",
        `` `worktrail-go <brief-id>` ``, and `## Dedup justification`.

      **B. Failing tests for Bash write detection**, exercising `hook.scan_transcript` /
      `hook.durable_artifact_paths_from_entry` through the existing `_tool_entry` /
      `_write_entries` helpers.

      These commands must yield **no** durable paths:
      - `git ls-tree --name-only origin/dev docs/specs/005-x/changes/ docs/specs/001-y/changes/ 2>/dev/null`
      - `npm ci >/dev/null && worktrail-run-record note --text 'see docs/specs/005/changes'`
      - `ls openspec/changes/foo 2>&1 | head`
      - `pytest -q > /tmp/out.txt && grep -r todo docs/specs/001-task/`
      - `cat > /tmp/notes.md <<'EOF'` followed by a body line mentioning
        `rm docs/specs/x/spec.md` and a body line with an unbalanced apostrophe
      - a command with an unbalanced quote, which must also not raise

      These commands must yield **exactly** the written paths:
      - `cp docs/specs/001-task/design.md openspec/changes/new-idea/design.md` → destination only
      - `sed -i 's#docs/specs/old#docs/specs/new#' openspec/changes/new-idea/tasks.md` → the file
        operand only
      - `echo x | tee -a docs/specs/001-task/notes.md` → the `tee` file
      - `mv openspec/changes/a openspec/changes/archive/a` → both paths
      - `rm`, `touch`, and `mkdir -p` → their operands
      - `pytest 2> docs/specs/001-task/err.log` → the redirect target
      - `git mv docs/specs/a docs/specs/b` → both paths
      - `ls docs/specs/001-task` then a newline then `touch openspec/changes/y/tasks.md` → the
        `touch` operand only

      Add a `main()`-level test using `_install_check_durable_artifact_capture_gate_shim`. Its
      transcript has one `Write` to `/repo/src/main.py` plus the `2>/dev/null` `git ls-tree`
      read, and the test asserts stdout is byte-for-byte
      `json.dumps({"decision": "block", "reason": hook.INSTRUCTION}) + "\n"`, with no DEDUP GATE
      block. Leave `test_scan_transcript_collects_touched_durable_paths_from_bash_write_markers`
      unchanged.

      Run `pytest hooks/test_suggest_next_step.py -q` and confirm the A and B tests fail against
      the current hook before editing it.

      **C. Fix.** In `hooks/suggest_next_step.py`:
      - rewrite `INSTRUCTION` per design D1 and `build_dedup_gate_block` per design D2;
      - replace the `BASH_WRITE_MARKERS` substring gate and whole-command harvest in
        `durable_artifact_paths_from_entry` with the private write-target helper from design D3,
        failing open to no paths per design D4, and filter its results through
        `DURABLE_ARTIFACT_PATH_RE`;
      - run `rg "BASH_WRITE_MARKERS" .` from the repo root and remove the constant once nothing
        references it;
      - keep the edit-tool `file_path`/`notebook_path` collection and `scan_transcript`'s
        single-pass contract unchanged.

      The A and B tests must pass, and these existing tests must pass without modification,
      each still comparing against `hook.INSTRUCTION` rather than a stale copy of the old text:
      - `test_main_output_unchanged_when_run_record_flags_nothing`
      - `test_main_no_hit_output_byte_identical_to_pre_gate_instruction`
      - `test_main_fail_open_no_path_missing_binary_and_headless`
      - `test_main_fail_open_when_dedup_gate_binary_missing`
      - `test_main_hit_appends_dedup_gate_block_naming_artifact`, with its
        `reason == INSTRUCTION + build_dedup_gate_block(...)` assertion
      - the PR-ledger baseline tests
      files: hooks/test_suggest_next_step.py, hooks/suggest_next_step.py
      Covers: Unfixed verified defect is captured regardless of the gate; EXCEPTIONAL-VALUE gate governs only forward-looking ideas; In-scope defect is fixed, not handed off; Dedup hit never suppresses capture of an untracked defect; Additive And Non-Interfering; EXCEPTIONAL-VALUE gate output is unchanged; Both checks can fire in the same session; Downgrade-To-Suggestion On Dedup Hit; Hit downgrades to suggestion-only; Explicit justification escape hatch is stated in the instruction; No hit leaves the instruction unchanged; Discarded or duplicated fd redirect is not a write; Durable path mentioned beside an unrelated write is not marked; Bash write marks only the path it writes; Read-only spec access does not trigger detection; Session-Touched Durable-Artifact Detection; Edited OpenSpec change triggers detection

## 2. Verification

- [x] 2.1 [e2e] [depends: 1.1] Run the checks:
      - `PYTHONPATH=src pytest -q`
      - `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`
      - `openspec validate stop-hook-deferral-flag-always-capture-bugs --strict`

      Then replay the proposal's reproduced commands through
      `durable_artifact_paths_from_entry` and confirm each yields `[]`:
      - the `git ls-tree ... 2>/dev/null` read
      - the `npm ci >/dev/null` command with a quoted `docs/specs/005/changes` note
