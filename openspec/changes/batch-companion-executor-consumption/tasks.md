## 1. Seed mapper carries the claimed batch

- [ ] 1.1 In `src/worktrail/router/handoff_seed.py`, add the batch pass. Factor the per-brief
      mapping `build_seed()` already performs into one helper both the primary and each
      companion call, then read the primary's optional `batch:` frontmatter list (the companion
      stems `claim-batch` recorded; tolerate an absent key, an empty list, and a bare string
      the way `suggested-skills` is already tolerated) and emit a `batch` key on the seed
      output: one member per stem, in declared order, each `{"id": <stem>, "path": <resolved
      file>, "focus", "repo", "feature_idea", "constraints", "error"}`. Resolve a stem to the
      sibling brief file beside the primary (`Path(path).parent / f"{stem}.md"`) -- the module
      follows only links the primary itself declares and must NOT list, move, or stamp the
      queue (keep `work_queue.py` the lifecycle owner). A stem with no readable sibling file
      becomes a member whose `error` names the unresolved stem/path (the existing `parse_brief`
      error path), never aborting the seed or disturbing the other members and the top-level
      fields; `"batch": []` for a brief with no list, leaving every other output field exactly
      as before. Carry the new row in the docstring's canonical field-mapping table and output
      shape, and verify the CLI's text mode and exit-code contract are unchanged.
      In `tests/router/test_handoff_seed.py`, cover: a primary carrying `batch: [comp-a,
      comp-b]` with both siblings present emits both members in order, each with its own
      `focus`/`feature_idea`/`constraints`/`repo` and `error: None`; a brief with no `batch:`
      field (and one with an empty list) emits `[]` with an otherwise unchanged seed; a stem
      whose sibling file is absent yields an error member naming it while the other members and
      the top-level fields are intact and the CLI still exits 0; a companion with a null `repo`
      keeps null; and the batch pass modifies no file on disk (content and mtimes unchanged).
      (Requirements: The seed carries the claimed batch's companions)
      files: src/worktrail/router/handoff_seed.py, tests/router/test_handoff_seed.py

- [ ] 1.2 In `tests/router/test_handoff_seed_e2e.py`, add the claim-to-seed end-to-end case
      through the real CLIs in a temporary `$WORK_QUEUE_DIR`: write a primary and a companion
      into `queue/`, claim both with `worktrail-work-queue claim-batch <primary> <companion>
      --by <id> --json`, then run `worktrail-handoff-seed seed <primary-picked-path> --json`
      and assert the emitted `batch` member names the companion's stem, carries the
      companion's focus, and has a null `error`. Add a second case that releases the companion
      after the batch claim (the claim's primary-side `batch:` list keeps the stem by design)
      and asserts the seed reports that member with an `error` naming it instead of failing.
      (Requirement: The seed carries the claimed batch's companions)
      files: tests/router/test_handoff_seed_e2e.py
      depends: 1.1

## 2. Executor consumes the union

- [ ] 2.1 In `skills/worktrail-go/references/subagent-prompts.md`, extend `#handoff-seed`.
      Step 4: document the seed's `batch` members and name the primary's own `batch:`
      frontmatter as their source; then require the flow to load every member together with
      the primary and treat the union as ONE request -- each consumed companion's
      focus/suggested-approach/constraints folded into the request labeled by its brief id,
      one classification fed the primary's route evidence, one run record, one worktree/PR.
      State the exclusion rule: a member carrying a seed `error` is reported by id and left
      alone (not consumed, not closed); a member whose `repo` differs from the primary's
      resolved repo, or whose `recommended_route` hint names a route other than the run's
      resolved route, is released back to the queue (`worktrail-work-queue release <id> --by
      "$GO_DISPATCH_ID"`, omitting `--by` only when no dispatch id is held) and reported
      rather than forced in; an empty `batch` keeps the unchanged single-brief path; carry the
      consumed ids as `$HANDOFF_CONSUMED_IDS`. Step 7: before any closure, record the consumed
      set with `worktrail-run-record set-list "$RUN" handoffs_consumed <ids...>` (never `set`,
      which stores a JSON string as a scalar -- the 20261003-204455 incident), then close EACH
      consumed brief individually with its own `worktrail-work-queue done <id>
      --implementation-complete --run "$RUN" --by "$GO_DISPATCH_ID"` (or `--planning-only`
      when the run explicitly stopped at planning), and release -- never mark done -- a
      consumed companion whose scope did not actually land.
      (Requirements: The handoff-seed flow consumes the batch as one request; The run record
      names every brief the run consumed; Each consumed brief is closed individually)
      files: skills/worktrail-go/references/subagent-prompts.md

## 3. References name the consumption path

- [ ] 3.1 In `skills/worktrail-go/references/batch-consumption.md` step 4, name the
      consumption path beside the existing promise: the claimed batch is read back from the
      primary's own `batch:` frontmatter by `worktrail-handoff-seed`, the executor's
      `#handoff-seed` flow folds the members into the one request, a member that cannot ride
      the run is released and reported rather than forced in, and the executor writes the
      consumed ids into the run record's `handoffs_consumed`. In
      `skills/worktrail-go/references/auto-mode.md` step 5, change the record line from "every
      claimed brief id" to every brief id the run consumed, written by the executor, so the
      claimed set and the consumed set are not conflated; leave both references' promise
      otherwise intact (one classification, one run record, one worktree/PR; per-brief
      `done`/`release`).
      (Requirement: Operator references name the executor batch path)
      files: skills/worktrail-go/references/batch-consumption.md, skills/worktrail-go/references/auto-mode.md

## 4. Lockstep guard

- [ ] 4.1 In `tests/test_plugin_surface.py`, add one guard test asserting the three ends of
      the batch seam keep naming each other: the `#handoff-seed` procedure in
      `skills/worktrail-go/references/subagent-prompts.md` (the text between the
      `## Handoff seed {#handoff-seed}` anchor and `### Invariants`) names the `batch:`
      frontmatter and the `worktrail-run-record set-list` write; the batch-consumption
      reference's union step names `worktrail-handoff-seed`, `#handoff-seed`, and
      `handoffs_consumed`; and `src/worktrail/router/handoff_seed.py`'s canonical field table
      documents the `batch` key it emits. Fail with the file and the missing token so a rename
      on one end is caught at build time, not by a companion sitting unpicked.
      (Requirement: Operator references name the executor batch path)
      files: tests/test_plugin_surface.py
      depends: 1.1, 2.1

## 5. Verification

- [ ] 5.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q
      tests/router/test_handoff_seed.py tests/router/test_handoff_seed_e2e.py
      tests/test_plugin_surface.py`, then `PYTHONPATH=src python3.14 -m pytest -q` and
      `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`, then
      `python3.14 scripts/ci/ruff_pinned.py check .`, `python3.14 scripts/ci/ruff_pinned.py
      format --check .`, and `python3.14 scripts/ci/check_shebang_exec_bits.py`. Probe by
      hand in a scratch queue: `claim-batch` a primary plus one companion, seed the claimed
      primary, and confirm the companion's scope is present in the seed's `batch` member; then
      release the companion and confirm the next seed reports it as an error member while the
      primary's own seed fields are unchanged. Confirm the single-brief path is untouched (a
      plain `claim` + `seed` emits `"batch": []` and the same fields as before). Run
      `openspec validate batch-companion-executor-consumption --strict` and
      `worktrail-compile openspec/changes/batch-companion-executor-consumption`.
      depends: 1.1, 1.2, 2.1, 3.1, 4.1
