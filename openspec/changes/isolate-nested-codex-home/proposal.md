## Why

Nested Codex launches currently retain a writable inherited `CODEX_HOME`. When
ChatGPT authentication inheritance is enabled, that makes the parent and child
homes identical and the defensive auth-link guard stops dispatch before the
child starts. The guard correctly protects the parent credential, but automatic
launches need a distinct child home so ordinary skill and orchestrator dispatch
can proceed safely.

## What Changes

- Make automatic Codex child-home selection isolate the child from an inherited
  `CODEX_HOME` even when the inherited path is writable; the inherited home
  remains the authentication source, not the child process home.
- Keep an explicit `--codex-home` or `WORKTRAIL_CODEX_HOME` override as the
  caller-selected, fail-closed home.
- Add regressions for the skill-dispatch and direct orchestrator-worker paths
  so both prove that their prepared child environment uses an isolated home and
  can inherit authentication without the identical-home failure.

## Capabilities

### New Capabilities

- `nested-codex-home-isolation`: automatic Codex child dispatch uses a
  distinct writable Worktrail home while preserving safe parent-session
  inheritance for nested skill and orchestrator launches.

### Modified Capabilities

- None.

## Impact

- `src/worktrail/router/skill_dispatch.py`: child-home selection and its
  shared environment-preparation contract.
- `tests/router/test_skill_dispatch.py` and
  `tests/orchestrator/test_spawnlib.py`: automatic isolation and direct-worker
  regressions.
- No new CLI flag, dependency, or credential-copy behavior; explicit home
  selection remains available.
