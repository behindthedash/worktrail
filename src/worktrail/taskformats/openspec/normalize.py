"""Normalize top-level OpenSpec task checkboxes that lack task ids."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

from .schema import GROUP_RE, TASK_RE, parse_tasks_md

CHECKBOX_RE = re.compile(r"^(-\s+\[(?: |x|X)\])(\s*)(.*)$")


def normalize_untrackable_task_ids(text: str) -> str:
    """Assign stable group-scoped ids, leaving ungrouped checkboxes untouched."""
    lines = text.splitlines(keepends=True)
    highest: dict[str, int] = {}
    scan_group: str | None = None
    for line in lines:
        body = line.rstrip("\r\n")
        heading = GROUP_RE.match(body)
        if heading:
            scan_group = heading.group(1)
            continue
        task = TASK_RE.match(body)
        if task and scan_group and task.group(3).startswith(f"{scan_group}."):
            suffix = int(task.group(3)[len(scan_group) + 1 :])
            highest[scan_group] = max(highest.get(scan_group, 0), suffix)

    group: str | None = None
    next_suffix = dict(highest)
    output: list[str] = []

    for line in lines:
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        heading = GROUP_RE.match(body)
        if heading:
            group = heading.group(1)
        task = TASK_RE.match(body)
        if task:
            output.append(line)
            continue

        checkbox = CHECKBOX_RE.match(body)
        if checkbox and group:
            suffix = next_suffix.get(group, 0) + 1
            next_suffix[group] = suffix
            spacing = checkbox.group(2) or " "
            output.append(
                f"{checkbox.group(1)}{spacing}{group}.{suffix} {checkbox.group(3)}{ending}"
            )
        else:
            output.append(line)

    return "".join(output)


def _tasks_path(path: Path) -> Path:
    return path / "tasks.md" if path.is_dir() else path


def _atomic_write(path: Path, text: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="OpenSpec change directory or tasks.md")
    parser.add_argument(
        "--check", action="store_true", help="report needed changes without writing"
    )
    args = parser.parse_args(argv)
    path = _tasks_path(args.path)
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            original = stream.read()
    except (OSError, UnicodeError) as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return 2

    parsed = parse_tasks_md(original)
    normalized = normalize_untrackable_task_ids(original)
    for item in parsed.untrackable:
        print(f"line {item.line_number}: {item.text}")
    if normalized == original:
        if args.check and parsed.untrackable:
            print("task id normalization is needed", file=sys.stderr)
            return 1
        print(
            "task ids are normalized"
            if not parsed.untrackable
            else "no grouped checkboxes could be normalized"
        )
        return 0
    if args.check:
        print("task id normalization is needed", file=sys.stderr)
        return 1
    try:
        _atomic_write(path, normalized)
    except OSError as exc:
        print(f"cannot write {path}: {exc}", file=sys.stderr)
        return 2
    print(f"normalized {len(parsed.untrackable)} task checkbox(es) in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
