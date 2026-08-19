"""Tests for workqueue/invocation.py -- the single owner of the
`-m`-vs-plain-script work_queue.py invocation decision.
"""

import sys
from pathlib import Path

import pytest

from worktrail.workqueue.invocation import WORK_QUEUE_PY, build_work_queue_argv


def test_installed_module_runs_via_dash_m():
    """The canonical installed work_queue.py must run via `-m
    worktrail.workqueue.work_queue`, not as a bare file path -- its
    package-relative imports (`from ..shared import ...`) break under
    bare-file execution."""
    argv = build_work_queue_argv(WORK_QUEUE_PY, ["list", "--json"])
    assert argv == [
        sys.executable, "-m", "worktrail.workqueue.work_queue", "list", "--json",
    ]


def test_override_path_runs_as_plain_script():
    """An explicit non-package override path (e.g. a test double or non-package
    install) still runs as a plain script."""
    override = Path("/tmp/fake_work_queue.py")
    argv = build_work_queue_argv(override, ["claim", "b1", "--json"])
    assert argv == [sys.executable, "/tmp/fake_work_queue.py", "claim", "b1", "--json"]


@pytest.mark.parametrize("args", [
    ["list", "--json"],
    ["claim", "b1", "--json"],
    ["done", "b1", "--planning-only", "--json"],
])
def test_extra_args_preserved(args):
    """Whatever args the caller appends travel through the argv untouched."""
    argv = build_work_queue_argv(WORK_QUEUE_PY, args)
    assert argv[3:] == args
