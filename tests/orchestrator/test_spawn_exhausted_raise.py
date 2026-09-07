"""`raise_if_exhausted` is the shared fail-closed boundary for spawn results."""

import pytest

from worktrail.orchestrator.spawnlib import (
    SpawnExhausted,
    SpawnResult,
    raise_if_exhausted,
)
from worktrail.runtime.selection import NoExecutionTarget


def _exhausted(failure_class="billing"):
    return SpawnResult(
        text="Claude usage limit reached",
        usage={},
        exhausted=True,
        failure_class=failure_class,
    )


def test_exhausted_result_raises_naming_context_and_failure_class():
    with pytest.raises(SpawnExhausted) as excinfo:
        raise_if_exhausted(_exhausted(), context="compile")
    assert "compile" in str(excinfo.value)
    assert excinfo.value.failure_class == "billing"
    assert excinfo.value.context == "compile"


def test_caught_by_no_execution_target_handler():
    try:
        raise_if_exhausted(_exhausted("rate_limit"), context="impl worker 1.1")
    except NoExecutionTarget as exc:
        assert isinstance(exc, SpawnExhausted)
        assert exc.failure_class == "rate_limit"
    else:  # pragma: no cover -- the raise above is unconditional
        pytest.fail("SpawnExhausted was not caught as NoExecutionTarget")


def test_non_exhausted_result_returned_unchanged():
    result = SpawnResult(text="ok", usage={})
    assert raise_if_exhausted(result, context="compile") is result


def test_result_without_exhausted_attribute_passes_through():
    class Double:
        text = "ok"

    double = Double()
    assert raise_if_exhausted(double, context="compile") is double
