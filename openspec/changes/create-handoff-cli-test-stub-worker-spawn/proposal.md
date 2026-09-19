## Why

`tests/workqueue/test_create_handoff.py::test_cli_human_mode_reports_overlap_warning_to_stderr_without_blocking`
(line 690) calls `main([... "--repo", str(repo)])` with no patch of the worker-spawn path.
`create_handoff()` reaches `_semantic_slug_summary()`
(`src/worktrail/workqueue/create_handoff.py:46`), which proceeds whenever the resolved routing
has a `default_tier` -- and the suite-wide `tests/conftest.py` fixture deliberately seeds exactly
such a routing file. So the test can spawn a **real headless agent** via `spawn_agent(...)`,
whose stderr lands in the same `capsys` stream the test then asserts is exactly one
`overlap warning:` line. That is the failure mode already documented in the test's own comment
(2026-09-05, runs `go-20260905-182103` feature-2/feature-3: fails under concurrent orchestrator
runs, passes alone), and it failed a required pre-PR gate on an unrelated diff. The same hazard
applies to any sibling test that passes a real `repo=`/`--repo` without stubbing the spawn.

(Work-queue brief `20260918-213358-create-handoff-cli-real-spawn`.)

## What Changes

- A package-scoped autouse fixture in a new `tests/workqueue/conftest.py` stubs
  `worktrail.workqueue.create_handoff.spawn_agent` so no test under `tests/workqueue/` can reach
  a real headless worker by default; the stub degrades to "no semantic summary", the same result
  the production code produces when no backend is usable.
- Tests that deliberately exercise the spawn path keep working: they patch `spawn_agent`
  themselves, and their per-test patch (applied later) wins over the autouse stub.
- A regression test asserts the default isolation holds -- capturing a brief with a real repo
  path never invokes the real spawn -- so the guard cannot be silently removed.
- `tests/workqueue/test_create_handoff.py` is audited: the stale "observed failing under
  concurrent orchestrator smoke runs" comment on the overlap-warning assertion is replaced with a
  statement of what now keeps the stream clean.

## Capabilities

### New Capabilities
- `workqueue-test-spawn-isolation`: work-queue tests never spawn a real headless agent.

### Modified Capabilities

## Impact

- `tests/workqueue/conftest.py` (new), `tests/workqueue/test_create_handoff.py` (comment audit
  plus regression tests).
- No production-code, CLI, or on-disk format change; `_semantic_slug_summary()`'s runtime
  behaviour is untouched.
