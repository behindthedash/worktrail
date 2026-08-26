## 1. Layer 1 — convention rewording (agent-doctrine)

- [ ] 1.1 Edit `~/projects/devops/agent-doctrine/AGENTS.md`, "End-of-Session Next-Step
      Suggestion" section: replace the unconditional-capture sentence with the gated wording —
      auto-capture the single strongest idea as a Worktrail handoff brief ONLY when no durable
      artifact already tracks the follow-up; durable artifacts are: a spec/OpenSpec change created
      or merged this session, an epic doc, an existing queue brief, or an open PR; otherwise emit
      a suggestion-only line naming the resume command (e.g. `worktrail-go <brief-id>` or the
      route command). Keep wording provider-portable; commit in agent-doctrine's own flow.
- [ ] 1.2 Verify the reworded section still reads correctly for a provider with no Worktrail
      install (no hook, no CLI): the suggestion-only fallback must stand alone.

## 2. Layer 2 — Stop hook mechanical dedup check

- [x] 2.1 Session-Touched Durable-Artifact Detection: extend `scan_transcript` in
      `hooks/suggest_next_step.py` to also collect touched
      `docs/specs/**` / `openspec/changes/**` paths from edit-tool `file_path`s and Bash
      write markers, in the same single pass (new regex collector alongside
      `RUN_RECORD_PATH_RE`); update `hooks/test_suggest_next_step.py`.
- [x] 2.2 Planned-Run-Record Detection and Merged Docs-Only Spec PR Detection Is
      Transcript-Local: add `src/worktrail/router/check_durable_artifact_capture_gate.py`,
      a fail-open checker accepting repeated `--touched-path` and `--run-record`, returning
      JSON hits of three kinds (session-touched durable artifact; run record finishing
      `planned_ready_for_implementation` via `run_record._load_lenient`; merged docs-only
      spec PR = transcript merge marker + touched spec paths), plus
      `worktrail-check-durable-artifact-capture-gate` entry point in
      `pyproject.toml [project.scripts]`.
- [x] 2.3 Add pytest coverage for the checker (`tests/router/`) mirroring
      `tests/router/test_check_deferred_work_handoff.py`: hit kinds, miss cases, malformed/unreadable
      inputs degrading to zero hits.
- [ ] 2.4 Downgrade-To-Suggestion On Dedup Hit and Fail-Open And Headless-Excluded: wire the
      hook to call the checker via subprocess (5 s timeout, fail-open on missing
      binary/nonzero exit/bad JSON); on hits append a DEDUP GATE block to `reason` that names the
      artifacts, forbids auto-capture, requires a suggestion-only line naming the resume command,
      and states the explicit-justification escape hatch (justification recorded inside the brief
      text); keep sentinel and headless behavior unchanged.
- [x] 2.5 Hook tests: no-hit output byte-for-byte identical to pre-gate instruction; hit case
      emits the gate block naming the artifact; binary-missing fails open; `CC_HEADLESS=1`
      unaffected.

## 3. Layer 3 — capture-time overlap warning

- [x] 3.1 In `src/worktrail/workqueue/create_handoff.py`, before writing the brief: resolve the
      repo path already computed for frontmatter; scan `<repo>/docs/specs/*/` slugs,
      `<repo>/openspec/changes/*/` names, and open PR titles (`gh pr list --repo <remote> --state
      open --json title,number`, short timeout, silently skipped when unavailable) against focus
      tokens using `cluster_detect`'s tokenization/OVERLAP_THRESHOLD (imported, not duplicated).
- [ ] 3.2 Emit warnings without blocking: top-5 candidates into `"overlap_warnings"` in the JSON
      result and to stderr in human mode; every failure mode (no gh, null remote, unreadable repo)
      leaves capture succeeding with a zero exit status.
- [x] 3.3 Extend `tests/workqueue/test_create_handoff.py`: spec-slug overlap warns, open-PR
      overlap warns (gh stubbed), below-threshold stays silent, failure modes stay silent, brief
      always written.

## 4. Verification and housekeeping

- [ ] 4.1 Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`; both green.
- [ ] 4.2 Apply the `go:no-version-bump` label to the PR (or bump pyproject/.codex-plugin versions if this ships standalone).
- [ ] 4.3 Confirm end-to-end story: reworded convention (layer 1) matches the hook's downgraded
      instruction vocabulary (layer 2) and the capture warning text (layer 3) — same
      durable-artifact list, same resume-command guidance.
