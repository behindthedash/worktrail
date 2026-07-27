"""Parser for GitHub Spec Kit ``tasks.md`` checklists."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

GROUP_RE = re.compile(r"^#{2,3}\s+(?:(?:Phase|User Story)\s+)?([\w.-]+)\s*[:.-]?\s*(.*?)\s*$", re.I)
TASK_RE = re.compile(r"^\s*-\s+\[( |x|X)\]\s+(?:\*\*)?(T\d{3,})(?:\*\*)?\s+(.*?)\s*$")
TAG_RE = re.compile(r"^\s*\[([^\]]+)\]\s*")
STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
KNOWN_KINDS = frozenset({"impl", "e2e", "cleanup", "docs", "feature", "chore"})


@dataclass
class ParsedTask:
    id: str
    title: str
    status: str
    group: str
    group_title: str
    line_no: int
    tags: list[str] = field(default_factory=list)
    kind: str = "impl"


@dataclass
class ParsedTasks:
    tasks: list[ParsedTask] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def by_id(self, task_id: str) -> Optional[ParsedTask]:
        return next((task for task in self.tasks if task.id == task_id), None)


def split_tags(title: str) -> tuple[str, list[str]]:
    tags: list[str] = []
    rest = title
    while True:
        match = TAG_RE.match(rest)
        if not match:
            return rest.strip(), tags
        tags.append(match.group(1).strip())
        rest = rest[match.end():]


def parse_tasks_md(text: str) -> ParsedTasks:
    result = ParsedTasks()
    group = ""
    group_title = ""
    seen: set[str] = set()

    for line_no, line in enumerate(text.splitlines()):
        group_match = GROUP_RE.match(line)
        if group_match:
            group, group_title = group_match.group(1), group_match.group(2).strip()
            continue

        task_match = TASK_RE.match(line)
        if task_match:
            mark, task_id, raw_title = task_match.groups()
            if task_id in seen:
                result.warnings.append(f"line {line_no + 1}: duplicate task id {task_id}")
                continue
            seen.add(task_id)
            title, tags = split_tags(raw_title)
            kinds = [tag.lower() for tag in tags if tag.lower() in KNOWN_KINDS]
            result.tasks.append(
                ParsedTask(
                    id=task_id,
                    title=title or raw_title,
                    status=STATUS_COMPLETED if mark.lower() == "x" else STATUS_PENDING,
                    group=group,
                    group_title=group_title,
                    line_no=line_no,
                    tags=tags,
                    kind=kinds[0] if kinds else "impl",
                )
            )
            continue

        if re.match(r"^\s*-\s+\[( |x|X)\]", line):
            result.warnings.append(
                f"line {line_no + 1}: checkbox without a TNNN id is not trackable: {line.strip()!r}"
            )

    if not result.tasks:
        result.warnings.append("no Spec Kit tasks found in tasks.md")
    return result


def set_task_checked(tasks_md, task_id: str, checked: bool = True) -> bool:
    from pathlib import Path

    path = Path(tasks_md)
    if not path.is_file():
        return False
    text = path.read_text()
    parsed = parse_tasks_md(text)
    task = parsed.by_id(task_id)
    if task is None or (task.status == STATUS_COMPLETED) == checked:
        return False
    lines = text.splitlines(keepends=True)
    line = lines[task.line_no]
    marker = "x" if checked else " "
    lines[task.line_no] = re.sub(r"\[( |x|X)\]", f"[{marker}]", line, count=1)
    path.write_text("".join(lines))
    return True
