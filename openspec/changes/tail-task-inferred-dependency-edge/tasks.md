## 1. Complete the tail-kind baseline in the OpenSpec task source

- [ ] 1.1 Rework `load()` in `src/worktrail/taskformats/openspec/source.py` into a two-phase
      pass: walk the parsed tasks once to collect the id of every non-tail task in the change,
      then assign each tail-kind (`e2e`/`cleanup`) task `deps` as the sorted union of its
      within-group predecessor (when one exists) and that whole non-tail id set -- not the
      prefix the single forward pass accumulates today. Leave the non-tail branch exactly as it
      is (nearest preceding non-tail sibling within the group), leave the authored-`depends:`
      union on top of both branches, and rewrite the docstring's "every preceding non-tail
      task" rationale to state the complete-coverage rule and why it is the tail phase's own
      semantics. In `tests/taskformats/openspec/test_openspec_source.py`, replace
      `test_tail_depends_on_every_preceding_non_tail_task` with a case asserting the complete
      set, and add the incident-shaped regression: a changeset whose `[e2e]` task sits in group
      1 and whose acceptance names a script produced by a task in group 2 loads with that
      group-2 id in its `deps`. Add a case asserting the unchanged non-tail behavior (two
      non-tail tasks in different groups keep no edge to each other) and one asserting a
      tail-kind task that is first in its group picks up the group's following non-tail ids
      without those tasks gaining a back-edge, so the group cannot deadlock.
      (Requirements: Tail-kind baseline dependencies cover every non-tail task in the change;
      Non-tail baseline dependencies are unchanged; Tail dependency inference stays additive
      and deterministic)
      files: src/worktrail/taskformats/openspec/source.py tests/taskformats/openspec/test_openspec_source.py

## 2. Apply the same baseline to the Spec Kit task source

- [ ] 2.1 Make the same two-phase correction in `load()` of
      `src/worktrail/taskformats/speckit/source.py`, whose loop has the identical
      prefix-only `non_tail_ids` accumulator and therefore the identical silent miss. Its
      tail branch already unions the group predecessor unconditionally, so only the id set
      changes from prefix to complete. In `tests/taskformats/speckit/test_speckit_source.py`,
      add a regression with an `[e2e]`/`[cleanup]`-tagged task that precedes a non-tail task
      in a later group and assert the later task's id is in its `deps`, plus a case that the
      existing cross-group independence of non-tail tasks is unchanged.
      (Requirements: Tail-kind baseline dependencies cover every non-tail task in the change;
      Non-tail baseline dependencies are unchanged)
      files: src/worktrail/taskformats/speckit/source.py tests/taskformats/speckit/test_speckit_source.py

## 3. Prove the edge reaches the compiled plan

- [ ] 3.1 Add a compile-boundary regression in `tests/conductor/test_compile.py`: a fixture
      OpenSpec change whose `[e2e]` task is in an earlier group than the non-tail task that
      produces the artifact its acceptance names. Compile it (the existing fixture and
      `RecordingSpawn` harness in that file), run the result through
      `runplan.apply_to_tasks`, and assert the tail task's merged `deps` retain the later
      group's task id -- the tail task declares no files, so `runplan`'s restore branch must
      keep the edge rather than let the plan drop it. This is the assertion that the edge the
      tail dispatch gate reads survives compilation, which the task-source test alone does
      not cover.
      (Requirement: Tail-kind baseline dependencies cover every non-tail task in the change)
      files: tests/conductor/test_compile.py
      depends: 1.1

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q
      tests/taskformats/openspec/test_openspec_source.py tests/taskformats/speckit
      tests/conductor/test_compile.py`, then the full `PYTHONPATH=src python3.14 -m pytest -q`
      and `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`, then
      `python3.14 scripts/ci/ruff_pinned.py check .`, `python3.14 scripts/ci/ruff_pinned.py
      format --check .` and `python3.14 scripts/ci/check_shebang_exec_bits.py`. Confirm the new
      regressions fail against the pre-fix `load()` (state which assertion fails and on what)
      and pass with it. Run `openspec validate tail-task-inferred-dependency-edge --strict` and
      `worktrail-compile openspec/changes/tail-task-inferred-dependency-edge`, and confirm on a
      scratch copy of the incident's change shape that the compiled plan's tail task now lists
      the later group's producer where it previously listed only the earlier tasks.
      depends: 1.1, 2.1, 3.1
