"""Local override of the root conftest.py's routing.yaml seed.

drain.py's own `machine_wide_routing()` tolerates a missing/empty routing
file gracefully (returns None -- see its docstring), and this whole test
file's capacity-gate scenarios were written against that "no routing
configured" baseline: bare-harness cache keys (e.g. "claude", not
"claude-sub:sonnet"). The root conftest.py's autouse fixture now seeds a real
routing.targets/tiers file for every test in the suite (needed elsewhere, for
tests exercising live.py's/compile.py's default-model resolution), which
would otherwise make drain.py resolve a REAL routing table here and silently
change these tests' capacity-gate semantics out from under them. Point
GO_ROUTING_FILE back at a nonexistent path for this directory specifically;
individual tests that want routing-sourced behavior already monkeypatch
`drain.machine_wide_routing()` directly (see test_select_available_agent_*
and the two `monkeypatch.setattr(drain, "machine_wide_routing", ...)` call
sites), which is unaffected by this override either way.
"""

import pytest


@pytest.fixture(autouse=True)
def _no_machine_wide_routing_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("GO_ROUTING_FILE", str(tmp_path / "no-such-routing.yaml"))
