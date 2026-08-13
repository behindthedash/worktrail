"""Suite-wide test isolation from the operator's real machine-wide state.

A real machine-wide config (routing.yaml, model-defaults.yaml, ...) under the
operator state dir (worktrail_home(): ~/.worktrail, legacy ~/.go) must never
change what a test asserts -- confirmed live 2026-08-03: two test_routing_e2e.py
tests broke the moment an operator actually populated the machine-wide
routing.yaml, because nothing pointed GO_ROUTING_FILE at an isolated path.
Every test in this suite gets a guaranteed-nonexistent path for each known
machine-wide config env var, plus a per-test WORKTRAIL_HOME so resolver-based
defaults (run records, telemetry logs, caches) can never touch the real
~/.worktrail or ~/.go either; a test that needs to exercise a real file does
so explicitly (writing to tmp_path and overriding the same env var itself),
which still wins since monkeypatch restores in LIFO order.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_go_machine_wide_config(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKTRAIL_HOME", str(tmp_path / "worktrail-home"))
    monkeypatch.setenv("GO_ROUTING_FILE", str(tmp_path / "no-such-routing.yaml"))
    monkeypatch.setenv("GO_MODEL_DEFAULTS_FILE", str(tmp_path / "no-such-model-defaults.yaml"))
    monkeypatch.setenv("GO_AGENT_CAPACITY_CACHE", str(tmp_path / "agent-capacity.json"))
    # The real work queue is machine-wide state too: any code path that falls
    # back to work_queue.base_dir() (e.g. drain's backlog seeding) must land in
    # a per-test directory, never the operator's $WORK_QUEUE_DIR/~/work-queue.
    monkeypatch.setenv("WORK_QUEUE_DIR", str(tmp_path / "work-queue"))
