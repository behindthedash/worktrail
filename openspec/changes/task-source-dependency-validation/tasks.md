## 1. Protocol

- [x] 1.1 Add `validate_dependencies(self, spec_id: str, tasks: List[TaskDict]) -> List[str]` to the `TaskSource` Protocol in `src/worktrail/taskformats/base.py`, documented per the existing method docstring style (see `resolve_external_dependency`).

## 2. Devkit implementation

- [x] 2.1 In `src/worktrail/taskformats/devkit/source.py`, implement `validate_dependencies`: for each task, check every `deps` entry against the loaded task-id set and append an "unresolved same-spec dependency" diagnostic string for each miss.
- [x] 2.2 In the same implementation, for each task's `decision-refs:` frontmatter entry (list of decision ids, default `[]`), locate `decision-log.md` alongside the spec's tasks and parse `## D<n>: <title>` headings with a following `Status:` line; report a "missing decision" diagnostic when the id is absent from the log, and an "open decision" diagnostic (including the found status) when present but not `decided`/`resolved` (case-insensitive).
- [x] 2.3 When `decision-log.md` does not exist for the spec but a task declares `decision-refs:`, treat every referenced id as missing (same diagnostic as 2.2's missing-decision case) rather than raising or silently skipping.

## 3. OpenSpec implementation

- [x] 3.1 In `src/worktrail/taskformats/openspec/source.py`, implement `validate_dependencies`: same-spec `deps` check identical in behavior to devkit's (2.1).
- [x] 3.2 If any task carries a `decision-refs:` entry (parsed from the OpenSpec task's own metadata, if the format exposes it) report the "decision-refs unsupported for OpenSpec-format tasks" diagnostic per task instead of validating it.

## 4. Precheck wiring

- [x] 4.1 In `src/worktrail/orchestrator/live.py`'s `precheck()`, call the loaded `TaskSource`'s `validate_dependencies(spec_id, tasks)` once per run, print one `WARN:` line per returned diagnostic (matching the existing external-dependency WARN format), and include each in the function's `warn_count` so the existing non-zero-exit contract covers these findings too.

## 5. Tests

- [x] 5.1 Add devkit fixtures/tests (`tests/taskformats/` or the nearest existing devkit test module) covering: a resolving same-spec dependency (no diagnostic), an unresolved same-spec dependency, and an externally-satisfied dependency declared via `external-dependencies:` that must NOT be flagged by the same-spec check.
- [x] 5.2 Add devkit fixtures/tests covering `decision-refs:`: a decided decision (no diagnostic), a missing decision id, an open/undecided decision, and a spec with `decision-refs:` but no `decision-log.md` file at all.
- [x] 5.3 Add an OpenSpec fixture/test covering the same-spec `deps` check and the "decision-refs unsupported" diagnostic.
- [x] 5.4 Add or extend a `live.py` precheck test asserting the new WARN lines appear and `precheck()`'s exit code goes non-zero when a same-spec-dependency or decision-log diagnostic is present, and stays clean (per existing behavior) when none is.

## 6. Verification

- [x] 6.1 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` locally; both must pass before the PR is opened.
