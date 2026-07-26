"""Parsing for OpenSpec's `tasks.md` checklist format.

Verified against OpenSpec 1.6.0 (`@fission-ai/openspec`), whose built-in
`spec-driven` schema defines the `tasks` artifact as:

    ## 1. <Task Group Name>

    - [ ] 1.1 <Task description>
    - [ ] 1.2 <Task description>

and states, in the schema's own instruction text: *"The apply phase parses
checkbox format to track progress. Tasks not using `- [ ]` won't be tracked."*
`openspec archive` reads the same checkboxes -- confirmed by hand-ticking them
with `sed` (no OpenSpec involvement) and observing `Task status: ✓ Complete`.

Two consequences this module leans on:

1. **Groups are first-class.** `## N. Name` is part of the authored format, not
   a convention we impose, and the schema instructs authors to "Group related
   tasks" and "Order tasks by dependency". That makes the group heading a real
   signal about intended execution shape.
2. **Nothing else is.** There is no per-task frontmatter, no file scope, no
   explicit dependency edge -- deliberately, per `docs/design/conductor-lanes.md`
   §2: those come from the compiled RunPlan, not from the authoring artifact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# `## 1. Setup` / `## 12. Core Implementation`
GROUP_RE = re.compile(r"^##\s+(\d+)\.\s*(.*?)\s*$")
# `- [ ] 1.1 Create module` / `- [x] 2.10 Wire endpoint`
TASK_RE = re.compile(r"^(\s*)-\s+\[( |x|X)\]\s+(\d+\.\d+)\s+(.*?)\s*$")

STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"


@dataclass
class ParsedTask:
    id: str  # "1.1" -- the authored identifier, used verbatim as the task id
    title: str
    status: str  # STATUS_PENDING | STATUS_COMPLETED
    group: str  # "1" -- the group number this task belongs to
    group_title: str  # "Setup"
    line_no: int  # 0-based index into the file's lines; the write-back anchor


@dataclass
class ParsedTasks:
    tasks: List[ParsedTask] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def by_id(self, task_id: str) -> Optional[ParsedTask]:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None


def parse_tasks_md(text: str) -> ParsedTasks:
    """Parse an OpenSpec `tasks.md` into ordered `ParsedTask`s.

    Tolerant by design: a checkbox line that does not carry an `N.M` id, or one
    that appears before any `## N.` heading, is recorded as a warning rather
    than raising. OpenSpec itself silently ignores malformed lines ("Tasks not
    using `- [ ]` won't be tracked"), and a hard parse error here would make the
    orchestrator refuse to run a change that OpenSpec considers valid.
    """
    result = ParsedTasks()
    group = ""
    group_title = ""
    seen: set[str] = set()

    for i, line in enumerate(text.splitlines()):
        gm = GROUP_RE.match(line)
        if gm:
            group, group_title = gm.group(1), gm.group(2)
            continue

        tm = TASK_RE.match(line)
        if tm:
            mark, tid, title = tm.group(2), tm.group(3), tm.group(4)
            if not group:
                result.warnings.append(
                    f"line {i + 1}: task {tid} appears before any '## N.' group heading"
                )
            if tid in seen:
                result.warnings.append(f"line {i + 1}: duplicate task id {tid}")
                continue
            seen.add(tid)
            result.tasks.append(
                ParsedTask(
                    id=tid,
                    title=title,
                    status=STATUS_COMPLETED if mark.lower() == "x" else STATUS_PENDING,
                    group=group,
                    group_title=group_title,
                    line_no=i,
                )
            )
            continue

        # A checkbox with no N.M id is invisible to OpenSpec's own tracker too;
        # surface it so an author can see why their task never ran.
        if re.match(r"^\s*-\s+\[( |x|X)\]", line):
            result.warnings.append(
                f"line {i + 1}: checkbox without an 'N.M' id is not trackable: {line.strip()!r}"
            )

    return result


def set_task_checked(tasks_md: Path, task_id: str, checked: bool = True) -> bool:
    """Tick (or untick) one task's checkbox in place. Returns True if changed.

    Surgical: rewrites only the one checkbox marker on that task's line and
    leaves every other byte of the file alone. `tasks.md` is a human-authored,
    reviewed artifact that lands in a PR diff -- a full re-render would turn a
    one-character state change into review noise, and would also risk dropping
    any content this parser does not model.
    """
    tasks_md = Path(tasks_md)
    if not tasks_md.exists():
        return False
    text = tasks_md.read_text()
    parsed = parse_tasks_md(text)
    task = parsed.by_id(task_id)
    if task is None:
        return False

    want = "x" if checked else " "
    if (task.status == STATUS_COMPLETED) == checked:
        return False

    lines = text.splitlines(keepends=True)
    line = lines[task.line_no]
    new_line = re.sub(r"\[( |x|X)\]", f"[{want}]", line, count=1)
    if new_line == line:
        return False
    lines[task.line_no] = new_line
    tasks_md.write_text("".join(lines))
    return True
