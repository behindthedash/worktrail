"""Regression guard for the load-flaky fixed subprocess waits in tests/router.

Original failure (2026-09-05, two concurrent full-suite runs in a linked
worktree on main @ ec66204b): a hardcoded `process.wait(timeout=5)` in
`test_internal_dispatch_lifecycle._run_seeded_lifecycle` raised
`subprocess.TimeoutExpired` under load while the same test passed in the
concurrent run, alone, and on CI. The orchestrator's integrated smoke gate
reads that red as real and quarantines the whole task group.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ._subprocess_timeouts import (
    DEFAULT_TIMEOUT_S,
    ENV_VAR,
    subprocess_timeout_s,
)

_ROUTER_TESTS = Path(__file__).parent

# `wait(timeout=5)` / `communicate(timeout=5)` and `time.monotonic() + 5` -- the
# two shapes that bound a wait on a real child process with a fixed number.
_FIXED_WAIT = re.compile(r"\b(?:wait|communicate)\(\s*timeout\s*=\s*\d+(?:\.\d+)?\s*\)")
_FIXED_DEADLINE = re.compile(r"\bmonotonic\(\)\s*\+\s*\d+(?:\.\d+)?\b")

# Doubles that raise a pre-built TimeoutExpired carry a `timeout=` kwarg that
# is a label on a simulated failure, not a real budget, so they are exempt.
_SIMULATED = re.compile(r"TimeoutExpired\s*\(")


def test_default_is_generous_enough_to_survive_a_loaded_host():
    """A passing run never spends this budget; only a real hang pays for it."""
    assert subprocess_timeout_s() == DEFAULT_TIMEOUT_S
    assert DEFAULT_TIMEOUT_S >= 30


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.25", 0.25),
        ("120", 120.0),
        ("  90  ", 90.0),
    ],
)
def test_env_override_is_honoured(monkeypatch, raw, expected):
    monkeypatch.setenv(ENV_VAR, raw)
    assert subprocess_timeout_s() == expected


@pytest.mark.parametrize("raw", ["", "   ", "junk", "0", "-3", "nan-ish"])
def test_unusable_override_falls_back_instead_of_failing_the_suite(monkeypatch, raw):
    """A misconfigured knob must not turn every waiting test into an error."""
    monkeypatch.setenv(ENV_VAR, raw)
    assert subprocess_timeout_s() == DEFAULT_TIMEOUT_S


def test_no_fixed_budget_bounds_a_real_subprocess_wait_in_router_tests():
    """The whole class, not just the one site that happened to go red.

    Fails on the pre-fix tree naming all nine hardcoded 5s sites across
    test_internal_dispatch_lifecycle.py, fake_internal_dispatch_agent.py, and
    test_check_openspec_propose_resume_integration.py.
    """
    offenders: list[str] = []
    for path in sorted(_ROUTER_TESTS.glob("*.py")):
        if path.name == Path(__file__).name or path.name == "_subprocess_timeouts.py":
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if _SIMULATED.search(line):
                continue
            if _FIXED_WAIT.search(line) or _FIXED_DEADLINE.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "fixed wall-clock budgets bound a real subprocess wait -- use "
        f"subprocess_timeout_s() ({ENV_VAR}-overridable) instead:\n"
        + "\n".join(offenders)
    )
