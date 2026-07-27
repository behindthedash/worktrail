"""``TaskSource`` adapter for GitHub Spec Kit feature tasks."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from worktrail.orchestrator.coordinator import TAIL_KINDS
from . import schema

DEFAULT_SPEC_ROOT = ".specify/specs"


class SpecKitTaskSource:
    """Read and update ``.specify/specs/<feature>/tasks.md``."""

    def __init__(self, repo_root: Path, spec_root: str = DEFAULT_SPEC_ROOT):
        self.repo_root = Path(repo_root)
        self._spec_root = spec_root

    def load(self, spec_ref: str) -> tuple[str, List[Dict[str, Any]]]:
        tasks_path = self.task_file_path("", spec_ref)
        if not tasks_path.is_file():
            raise FileNotFoundError(f"no tasks.md for Spec Kit feature {spec_ref!r} at {tasks_path}")
        parsed = schema.parse_tasks_md(tasks_path.read_text())
        rel = str(tasks_path.relative_to(self.repo_root))
        previous_by_group: dict[str, str] = {}
        non_tail_ids: list[str] = []
        tasks: list[Dict[str, Any]] = []
        for task in parsed.tasks:
            deps = [previous_by_group[task.group]] if task.group in previous_by_group else []
            previous_by_group[task.group] = task.id
            if task.kind in TAIL_KINDS:
                deps = sorted(set(deps) | set(non_tail_ids))
            else:
                non_tail_ids.append(task.id)
            tasks.append({
                "id": task.id,
                "title": task.title,
                "status": task.status,
                "deps": deps,
                "external_deps": [],
                "files": [],
                "kind": task.kind,
                "tags": list(task.tags),
                "group": task.group,
                "group_title": task.group_title,
                "path": rel,
                "frontmatter_warnings": list(parsed.warnings),
            })
        return spec_ref, tasks

    def resolve_external_dependency(self, dep_ref: str) -> Dict[str, Any]:
        unresolved = {"ref": dep_ref, "resolved": False, "satisfied": False, "status": None, "reason": "malformed"}
        feature, separator, task_id = dep_ref.partition("/")
        if not separator or not feature or not task_id:
            return unresolved
        root = self.repo_root.resolve()
        tasks_path = (root / self._spec_root / feature / "tasks.md").resolve()
        try:
            tasks_path.relative_to(root)
        except ValueError:
            return {**unresolved, "reason": "feature folder not found"}
        if not tasks_path.is_file():
            return {**unresolved, "reason": "feature folder not found"}
        task = schema.parse_tasks_md(tasks_path.read_text()).by_id(task_id)
        if task is None:
            return {**unresolved, "reason": "task not found"}
        return {
            "ref": dep_ref,
            "resolved": True,
            "satisfied": task.status == schema.STATUS_COMPLETED,
            "status": task.status,
            "reason": None,
        }

    def mark_status(self, task_id: str, status: str, *, spec_ref: Optional[str] = None) -> bool:
        if spec_ref is None:
            raise ValueError("SpecKitTaskSource.mark_status requires spec_ref")
        if status != "completed":
            return False
        return schema.set_task_checked(self.task_file_path(task_id, spec_ref), task_id, True)

    def task_brief_ref(self, task_id: str, spec_ref: str) -> tuple[str, str]:
        return f"{self._spec_root}/{spec_ref}/tasks.md", task_id

    def task_file_path(self, task_id: str, spec_ref: str) -> Path:
        return self.spec_root(spec_ref) / "tasks.md"

    def spec_root(self, spec_ref: str) -> Path:
        return self.repo_root / self._spec_root / spec_ref

    def spec_root_prefix(self) -> str:
        return ".specify/"

    def file_sections(self, text: str) -> tuple[List[str], List[str]]:
        return [], []
