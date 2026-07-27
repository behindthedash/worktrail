"""Create empty, format-owned spec scaffolds.

This command only creates the durable artifact shape. Authoring the actual
requirements, design, and task breakdown remains an interactive /go stage.
Keeping scaffold creation here gives /go one stable entry point while allowing
repositories to retain either the legacy devkit layout or OpenSpec.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_FORMATS = {"openspec", "devkit"}


def _require_slug(value: str, label: str = "id") -> str:
    value = value.strip()
    if not value or not _SLUG_RE.fullmatch(value):
        raise ValueError(f"{label} must contain only lowercase letters, numbers, '.', '_' or '-'")
    return value


def _write_new(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing file: {path}")
    path.write_text(content)


def create_spec(repo: Path | str, spec_id: str, request: str, fmt: str = "openspec") -> dict:
    """Create a new spec scaffold and return its format/path metadata."""
    repo = Path(repo).resolve()
    fmt = fmt.strip().lower()
    if fmt not in _FORMATS:
        raise ValueError(f"format must be one of: {', '.join(sorted(_FORMATS))}")
    spec_id = _require_slug(spec_id, "spec id")
    request = request.strip()
    if not request:
        raise ValueError("request must not be empty")

    if fmt == "openspec":
        root = repo / "openspec" / "changes" / spec_id
        _write_new(root / "proposal.md", f"# Proposal: {spec_id}\n\n{request}\n")
        _write_new(root / "design.md", f"# Design: {spec_id}\n\n## Context\n\n{request}\n")
        _write_new(
            root / "specs" / spec_id / "spec.md",
            f"# {spec_id}\n\n## ADDED Requirements\n\n### Requirement: Define the outcome\n\nThe system MUST deliver the following outcome: {request}\n\n#### Scenario: Initial behavior\n\n- **WHEN** the implementation is complete\n- **THEN** the requested outcome is observable\n",
        )
        _write_new(root / "tasks.md", "# Tasks\n\n## 1. Implementation\n\n- [ ] 1.1 Define implementation tasks\n")
    else:
        root = repo / "docs" / "specs" / spec_id
        _write_new(root / "user-request.md", request + "\n")
        _write_new(root / "spec.md", f"# {spec_id}\n\n## Feature Summary\n\n{request}\n\n## Acceptance Criteria\n\n- [ ] Define the observable acceptance criteria.\n")
        _write_new(root / "tasks" / "TASK-001.md", "---\nid: TASK-001\ntitle: Define implementation tasks\nstatus: pending\ndependencies: []\nfiles: []\nkind: impl\n---\n\n## Implementation Details\n\nDescribe the implementation work.\n")

    return {"format": fmt, "spec_id": spec_id, "path": str(root), "created": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a Worktrail spec scaffold")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--format", choices=sorted(_FORMATS), default="openspec")
    parser.add_argument("--id", required=True, dest="spec_id")
    parser.add_argument("--request", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = create_spec(args.repo, args.spec_id, args.request, args.format)
    except (FileExistsError, OSError, ValueError) as exc:
        print(f"worktrail-spec-create: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result) if args.json else result["path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
