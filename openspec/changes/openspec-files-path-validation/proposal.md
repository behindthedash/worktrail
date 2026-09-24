## Why

The OpenSpec checklist parser currently treats every whitespace- or comma-separated word on an
indented `files:` continuation as a file. Prose accidentally placed after `files:` therefore
becomes a non-empty declared scope, which bypasses scope inference and can give a worker a
phantom write surface. Queue-triage confirmed this in `schema.py`; no active change covers
parser-side validation.

## What Changes

- Validate each token in an OpenSpec task's inline `files:` declaration before accepting it as
  declared scope.
- Preserve valid repo-relative path declarations and the existing tolerant parser posture.
- Warn with the task and offending token when a declaration contains prose or another token that
  is not path-like, and exclude that token from the task's declared scope.

## Capabilities

### New Capabilities

### Modified Capabilities

- `openspec-task-file-declaration`: inline file-scope declarations reject non-path-like tokens
  instead of turning prose into declared worker scope.

## Impact

- `src/worktrail/taskformats/openspec/schema.py`: lexical validation of parsed `files:` tokens
  and parser warnings for rejected tokens.
- `tests/taskformats/openspec/test_openspec_schema.py`: coverage for valid paths, prose, and
  mixed declarations.
- No CLI, task syntax, or RunPlan format change; malformed declarations remain warnings rather
  than hard parse failures.
