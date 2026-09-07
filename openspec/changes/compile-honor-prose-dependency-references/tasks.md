## 1. Deterministic Prose-Reference Extraction and Union

- [ ] 1.1 In `src/worktrail/conductor/compile.py`, extract explicit prose dependency references from each task's authored text (the ids named by a "depends on <ids>" sentence in the task title/text as loaded by the task source), and union the resolved ids into the compiled `deps` in both `_plan_from_tasks()` and `_validate()` — additive only, never removing an existing edge, never adding a self-edge — and report a reference identifier matching no task in the change as a compile problem naming the referring task and the unmatched identifier; cover extraction (single id, comma-joined ids, no reference, self-reference), the additive union on both compile paths including a file-disjoint pair, and the unresolvable-reference problem plus the fully-resolvable no-problem case in `tests/conductor/test_compile.py`. (Requirement: Authored prose dependency references become plan edges) (Requirement: Prose dependency references are honored on every compile path) (Requirement: An unresolvable prose dependency reference is reported)

## 2. Prompt Alignment

- [ ] 2.1 Extend `PROMPT` in `src/worktrail/conductor/compile.py` so the dependency instructions tell the model that a task's explicitly stated dependency on another task is a real ordering constraint even when the two tasks share no file, alongside the existing shared-file re-check; pin the new instruction text and prove it survives `.format()` into the sent prompt in `tests/conductor/test_compile.py`; depends on 1.1. (Requirement: The compile prompt asks the model to honor prose dependency references)

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` and confirm both repository gates pass; depends on 2.1.
