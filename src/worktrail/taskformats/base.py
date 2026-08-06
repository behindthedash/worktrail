"""The TaskSource adapter interface.

Everything under `worktrail.orchestrator` operates on plain task dicts
(`coordinator.py`'s own description: "a plain list of task dicts... the shape
produced by spec-to-tasks / fix_plan.json") and a handful of filesystem paths
derived from a `spec_ref`. A `TaskSource` is the one seam between that planning
core and wherever task definitions actually live on disk — the devkit
`docs/specs/[id]/tasks/TASK-*.md` convention (`worktrail.taskformats.devkit`)
and the OpenSpec `openspec/changes/<id>/tasks.md` convention. Nothing in
`orchestrator/` should construct a task-definition path itself; it asks its
`TaskSource` instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, TypedDict


class TaskDict(TypedDict, total=False):
    id: str
    title: str
    status: str
    deps: List[str]
    external_deps: List[str]
    external_deps_warnings: List[str]
    files: List[str]
    kind: str
    reqs: List[str]
    timeout: Optional[int]
    review: str
    complexity: str
    domain: str
    purpose: str
    path: str
    frontmatter_warnings: List[str]


class TaskSource(Protocol):
    """Where task definitions come from, and how to write status back."""

    def load(self, spec_ref: str) -> tuple[str, List[TaskDict]]:
        """Return (spec_id, tasks) for the given spec reference."""
        ...

    def mark_status(self, task_id: str, status: str, *, spec_ref: Optional[str] = None) -> bool:
        """Persist a status transition for one task."""
        ...

    def resolve_external_dependency(self, dep_ref: str) -> Dict[str, Any]:
        """Resolve a `<spec-id>/<task-id>` cross-spec reference."""
        ...

    def task_brief_ref(self, task_id: str, spec_ref: str) -> tuple[str, str]:
        """Where a cold worker reads this task's brief: `(repo-relative path, anchor)`.

        The anchor is `""` when the file *is* the brief (devkit: one
        `TASK-*.md` per task) and the task id when the brief is an item inside a
        shared file (OpenSpec: one `tasks.md` for the whole change).

        Separate from `task_file_path` on purpose. That one answers "which file
        does the orchestrator read and write"; this one answers "what do I tell
        a worker to open", and the two diverge the moment a format stops giving
        each task its own file.
        """
        ...

    def task_file_path(self, task_id: str, spec_ref: str) -> Path:
        """Where the given task's definition file lives on disk."""
        ...

    def spec_root(self, spec_ref: str) -> Path:
        """The root directory for the given spec reference (relative to repo root)."""
        ...

    def spec_root_prefix(self) -> str:
        """Path prefix (e.g. `docs/specs/`) worker agents must never write to —
        used to build safety guards like `FORBIDDEN_WORKER_PATH_PREFIXES`."""
        ...

    def file_sections(self, text: str) -> tuple[List[str], List[str]]:
        """Return authored create/modify file sections, when the format has them."""
        ...
