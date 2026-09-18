## 1. Verdict-from-file apply (`intake-triage`)

- [ ] 1.1 In `src/worktrail/router/skill_dispatch.py`: add
      `--apply-brief-triage-file` (metavar `VERDICT_PATH`, default `None`) in an argparse
      mutually-exclusive group with `--apply-brief-triage`, and include it in the
      "no `--skill`/`--agent` required" and "no `--payload` required" conditions alongside
      the inline flag. In the apply branch, when the file flag is set, read the file and
      `json.loads()` its text; on `OSError` or `json.JSONDecodeError` print the existing
      `status: error` entry shape with `error` naming the path and return 1. Feed the parsed
      payload through the same null / non-object / verdictless checks and the same
      `apply_single_brief_verdict()` call as the inline form. Update the `--apply-brief-triage`
      and `--triage-*` help strings to mention the file flag.
      (Requirement: Interactive apply reads the verdict from a file.)
      In `tests/router/test_skill_dispatch.py`, alongside the existing
      `test_apply_brief_triage_*` tests, add one test per spec scenario: confirm from file
      writes the keep note and matches the inline entry; preview from file leaves the brief
      unchanged; missing path yields `status: error` naming the path and exit 1; a file
      containing `null` yields the inline form's null error; passing both flags exits with
      argparse's usage error.
      files: src/worktrail/router/skill_dispatch.py, tests/router/test_skill_dispatch.py

- [ ] 1.2 In `skills/worktrail-go/SKILL.md`, Phase 2 intake-brief triage gate: in step 1
      replace the `VERDICT_JSON=$(...)` capture with a redirect of the evaluator's stdout to
      `VERDICT_FILE="${TMPDIR:-/tmp}/worktrail-triage-verdict-${BRIEF_ID}.json"` (set in the
      same block), followed by printing the exit code and the file so the exit-2 / exit-1
      branches can be judged from that one call's output; restate those two branches in terms
      of the file printing `null`. In step 2 replace `--apply-brief-triage "$VERDICT_JSON"`
      with `--apply-brief-triage-file "$VERDICT_FILE"` (still with `--confirm`), and state
      that the path is re-derived from the brief id, never the JSON re-typed.
      (Requirement: Interactive apply reads the verdict from a file.)
      In `tests/router/test_skill_prose_enforcement_coverage.py`, extend
      `TestPhase2IntakeGateNoConfirmationPrompt` with a test for the "Gate carries only the
      verdict path between steps" scenario: the gate text contains
      `--apply-brief-triage-file` and a stdout redirect in the evaluate block, and contains
      neither `VERDICT_JSON=$(` nor `--apply-brief-triage "$VERDICT_JSON"`; point
      `test_apply_call_passes_confirm` at the file flag.
      files: skills/worktrail-go/SKILL.md, tests/router/test_skill_prose_enforcement_coverage.py

## 2. Verification

- [ ] 2.1 [depends: 1.1, 1.2] [e2e] Run `PYTHONPATH=src pytest -q tests/router/test_skill_dispatch.py
      tests/router/test_skill_prose_enforcement_coverage.py tests/test_plugin_surface.py`,
      then `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate apply-brief-triage-verdict-from-file --strict` and
      `worktrail-compile openspec/changes/apply-brief-triage-verdict-from-file`.
