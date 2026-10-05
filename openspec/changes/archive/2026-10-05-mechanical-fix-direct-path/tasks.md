## 1. Direct-mode gate + pipeline documentation

- [ ] 1.1 Add `src/worktrail/router/modify_direct_gate.py` (stdlib-only, read-only,
      deterministic — no model, no writes, no network), the code-enforced eligibility
      predicate for direct mode:
      (a) Module-level named constants in one place — `MAX_TASKS = 1`, `MAX_FILES = 3`,
      `MAX_ESTIMATED_CHANGED_LINES = 40`, `ROUTING_SURFACE` (exact paths
      `src/worktrail/router/{classify,classify_handoff,risk_judgment,policy,routing_cli}.py`
      plus the directory prefixes `src/worktrail/router/cassettes/` and `scripts/cassettes/`
      — the legacy cassette path `routes.md` §J names, kept defensively), and the stable
      reason tokens `change_shape`, `task_count`, `files_undeclared`, `files_over_cap`,
      `routing_surface`, `delta_over_cap`, `eligible`. No policy keys, no env overrides,
      no flags beyond `--json`.
      (b) Parse the change's `tasks.md` through
      `worktrail.taskformats.openspec.schema.parse_tasks_md` (the parser compile already
      trusts); declared files are the sorted union of the tasks' `files:` lists.
      (c) Evaluate in this fixed order, first failure deciding the reason:
      **change_shape** (no `tasks.md` or no `specs/**/spec.md` in the change dir) →
      **task_count** (must be exactly `MAX_TASKS`) → **files_undeclared** (the single task
      declares no file) → **files_over_cap** (distinct declared files > `MAX_FILES`) →
      **routing_surface** (any declared file equals or lies under a `ROUTING_SURFACE`
      entry) → **delta_over_cap** (estimated changed lines > `MAX_ESTIMATED_CHANGED_LINES`).
      (d) Estimate changed lines as the sum, over the change's `specs/**/spec.md` files, of
      non-blank stripped lines absent from the corresponding base spec
      `<openspec-root>/specs/<capability>/spec.md`, where the openspec root is the nearest
      ancestor of the change directory containing a `specs/` directory (resolves both
      `changes/<id>` and `changes/archive/<id>`); a missing base spec counts every delta
      line as new. Delta-format scaffolding (e.g. the `## MODIFIED Requirements` heading)
      counts as new and is deliberately not special-cased.
      (e) `main(argv)` implements `worktrail-modify-direct-gate <change-dir> [--json]`:
      `--json` prints exactly `{"eligible": bool, "reason": str, "task_count": int,
      "files": [...]}` where `reason` begins with the verdict's token (`eligible: …` or
      `<failing_token>: <observed value vs cap>`) and `files` is the sorted declared list;
      the human mode prints one line with the same facts. Exit 0 whenever a verdict is
      computed (eligible OR ineligible — a nonzero exit never means ineligible), 2 on a
      usage error (missing/non-directory argument) with a stderr diagnostic and no verdict
      object. Register
      `worktrail-modify-direct-gate = "worktrail.router.modify_direct_gate:main"` in
      `pyproject.toml`'s `[project.scripts]` alongside the other `worktrail.router.*`
      entries.
      (Requirements: Direct-mode eligibility gate is an executable verdict; Eligibility
      conditions are capped by named constants)
      Add `tests/router/test_modify_direct_gate.py` (hermetic `tmp_path` fixtures) covering:
      the eligible happy path (1 task, 2 declared files, small delta vs a fixture base spec)
      asserting the exact four-key JSON shape, `task_count == 1`, sorted `files`, exit 0,
      and the `eligible` token; each ineligible token with its own fixture and reason
      substring — 2 tasks (`task_count`), 4 declared files (`files_over_cap`), no `files:`
      line (`files_undeclared`), a delta spec far larger than the cap (`delta_over_cap`),
      a declared `src/worktrail/router/cassettes/routing_cassette.json` and a declared
      `classify.py` (`routing_surface`, naming the file), missing `tasks.md` and missing
      `specs/` dir (`change_shape`); a declared near-miss
      `src/worktrail/router/run_record.py` staying eligible (the motivating specimen's
      file — the surface is named files, not the whole `router/` package); the new-to-base
      measurement (a >cap-long delta spec that restates base lines verbatim stays eligible
      when its new-to-base count is within the cap, and a missing base spec counts every
      line); usage error (nonexistent dir → exit 2, no verdict JSON). Prefer calling
      `main()` directly for verdict assertions plus one subprocess-level exit-code check.
      Run `pytest tests/router/test_modify_direct_gate.py` and
      `pytest tests/test_plugin_surface.py`.
      files: pyproject.toml src/worktrail/router/modify_direct_gate.py tests/router/test_modify_direct_gate.py

