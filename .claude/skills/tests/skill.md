---
name: tests
description: Testing conventions for worktrail — hermetic isolation, AST-based enforcement-coverage tests, and cross-module fixture reuse
triggers:
  files:
    - tests/**/*.py
    - conftest.py
  keywords:
    - test
    - pytest
    - unittest
    - fixture
    - hermetic
    - enforcement coverage
    - selfcheck
---

You are working on **worktrail's test suite** (`tests/`, mirrors `src/worktrail/` package layout).

## Domain purpose
This suite must prove the orchestrator's side-effecting paths (git, `gh`, worker spawn, machine-wide config) behave correctly without ever touching the real machine or network — a failing/flaky test here blocks every PR via CI, and a test that silently passes despite a real bug (self-certification) has repeatedly caused production incidents (see enforcement-coverage tests below).

## Business rules / invariants
- **Never touch real machine-wide state.** `tests/conftest.py`'s autouse `_isolate_go_machine_wide_config` fixture redirects `WORKTRAIL_HOME`, `GO_ROUTING_FILE`, `GO_MODEL_DEFAULTS_FILE`, `GO_AGENT_CAPACITY_CACHE`, and `WORK_QUEUE_DIR` into `tmp_path` for every test. A real populated `~/.worktrail`/`~/.go`/`~/work-queue` broke `test_routing_e2e.py` in production once (2026-08-03) — do not add a new machine-wide config path without wiring it into this fixture.
- **Bare `tempfile.mkdtemp()` never leaks into `/tmp`.** `tests/conftest.py`'s session-scoped autouse `_contain_bare_tempfile_calls` fixture points `tempfile.tempdir` and `TMPDIR` (for spawned subprocesses) at a `tempfile/` dir under pytest's basetemp, which pytest prunes itself. The suite has well over a hundred bare `mkdtemp()` calls with no matching `rmtree`; before this fixture (2026-09-02) ~367k leftover `tmp*`/`preflight-*`/`prepr-*`/`spec-collision-*` entries in `/tmp` stalled systemd-tmpfiles at boot and the WSL user session failed to start. Rely on this containment rather than hand-cleaning each call site, but do not bypass it by hardcoding `/tmp` paths.
- **Hermetic by construction, not by discipline.** Orchestrator tests (`test_verify.py`, `test_integrate.py`, lifecycle harness) inject a fake `run`/`spawn` callable instead of mocking `subprocess` ad hoc — see `FakeRun`/`FakeSpawn` in `test_verify.py` and `tests/orchestrator/lifecycle/fake_gh.py` (a subprocess-level `gh` stand-in placed first on `PATH`, deliberately not a Python-level mock, so the real argument-building/JSON-parsing code paths that broke in prod, #163/#164/#207, actually run).
- **Addon machine-local state is isolated the same way.** `tests/addons/test_aspens.py`'s `_MarkerIsolation` base patches `aspens_module.CACHE_DIR`/`LAST_CHECK_MARKER` to a per-test tempdir — never read/write the real `~/.cache/worktrail/addons/aspens/`. Follow this pattern for any new addon under `src/worktrail/addons/`.

## Non-obvious behaviors
- **AST-based "enforcement coverage" tests are a load-bearing regression-prevention pattern, not incidental.** `test_gate_enforcement_coverage.py`, `test_policy_key_enforcement_coverage.py`, and `test_pr_creation_callsite_enforcement_coverage.py` AST-walk `src/worktrail` for a specific literal shape (e.g. a `gates.append("...")` call, or a `["gh", "pr", "create", ...]` list literal), assert the discovered set exactly matches a hand-maintained `KNOWN_*`/registered-consumer dict, and behaviorally prove each site is actually enforced — not just present. These exist because the same failure mode (a policy gate computed correctly but silently unconsumed by a new call site) recurred 3-4 times in one week. When adding a new `gh pr create` call site or a new `gates.append(...)` string, you MUST register it in the corresponding test file or the AST-walk test fails immediately. Conversely, when a call site migrates onto `router/land_pr.py` and its `gh pr create` literal disappears, remove its entry from `CALLSITE_CONSUMERS` in the same change (as `drain/drain.py`'s was on 2026-09-05) — the AST walk fails on a registered consumer with no matching literal just as it does on the reverse.
- **Call sites that compose with `land_pr` are tested by monkeypatching the module-level `land_pr` name, not by faking `gh pr create`.** `tests/drain/test_drain.py` sets `drain.land_pr` to a stub returning a `LandOutcome` and asserts on the captured `LandRequest` (`route`, `risk`, `watch_timeout_s`, `title`); `tests/router/test_close_stale_openspec.py` patches `worktrail.router.close_stale_openspec.land_pr` the same way. A non-`landed` outcome in drain is asserted through `sweep_remediations`'s logged `<action> error:` line, not `pytest.raises` on the remediation function.
- **`land_pr._push()` regressions go in `tests/router/test_land_pr_push_refusal.py`, not `test_land_pr.py`.** Tasks 1.1→1.2 of `shared-pr-landing-pipeline` already saturate the compile same-file chain gate on `test_land_pr.py`, so push-refusal detail and explicit-refspec tests were deliberately placed in their own module. Its `LandPrPushRefusalOrchestrationTests` shows the pattern for exercising `land_pr()` end-to-end with every prior step patched at the `land_pr` module seam.
- **`*_selfcheck.py` tests** (`test_automerge_selfcheck.py`, `test_dashboard_selfcheck.py`, `test_journal_selfcheck.py`, `test_policy_drift_selfcheck.py`, `test_policy_selfcheck.py`, `test_quarantine_selfcheck.py`) exercise the corresponding `*_selfcheck` module's own drift/consistency detector against the real repo state — treat these as guarding a live invariant, not just unit-testing a helper.
- **`e2e` tests reuse fixtures across modules via relative imports** rather than reinventing fixture builders — e.g. `test_check_spec_collision_e2e.py` imports `_git`/`_init_repo`/`_make_spec`/`_make_task` from `.test_check_spec_collision` and `QueueTestBase` from `..workqueue.test_work_queue`. Prefer importing an existing fixture builder over duplicating one when writing a new e2e test that spans two subsystems.
- **Golden record/replay regression**: `python3 -m worktrail.orchestrator.orchestrate check` (see AGENTS.md Development section) is a separate check from `pytest` — run both before considering orchestrator changes verified.

## Critical files (purpose, not inventory)
- `tests/conftest.py` — the mandatory isolation fixtures every test in the suite inherits (machine-wide config redirect plus session-wide tempfile containment); read it before writing any test that touches env-resolved paths or scratch dirs.
- `tests/orchestrator/lifecycle/fake_gh.py` — canonical subprocess-level fake for anything that shells out to `gh`; extend its `$GH_FAKE_STATE` JSON schema rather than adding a second fake.
- `tests/router/test_gate_enforcement_coverage.py` / `test_pr_creation_callsite_enforcement_coverage.py` — the reference implementations of the AST-walk + registered-consumer pattern; copy this shape for a new class of "silently unenforced" risk.
- `tests/router/test_land_pr_push_refusal.py` — the designated home for `_push()` / push-refusal regressions and the reference for seam-patched full `land_pr()` orchestration tests.

## Critical Rules
- Both `unittest.TestCase` (majority, ~130 files) and plain pytest-style functions (~39 files) coexist — match the style already used in the file/subsystem you're editing rather than converting.
- A test that mocks `subprocess`/`gh` ad hoc instead of using `FakeRun`/`fake_gh.py` risks missing the exact argument-building bugs those fakes exist to catch — prefer the existing fake over a new mock.

---
**Last Updated:** 2026-09-05
