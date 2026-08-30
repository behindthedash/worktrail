#!/usr/bin/env python3
"""
Backfill `recommended-route:` onto existing queued handoff briefs that
predate Step 2.5's creation-time stamping (SKILL.md Step 2.5, PR #323).

Two phases, mirroring consolidate_cluster.py's preview/execute split so a
batch write to already-authored briefs is never silent:

  preview -- classify every queue/*.md brief that doesn't already carry
             `recommended-route:` via Worktrail's classify command
             (invoked as a subprocess -- classify.py lives in a sibling
             skill, not the shared plugin-level _shared/ dir, so this
             mirrors consolidate_cluster.py's documented no-cross-skill-
             import convention rather than importing it as a module).
             Propose a stamp only when confidence is high/medium AND
             ambiguous_between is empty -- stricter than Step 2.5's own
             creation-time rule (which also stamps a plain "low" with no
             ambiguity), deliberately: these are pre-existing briefs
             batch-processed with no per-item human context at capture
             time, so a shaky guess here risks poisoning /go auto's hint
             pool across the whole backlog, not just one fresh capture.
  execute -- given a preview's JSON (re-passed via `--preview`, never
             trusted from a stale file) and an explicit `--confirm` (or
             `--decline`, which performs zero writes), stamp each proposed
             brief's `recommended-route:` field via work_queue.py's
             `_set_fm_fields` -- imported directly since this script lives
             in the same skill (devkit-pm-handoff) as work_queue.py, the
             same same-skill-import precedent its own test suite already
             uses (`import work_queue as q; q._set_fm_fields(...)`) -- and
             re-validates via brief_frontmatter.validate_brief immediately
             after each write, matching every other work_queue.py write
             path. A brief that got claimed/re-stamped between preview and
             execute is skipped, not clobbered.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..shared.brief_frontmatter import validate_brief
from . import work_queue as wq


def _resolve_classify_command() -> list[str] | None:
    """Argv prefix to invoke classify.py's `--request ... --json` CLI, or None if unavailable.

    Prefers worktrail's own `router.classify` once that sub-phase lands (installed console
    script, then importable module); falls back to devkit's still-unmigrated copy in the
    meantime so this keeps working across the extraction's phased rollout.
    """
    if shutil.which("worktrail-classify"):
        return ["worktrail-classify"]
    try:
        import worktrail.router.classify  # noqa: F401

        return [sys.executable, "-m", "worktrail.router.classify"]
    except ImportError:
        pass
    return None


def classify_focus(
    classify_command: list[str], focus_text: str
) -> dict[str, Any] | None:
    """Run classify.py against `focus_text`; None on any failure (skip, don't guess)."""
    try:
        proc = subprocess.run(
            [*classify_command, "--request", focus_text, "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def build_preview(queue_dir: Path, classify_command: list[str]) -> dict[str, Any]:
    proposals: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for path in sorted(queue_dir.glob("*.md")):
        fm = wq._read_frontmatter(path)
        existing = fm.get("recommended-route")
        if isinstance(existing, str) and existing.strip():
            continue  # already stamped -- nothing to backfill

        focus_text = wq._focus_of(path)
        if not focus_text:
            skipped.append({"id": path.stem, "reason": "no focus text found"})
            continue

        result = classify_focus(classify_command, focus_text)
        if result is None:
            skipped.append(
                {
                    "id": path.stem,
                    "reason": "classify.py errored or produced invalid JSON",
                }
            )
            continue

        confidence = result.get("confidence")
        ambiguous = result.get("ambiguous_between") or []
        if confidence not in ("high", "medium") or ambiguous:
            skipped.append(
                {
                    "id": path.stem,
                    "reason": f"confidence={confidence!r} ambiguous_between={ambiguous!r}",
                }
            )
            continue

        proposals.append(
            {
                "id": path.stem,
                "path": str(path),
                "focus": focus_text,
                "proposed_route": result["route"],
                "route_name": result.get("route_name"),
                "confidence": confidence,
                "classify_reason": result.get("reason"),
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
        raise ValueError("--preview must be a JSON object with a 'proposals' list")  # noqa: TRY004 -- ValueError is this function's uniform malformed-input contract; callers catch ValueError specifically
    return parsed


def execute_apply(
    preview: dict[str, Any], queue_dir: Path, confirm: bool
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
        if not path.is_file() or path.parent.resolve() != queue_dir.resolve():
            skipped.append(
                {
                    "id": item["id"],
                    "reason": "brief no longer present in queue/ (claimed/moved since preview)",
                }
            )
            continue

        fm = wq._read_frontmatter(path)
        existing = fm.get("recommended-route")
        if isinstance(existing, str) and existing.strip():
            skipped.append(
                {
                    "id": item["id"],
                    "reason": "already stamped since preview -- not clobbering",
                }
            )
            continue

        try:
            wq._set_fm_fields(path, {"recommended-route": item["proposed_route"]})
        except (OSError, ValueError) as exc:
            skipped.append({"id": item["id"], "reason": f"write failed: {exc}"})
            continue

        ok, verr = validate_brief(path)
        if not ok:
            skipped.append(
                {"id": item["id"], "reason": f"post-write validation failed: {verr}"}
            )
            continue

        stamped.append(item["id"])

    return {"stamped": stamped, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="backfill recommended-route: on existing queue briefs"
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--queue-dir",
        default=None,
        help="override queue/ (default: WORK_QUEUE_DIR/queue)",
    )
    subs = p.add_subparsers(dest="cmd")
    subs.required = True

    subs.add_parser(
        "preview",
        parents=[common],
        help="classify every un-stamped brief; write nothing",
    )

    execute_p = subs.add_parser(
        "execute", parents=[common], help="stamp recommended-route from a preview"
    )
    execute_p.add_argument("--preview", required=True, help="preview's JSON stdout")
    confirm_grp = execute_p.add_mutually_exclusive_group(required=True)
    confirm_grp.add_argument("--confirm", action="store_true", help="write the stamps")
    confirm_grp.add_argument(
        "--decline", action="store_true", help="perform zero writes"
    )

    args = p.parse_args(argv)
    queue_dir = Path(args.queue_dir) if args.queue_dir else wq.queue_dir()

    if args.cmd == "preview":
        classify_command = _resolve_classify_command()
        if classify_command is None:
            print(json.dumps({"error": "classify.py not found"}), file=sys.stderr)
            return 2
        result = build_preview(queue_dir, classify_command)
        print(json.dumps(result, indent=2))
        return 0

    try:
        preview = _resolve_preview_payload(args.preview)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2

    result = execute_apply(preview, queue_dir, args.confirm)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
