## 1. The gate section and its callers

- [ ] 1.1 In `skills/worktrail-go/references/subagent-prompts.md`, add
      `### Compile gate {#compile-gate}` under `## Orchestrator pre-launch gates
      {#orchestrator-gates}` (after `#precheck-gate`, so the section order matches the order the
      gates run in), carrying the failure-class → recovery table from design D1 -- plan-shape
      rejection, scope gaps, unordered file collisions, uncovered requirements, bad spec path /
      not a git repo, refused `--force` over active worktrees, and the exit-0 degraded plan --
      with the D1 split between "a defect in the change" and "a defect in the invocation", and
      the D2 rule stated once: an unchanged re-run cannot resolve a plan-shape or coverage
      rejection, so retry is never the answer to those. Add the in-section `$AUTO_MODE=true`
      branch (D4: block `blocked_product_decision` quoting the compile output, brief stays
      claimed; the exit-0 degrade takes its safe default and is recorded via
      `worktrail-run-record append`), and a matching per-site bullet in the
      `## Auto-mode ask fallbacks (route execution) {#auto-mode-ask-fallbacks}` list beside the
      `#precheck-gate` one. In the same file's `## Orchestrator invocation {#orchestrator}`
      code block, replace the bare `echo "ERROR: worktrail-compile failed ... inspect the error
      above before retrying full-real."` branch (~line 750) with one citing `#compile-gate`,
      and add the exit-0 check for a degraded-plan `note:` line that nothing reads today
      (Requirements: The compile step is a documented pre-launch gate, Compile failure recovery
      names an action per failure class, A compile failure has an unattended fallback).
      files: skills/worktrail-go/references/subagent-prompts.md

- [ ] 1.2 In `skills/worktrail-sdd-workflow/references/pipeline-details.md`, point both
      scope-check steps at the new anchor: the `new` pipeline's step 3 (~lines 34-47) and the
      `implement` pipeline's step 3 (~lines 193-208). Each currently carries its own one-line
      `echo "ERROR: worktrail-compile found scope gaps ..."` naming only the scope-gap remedy,
      which is wrong for a plan-shape or coverage rejection; replace the trailing guidance with
      a citation of `../../worktrail-go/references/subagent-prompts.md#compile-gate`, in the
      same cross-reference style those steps already use for
      `#already-implemented-check`/`#precheck-gate`. Keep each step's existing exit behaviour
      (stop before pushing / before continuing) unchanged
      (Requirements: The compile step is a documented pre-launch gate).
      files: skills/worktrail-sdd-workflow/references/pipeline-details.md

## 2. Enforcement

- [ ] 2.1 In `tests/test_plugin_surface.py`, add
      `test_compile_gate_documents_failure_recovery` beside
      `test_route_execution_ask_sites_carry_auto_mode_fallbacks`, reusing that module's
      `_h2_sections` helper (design D5). Assert: `{#compile-gate}` exists and its heading falls
      inside the `#orchestrator-gates` h2 section, not elsewhere in the file; the section body
      names every D1 failure class (plan shape, no file scope, unordered collision, uncovered
      requirement, spec path / git repo, `--force` over active worktrees, and the exit-0
      degraded-plan `note:`); the section carries an `$AUTO_MODE` branch with
      `blocked_product_decision` and leaves the brief in `picked/`; `#auto-mode-ask-fallbacks`
      contains a `#compile-gate` bullet; the phrase `inspect the error above before retrying`
      appears nowhere under `skills/`; and every site running `worktrail-compile` in
      `subagent-prompts.md` and `pipeline-details.md` cites `#compile-gate` in the same section.
      depends on 1.1, 1.2
      (Requirements: The compile gate documentation is enforced).
      files: tests/test_plugin_surface.py

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src pytest -q` and `openspec validate
      compile-failure-recovery-procedure --strict`, and confirm both pass; depends on 2.1.
      Verification-only, no file changes expected.
