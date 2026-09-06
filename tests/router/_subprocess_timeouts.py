"""Load-tolerant wall-clock budgets for tests that wait on a real subprocess.

A fixed 5s budget is not a correctness bound -- it is a guess about how fast
this machine happens to be while the test runs. Confirmed load-flaky
2026-09-05: two concurrent full-suite runs in a linked worktree on main @
ec66204b, and
`test_internal_dispatch_lifecycle.py::InternalDispatchLifecycleTests::test_seeded_child_mutates_only_the_exact_parent_run_record`
(subtest agent='claude') died on `subprocess.TimeoutExpired` raised by a
hardcoded `process.wait(timeout=5)` while waiting on the
`worktrail-skill-dispatch` child. The other concurrent run passed the same
test, and it passes alone and on CI (run A: 1 failed, 5561 passed; run B:
clean).

That red is expensive out of proportion to its cause: the orchestrator's
integrated smoke gate reads any suite failure as real and quarantines the
whole task group, so a machine-speed guess costs a ci-fix cycle (observed
during run agent-capacity-expired-gate-hygiene, which quarantined 2 of 3
groups on exactly this).

Every wait these budgets guard has the same shape: the child *will* finish on
its own, and the budget exists only so a genuinely hung child fails the suite
instead of hanging it forever. That makes a generous budget strictly better
than a tight one -- a passing run never spends the extra headroom, and only a
real hang ever pays for it.

Set `WORKTRAIL_TEST_TIMEOUT` (seconds) to override, e.g. a small value to
assert timeout behaviour itself, or a larger one on a heavily loaded host. A
missing, unparseable, or non-positive value falls back to the default rather
than failing the suite on a misconfigured environment.
"""

from __future__ import annotations

import os

ENV_VAR = "WORKTRAIL_TEST_TIMEOUT"
DEFAULT_TIMEOUT_S = 60.0


def subprocess_timeout_s() -> float:
    """Return the wall-clock budget, in seconds, for waiting on a subprocess."""
    raw = os.environ.get(ENV_VAR)
    if raw is None:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw.strip())
    except (AttributeError, ValueError):
        return DEFAULT_TIMEOUT_S
    if value <= 0:
        return DEFAULT_TIMEOUT_S
    return value
