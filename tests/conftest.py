"""Suite-wide test isolation from the operator's real machine-wide state.

A real machine-wide config (routing.yaml, model-defaults.yaml, ...) under the
operator state dir (worktrail_home(): ~/.worktrail, legacy ~/.go) must never
change what a test asserts -- confirmed live 2026-08-03: two test_routing_e2e.py
tests broke the moment an operator actually populated the machine-wide
routing.yaml, because nothing pointed GO_ROUTING_FILE at an isolated path.
Every test in this suite gets a per-test WORKTRAIL_HOME so resolver-based
defaults (run records, telemetry logs, caches) can never touch the real
~/.worktrail or ~/.go either; a test that needs to exercise a real file does
so explicitly (writing to tmp_path and overriding the same env var itself),
which still wins since monkeypatch restores in LIFO order.

The isolated routing file IS a guaranteed-nonexistent path: `routing.agents`
(the `{claude,codex,opencode}: {default_model}` shape `spawnlib.
default_model_for_agent()` reads) is a retired key as of the target-selector
routing schema (`policy._reject_legacy_routing_keys()`, task 1.4) -- writing
it here would make every test that merely calls `load_policy()`/
`resolve_routing()` (not just the ones exercising spawn/model-default
semantics) raise `OperatorConfigError` ambiently. A test that needs
`default_model_for_agent()` to resolve successfully sets up its own
GO_ROUTING_FILE override in the current `routing.targets`/`routing.tiers`
schema.

WORKTRAIL_SKILL_DISPATCH_DEPTH is ambient *dispatch-session* state of the same
class: a suite run from inside a dispatched session (worktrail-go ->
worktrail-skill-dispatch -> agent shell -> pytest) inherits depth=1, and every
test calling skill_dispatch.main() on an internal skill then trips
blocked_internal_dispatch_recursion -- confirmed live 2026-08-25, 8 failures in
test_skill_dispatch.py inside dispatched sessions while the clean-env suite
passed (runs go-20260825-135050 / go-20260825-202107). The recursion guard
itself reads ambient depth by design and stays; tests that exercise depth
semantics set the variable explicitly.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_go_machine_wide_config(tmp_path, monkeypatch):
    # Dispatch-chain marker: see module docstring -- never inherit it ambiently.
    monkeypatch.delenv("WORKTRAIL_SKILL_DISPATCH_DEPTH", raising=False)
    monkeypatch.setenv("WORKTRAIL_HOME", str(tmp_path / "worktrail-home"))
    monkeypatch.setenv("GO_ROUTING_FILE", str(tmp_path / "no-such-routing.yaml"))
    monkeypatch.setenv("GO_MODEL_DEFAULTS_FILE", str(tmp_path / "no-such-model-defaults.yaml"))
    monkeypatch.setenv("GO_AGENT_CAPACITY_CACHE", str(tmp_path / "agent-capacity.json"))
    # The real work queue is machine-wide state too: any code path that falls
    # back to work_queue.base_dir() (e.g. drain's backlog seeding) must land in
    # a per-test directory, never the operator's $WORK_QUEUE_DIR/~/work-queue.
    monkeypatch.setenv("WORK_QUEUE_DIR", str(tmp_path / "work-queue"))
