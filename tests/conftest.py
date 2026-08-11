"""Suite-wide test isolation from the operator's real ~/.go/*.yaml files.

A real machine-wide config (routing.yaml, model-defaults.yaml, ...) must never
change what a test asserts -- confirmed live 2026-08-03: two test_routing_e2e.py
tests broke the moment an operator actually populated ~/.go/routing.yaml, because
nothing pointed GO_ROUTING_FILE at an isolated path. Every test in this suite gets
a guaranteed-nonexistent path for each known machine-wide config env var; a test
that needs to exercise a real file does so explicitly (writing to tmp_path and
overriding the same env var itself), which still wins since monkeypatch restores
in LIFO order.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_go_machine_wide_config(tmp_path, monkeypatch):
    monkeypatch.setenv("GO_ROUTING_FILE", str(tmp_path / "no-such-routing.yaml"))
    monkeypatch.setenv("GO_MODEL_DEFAULTS_FILE", str(tmp_path / "no-such-model-defaults.yaml"))
    monkeypatch.setenv("GO_AGENT_CAPACITY_CACHE", str(tmp_path / "agent-capacity.json"))
