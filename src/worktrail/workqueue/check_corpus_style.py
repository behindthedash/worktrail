#!/usr/bin/env python3
"""Scan the work-queue corpus for briefs whose frontmatter is not canonical.

Read-only: every brief under ``queue/`` and ``picked/`` is read and classified,
never rewritten. A brief that cannot be parsed at all (missing/unclosed fence,
YAML that does not parse, a non-mapping document) is ``malformed``; a brief that
parses but whose YAML block is not byte-identical to what
`serialize_frontmatter` would produce is a ``style-mismatch``. Canonical briefs
produce no finding, so an empty result means a clean corpus.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..shared.brief_frontmatter import is_canonical_style, split_frontmatter
from .work_queue import base_dir


def scan_corpus(queue_base: Path) -> list[dict]:
    """Return one finding per non-canonical brief under `queue_base`.

    Walks ``queue/*.md`` and ``picked/*.md``; each finding is
    ``{"path": <str>, "classification": "malformed" | "style-mismatch"}``.
    """
    findings: list[dict] = []
    queue_base = Path(queue_base)
    for subdir in ("queue", "picked"):
        for path in sorted((queue_base / subdir).glob("*.md")):
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                findings.append({"path": str(path), "classification": "malformed"})
                continue
            frontmatter, _ = split_frontmatter(content)
            if not frontmatter:
                findings.append({"path": str(path), "classification": "malformed"})
            elif not is_canonical_style(content):
                findings.append({"path": str(path), "classification": "style-mismatch"})
    return findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan the work-queue corpus for non-canonical brief frontmatter."
    )
    parser.add_argument(
        "--queue-dir",
        default=None,
        help="work-queue root (default: $WORK_QUEUE_DIR, else ~/work-queue)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON output"
    )
    args = parser.parse_args(argv)

    queue_base = Path(args.queue_dir).expanduser() if args.queue_dir else base_dir()
    findings = scan_corpus(queue_base)

    if args.json:
        print(json.dumps({"findings": findings}, indent=2))
    elif findings:
        for finding in findings:
            print(f"{finding['classification']}: {finding['path']}")
    else:
        print(f"corpus is clean: no non-canonical briefs under {queue_base}")

    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
