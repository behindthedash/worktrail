"""Shared `work_queue.py` subprocess invocation.

work_queue.py uses package-relative imports (`from ..shared import ...`), so it
must run via `-m worktrail.workqueue.work_queue`, not as a bare file path
(which loses package context) -- but only for the real installed module; an
explicit override path (e.g. a test double or non-package install) still runs
as a plain script.  This module owns that `-m`-vs-plain-script decision and the
canonical path constant in one place instead of duplicating them across
drain.list_queue and router/consolidate_cluster._run_work_queue_cli.
"""

import sys
from pathlib import Path
from typing import List

# work_queue.py is the work-queue subsystem's owner of queue/picked -- a
# sibling module within worktrail, resolved relative to this file.
WORK_QUEUE_PY = Path(__file__).resolve().parent / "work_queue.py"


def build_work_queue_argv(work_queue_py: Path, args: List[str]) -> List[str]:
    """Build the subprocess argv that runs work_queue.py with `args`.

    The canonical installed module (`WORK_QUEUE_PY`) is invoked via
    `-m worktrail.workqueue.work_queue`; any explicit override path (e.g. a test
    double or non-package install) runs as a plain script.
    """
    if work_queue_py == WORK_QUEUE_PY:
        return [sys.executable, "-m", "worktrail.workqueue.work_queue", *args]
    return [sys.executable, str(work_queue_py), *args]
