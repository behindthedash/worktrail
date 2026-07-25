#!/usr/bin/env python3
"""CLI entry point for the devkit TASK-*.md frontmatter contract.

Ported unchanged from developer-kit's `hooks/task_lifecycle.py` CLI section
(previously wired via that repo's PostToolUse hook on Write|Edit of
TASK-(CHG-)?\\d+\\.md files). devkit now shims this via the installed
`worktrail-task-lifecycle` console script.
"""
import sys

from worktrail.taskformats.devkit.schema import is_task_file, update_status, validate_task


def main():
    if len(sys.argv) < 3:
        sys.exit(0)  # No file argument — not our concern; exit cleanly.

    action = sys.argv[1]
    filepath = sys.argv[2]

    if not filepath:
        sys.exit(0)  # Empty CLAUDE_CHANGED_FILE — not our concern.

    if not is_task_file(filepath):
        sys.exit(0)  # Not a TASK-*.md file — not our concern.

    if action == "auto-status":
        update_status(filepath)
    elif action == "validate":
        if not validate_task(filepath):
            sys.exit(1)
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)


if __name__ == "__main__":
    main()
