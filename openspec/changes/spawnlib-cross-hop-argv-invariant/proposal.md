## Why

PR #701 fixed a real argv-leak defect (a capacity-gated claude primary handed its
claude-only `extra_args` — `--strict-mcp-config`, `--tools`, `--setting-sources` — to the
codex/opencode CLI, producing an immediate usage failure) and closed it with one
scenario-shaped test, `test_preflight_fallback_drops_primary_agent_extra_args`, which covers
exactly one spawn path: the persisted-capacity preflight hop. The other build_cmd call site
inside `spawn_agent` — the session-limit fallback hop rebuild (spawnlib.py:924) — is guarded
only by `SessionLimitFallback.test_switches_to_configured_fallback_without_sleeping`
asserting a single token (`--append-system-prompt`) on a single fallback pair. Nothing fails
if a future edit reintroduces the leak class on any hop/pair/token combination the two tests
don't happen to enumerate — the same "tests pass while allowing the leak" gap PR #701's own
research doc called out in the pre-#701 coverage.

## What Changes

- Add a property-style argv invariant test for `spawnlib.spawn_agent`: sweep every spawn path
  that builds a command — the capacity-gate first-hop selection and every session-limit
  fallback hop — across all non-claude agents (codex, opencode), asserting that **no**
  claude-only flag token ever appears in any captured codex/opencode argv.
- Ground the forbidden-token set in the flags this repo actually derives for claude workers:
  live.py's `_LEAN_WORKER_FLAGS` tokens (`--strict-mcp-config`, `--tools`,
  `--setting-sources`) plus the other claude-only tokens callers pass as `extra_args`
  (`--append-system-prompt`, `--permission-mode`/`bypassPermissions`,
  `--output-format`/`stream-json`/`--verbose`, `--effort`, `--resume`/`--fork-session`) and
  the structural claude-branch flags `build_cmd` itself adds.
- Cover both fallback mechanisms symmetrically: gate-based first-hop selection (claude gated →
  each of codex/opencode selected before any launch) and session-limit mid-run hops (claude
  hits the limit → each of codex/opencode; plus a multi-hop chain claude→codex→opencode so
  second hops are swept too), while also asserting the ungated claude primary still receives
  its extra_args unchanged.
- Test-only change: no production code under `src/worktrail/` is modified.

## Capabilities

No specs apply. The argv-isolation behavior being pinned already exists (PR #701 established
it as code behavior; the session-limit hop has carried `extra_args=None` since before it),
and this change adds no new or modified runtime behavior — it widens regression coverage for
an invariant that lives entirely inside `spawn_agent`. As with the other test-only changes in
this repo's history, `skip_specs: true` is set in `.openspec.yaml`.

### New Capabilities

- _none_ — test-only coverage hardening; no spec-level behavior changes.

### Modified Capabilities

- _none_

## Impact

- `tests/orchestrator/test_agent_capacity.py` (or a sibling spawnlib test module): new
  invariant test(s); existing tests untouched.
- No changes to `src/worktrail/orchestrator/spawnlib.py`, `live.py`, or any caller; no API,
  dependency, or plugin-surface impact. Keeps `PYTHONPATH=src pytest -q` green by
  construction (the change only adds passing assertions against current behavior).