- [ ] 1.2 Document the direct branch in the modify pipeline:
      (a) In `skills/worktrail-sdd-workflow/references/pipeline-details.md#modify-pipeline`,
      insert a new numbered step immediately before the existing scope-check/compile step:
      invoke `worktrail-modify-direct-gate "$CHANGE_DIR" --json` and branch **only** on its
      `eligible` field — the gate's verdict is the decision; nothing in the prose may ask
      the agent to judge eligibility by reading the change. Renumber the following steps
      and update every in-section reference to them (the scope-check prose's "step 3"
      mentions, the uncommitted-output guard's "from step 3", etc.).
      (b) Eligible → the direct branch, documented as: append
      `worktrail-run-record append "$RUN" decisions "modify-direct-mode: direct — <the gate's reason>"`,
      then implement the change's single task inline in the change worktree (`$WT` on
      `chg/$CHANGE_ID` — never the base checkout, never a task worktree), commit there; run
      the retained gates explicitly listed — compile (`worktrail-compile "$CHANGE_DIR"`,
      committing the resulting `.compile-ok`), `openspec validate <change-id>`, the
      pre-PR gate (`worktrail-pre-pr-gate --run …`, same invocation and label computation
      the existing landing blocks use), and CI — then land exactly ONE PR via
      `worktrail-land-pr` carrying the change artifacts + implementation, in checkpoint
      mode so the run record stays open (mirroring the sync PR's checkpoint usage in
      `subagent-prompts.md#sync-before-teardown`; verify flags against `land_pr.py`'s
      CLI). No orchestrator launch, no worker spawn, no task worktree. Then continue to the
      sync step and teardown exactly as the orchestrated path, and finish per the route's
      normal completion. State the accepted trade-off (no orchestrator reviewer, no
      worktree isolation — the trust level `routes.md` §F already extends to direct
      fix-branch worktrees) and that any change touching the routing/classification
      surface can never ride direct mode.
      (c) Ineligible → record
      `worktrail-run-record append "$RUN" decisions "modify-direct-mode: orchestrated — <the gate's reason>"`
      before the orchestrator launch, surface the reason to the operator, and proceed
      through the existing orchestrated steps unchanged — the fallback is never silent.
      (d) In `skills/worktrail-sdd-workflow/SKILL.md`, add the direct-mode mention to the
      F/G route-table rows (and any one-line pipeline summary that would otherwise
      contradict it). In `skills/worktrail-go/references/routes.md`, replace §F step 5's
      "(single-worker orchestrate for 1-task fixes)" parenthetical with the direct-or-
      orchestrated branch description and make §G step 3's pipeline pointer name the same
      branch.
      (Requirements: The modify pipeline branches on the gate's verdict; The chosen mode
      is recorded on the run record; Direct mode relaxes no existing gate)
      Run `pytest tests/test_plugin_surface.py` (the new command token must resolve to the
      task-1.1 entry point — docs must not land before it; this same test also resolves
      every cross-skill `{#anchor}` citation these docs use, so any anchor edits are
      covered by the same run).
      files: skills/worktrail-sdd-workflow/SKILL.md skills/worktrail-sdd-workflow/references/pipeline-details.md skills/worktrail-go/references/routes.md

- [ ] 1.3 [e2e] Verification — run and record: `PYTHONPATH=src python3.14 -m pytest -q`
      (full suite, including `tests/router/test_modify_direct_gate.py`,
      `tests/router/test_classify.py`, and `tests/test_plugin_surface.py`),
      `python3.14 -m worktrail.orchestrator.orchestrate check` (golden regression),
      `python3.14 scripts/ci/ruff_pinned.py check .`,
      `python3.14 scripts/ci/ruff_pinned.py format --check .`,
      `python3.14 scripts/ci/check_shebang_exec_bits.py`, and
      `openspec validate mechanical-fix-direct-path --strict`. Then run the gate CLI
      end-to-end against the motivating specimen's real archived change directory
      (`openspec/changes/archive/2026-10-04-active-conflicts-staleness-reconciliation-missing-root-fail-loud`)
      and confirm the verdict shape: `eligible: true`, `task_count` 1, `files` the two
      declared paths (`src/worktrail/router/run_record.py`,
      `tests/router/test_run_record.py`), exit 0 — a live check that the gate's surface,
      caps, and parser accept the exact change class it was built for.
      (Requirements: Direct-mode eligibility gate is an executable verdict; Eligibility
      conditions are capped by named constants)
