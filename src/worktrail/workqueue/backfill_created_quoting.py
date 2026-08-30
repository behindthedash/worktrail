#!/usr/bin/env python3
"""
Backfill `created:` timestamp quoting on existing handoff briefs.

`yaml.safe_dump` always single-quotes an ISO-8601 `created:` timestamp string
today (its resolver would otherwise read an unquoted one back as YAML's
implicit `!!timestamp` type, a `datetime.datetime` -- not the `str` every
consumer expects), but a corpus scan found 761 briefs across queue/ + picked/
with an unquoted `created:` line, predating whatever change made today's
quoting consistent. This backfills those legacy files to the single-quoted
form without touching any other field, key order, or body content.

Two phases, mirroring backfill_recommended_route.py's preview/execute split
so a batch rewrite of already-authored briefs is never silent:

  preview -- scan queue/*.md and picked/*.md for a `created:` frontmatter
             line whose value is not already quoted. Files with no
             `created:` line, or one already quoted, are skipped.
  execute -- given a preview's JSON (re-passed via `--preview`, never
             trusted from a stale file) and an explicit `--confirm` (or
             `--decline`, which performs zero writes), rewrite each
             proposed file's `created:` line in place -- wrapping the exact
             existing text in single quotes -- and re-validate via
             brief_frontmatter.validate_brief immediately after, rolling
             the write back if validation fails. A file that changed
             (removed, or already re-quoted) between preview and execute is
             skipped, not clobbered.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..shared.brief_frontmatter import validate_brief
from . import work_queue as wq


def _scan_created_line(content: str) -> tuple[str, bool] | None:
    """Return `(raw_value, already_quoted)` for the frontmatter's `created:`
    line, or None when there is no frontmatter block or no `created:` line."""
    m = wq._FM_RE.match(content)
    if not m:
        return None
    for line in m.group(1).split("\n"):
        if line.startswith("created:"):
            value = line[len("created:") :].strip()
            if not value:
                return None
            return value, value[0] in ("'", '"')
    return None


def build_preview(queue_base: Path) -> dict[str, Any]:
    proposals: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for subdir in ("queue", "picked"):
        directory = queue_base / subdir
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            try:
                content = path.read_text(encoding="utf-8")
            except OSError as exc:
                skipped.append({"id": path.stem, "reason": f"unreadable: {exc}"})
                continue
            scan = _scan_created_line(content)
            if scan is None:
                skipped.append({"id": path.stem, "reason": "no created: line found"})
                continue
            raw_value, already_quoted = scan
            if already_quoted:
                continue  # already canonical -- nothing to backfill
            proposals.append(
                {
                    "id": path.stem,
                    "path": str(path),
                    "created_raw": raw_value,
                }
            )
    return {"proposals": proposals, "skipped": skipped}


def _resolve_preview_payload(raw: str) -> dict[str, Any]:
    """Parse `--preview`, accepting either `preview`'s full payload or a bare
    `{"proposals": [...]}` dict -- same shape-tolerance rationale as
    consolidate_cluster.py's `_resolve_draft_payload`."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--preview is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("proposals"), list):
        raise ValueError("--preview must be a JSON object with a 'proposals' list")
    return parsed


def _requote(content: str, raw_value: str) -> str:
    """Rewrite the frontmatter's `created:` line to single-quote `raw_value`
    verbatim (doubling any embedded `'`, per YAML single-quote escaping),
    leaving every other line -- frontmatter and body -- untouched."""
    m = wq._FM_RE.match(content)
    assert m is not None  # caller already confirmed a created: line exists
    quoted_value = "'" + raw_value.replace("'", "''") + "'"
    lines = m.group(1).split("\n")
    replaced = False
    for i, line in enumerate(lines):
        if not replaced and line.startswith("created:"):
            lines[i] = f"created: {quoted_value}"
            replaced = True
    new_block = "\n".join(lines)
    return content[: m.start(1)] + new_block + content[m.end(1) :]


def execute_apply(
    preview: dict[str, Any], queue_base: Path, confirm: bool
) -> dict[str, Any]:
    stamped: list[str] = []
    skipped: list[dict[str, Any]] = []

    if not confirm:
        return {
            "stamped": stamped,
            "skipped": [
                {"id": p["id"], "reason": "declined"} for p in preview["proposals"]
            ],
        }

    for item in preview["proposals"]:
        path = Path(item["path"])
        if not path.is_file():
            skipped.append(
                {
                    "id": item["id"],
                    "reason": "file no longer present (claimed/moved/removed since preview)",
                }
            )
            continue

        content = path.read_text(encoding="utf-8")
        scan = _scan_created_line(content)
        if scan is None or scan[1]:
            skipped.append(
                {
                    "id": item["id"],
                    "reason": "created: line missing or already quoted since preview",
                }
            )
            continue

        new_content = _requote(content, scan[0])
        path.write_text(new_content, encoding="utf-8")

        ok, verr = validate_brief(path)
        if not ok:
            path.write_text(content, encoding="utf-8")  # roll back
            skipped.append(
                {
                    "id": item["id"],
                    "reason": f"post-write validation failed, rolled back: {verr}",
                }
            )
            continue

        stamped.append(item["id"])

    return {"stamped": stamped, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="backfill created: timestamp quoting on existing handoff briefs"
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--queue-dir",
        default=None,
        help="override the queue base dir containing queue/ and picked/ (default: WORK_QUEUE_DIR)",
    )
    subs = p.add_subparsers(dest="cmd")
    subs.required = True

    subs.add_parser(
        "preview",
        parents=[common],
        help="scan queue/ + picked/ for unquoted created: lines; write nothing",
    )

    execute_p = subs.add_parser(
        "execute", parents=[common], help="requote created: from a preview"
    )
    execute_p.add_argument(
        "--preview",
        help="preview's JSON stdout; omit to read it from stdin instead "
        "(a full-corpus preview routinely exceeds the shell argv size limit)",
    )
    confirm_grp = execute_p.add_mutually_exclusive_group(required=True)
    confirm_grp.add_argument(
        "--confirm", action="store_true", help="write the requoted lines"
    )
    confirm_grp.add_argument(
        "--decline", action="store_true", help="perform zero writes"
    )

    args = p.parse_args(argv)
    queue_base = Path(args.queue_dir) if args.queue_dir else wq.base_dir()

    if args.cmd == "preview":
        result = build_preview(queue_base)
        print(json.dumps(result, indent=2))
        return 0

    try:
        preview = _resolve_preview_payload(
            args.preview if args.preview is not None else sys.stdin.read()
        )
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2

    result = execute_apply(preview, queue_base, args.confirm)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
