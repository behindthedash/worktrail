"""Create and validate a Worktrail handoff brief."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from ..router.classify import classify
from ..shared.brief_frontmatter import validate_brief
from . import score_candidates
from . import work_queue
from .work_queue import normalize_dependency_reference


class _LiteralStr(str):
    """Marker subclass so the YAML dumper renders this value as a literal
    block scalar (``|``) instead of a quoted flow scalar.

    Free-text fields like ``focus`` routinely contain a colon+space or a
    space+``#`` (both of which force PyYAML to quote a plain scalar) and
    apostrophes (which single-quote style then escapes by doubling, e.g.
    ``PR #2010''s``). A literal block scalar needs no escaping at all.
    """


def _represent_literal_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.SafeDumper.add_representer(_LiteralStr, _represent_literal_str)


def _slugify(focus: str) -> str:
    words = re.findall(r"[a-z0-9]+", focus.lower())[:5]
    return "-".join(words) or "handoff"


def _clean_lines(values: Optional[Iterable[str]]) -> list[str]:
    return [value.strip() for value in (values or []) if value and value.strip()]


def _validate_blocked_by(values: Optional[Iterable[str]]) -> list[str]:
    """Return trimmed dependency references, rejecting empty or comma-joined values."""
    cleaned: list[str] = []
    for raw in values or []:
        try:
            cleaned.append(normalize_dependency_reference(raw))
        except ValueError as exc:
            message = str(exc)
            if "comma-joined" in message:
                raise ValueError(
                    "blocked-by accepts exactly one dependency reference per flag; "
                    "repeat --blocked-by for each prerequisite instead of comma-joining values"
                ) from exc
            raise ValueError("blocked-by values must be non-empty dependency references") from exc
    return cleaned


def _normalize_repo(repo: Optional[str]) -> Optional[str]:
    """Resolve a `--repo` value to an absolute path at capture time.

    A bare or `owner/name` value (e.g. 'devops', 'behindthedash/devops') has
    no filesystem meaning by itself, and dashboard.py's `auto_pick_brief()`
    only resolves such values by basename when a `repos_root` is supplied at
    read time -- capturing the absolute path here fixes it at the source
    instead of relying on every future reader to do that resolution.

    Tries, in order: the value as given (absolute or cwd-relative), then its
    basename under `~/projects` (the same default the /go front door and
    dashboard.py's own auto-pick resolution use). Returns the value
    unchanged when neither exists -- fabricating a wrong absolute path would
    be worse than leaving a value a reader can still recognize by basename.
    """
    if not repo:
        return repo
    direct = Path(repo).expanduser()
    if direct.is_dir():
        return str(direct.resolve())
    projects_candidate = Path.home() / "projects" / Path(repo).name
    if projects_candidate.is_dir():
        return str(projects_candidate.resolve())
    return repo


def _section(title: str, value: Optional[str]) -> str:
    value = (value or "").strip()
    return f"\n## {title}\n\n{value}\n" if value else ""


def _list_section(title: str, values: Iterable[str]) -> str:
    lines = _clean_lines(values)
    return f"\n## {title}\n\n" + "\n".join(f"- {line}" for line in lines) + "\n" if lines else ""


def _route_for(focus: str, requested: Optional[str]) -> Optional[str]:
    if requested:
        route = requested.strip().upper()
        if route not in "ABCDEFGHIJ":
            raise ValueError("recommended route must be one of A-J")
        return route
    result = classify(focus)
    if result["confidence"] == "low" and result["ambiguous_between"]:
        return None
    if result["route_source"] == "no-signal-default":
        return None
    return result["route"]


def _brief_body(
    focus: str,
    context: Optional[str],
    approach: Optional[str],
    artifacts: Optional[str],
    questions: Optional[str],
    skills: Iterable[str],
) -> str:
    return (
        f"## Focus\n\n{focus.strip()}\n"
        + _section("Discovery context", context)
        + _section("Suggested approach", approach)
        + _section("Key artifacts", artifacts)
        + _section("Open questions / blockers", questions)
        + _list_section("Suggested skills", skills)
        + "\n"
    )


def create_handoff(
    focus: str,
    *,
    queue_base: Optional[Path] = None,
    repo: Optional[str] = None,
    remote: Optional[str] = None,
    base_branch: Optional[str] = None,
    suggested_skills: Optional[Iterable[str]] = None,
    context: Optional[str] = None,
    approach: Optional[str] = None,
    artifacts: Optional[str] = None,
    questions: Optional[str] = None,
    recommended_route: Optional[str] = None,
    implementation_intent: Optional[str] = None,
    change_kind: Optional[str] = None,
    target_spec: Optional[str] = None,
    triage: Optional[str] = None,
    blocked_by: Optional[Iterable[str]] = None,
    watch: Optional[Iterable[str]] = None,
    seeded_from: Optional[str] = None,
) -> dict[str, Any]:
    """Create one queued brief and auto-link high-confidence neighbours."""
    focus = focus.strip()
    if not focus:
        raise ValueError("focus must not be empty")
    if implementation_intent and implementation_intent not in {"requested", "planning-only", "unknown"}:
        raise ValueError("implementation-intent must be requested, planning-only, or unknown")
    if change_kind and change_kind not in {"new", "delta", "bugfix"}:
        raise ValueError("change-kind must be new, delta, or bugfix")
    if triage and triage not in work_queue.VALID_TRIAGE:
        raise ValueError("triage must be blocker or deferred")
    blocked_by_refs = _validate_blocked_by(blocked_by)

    base = Path(queue_base or work_queue.base_dir()).expanduser()
    queue = base / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now().astimezone()
    stem = f"{now:%Y%m%d-%H%M%S}-{_slugify(focus)}"
    path = queue / f"{stem}.md"
    suffix = 2
    while path.exists():
        path = queue / f"{stem}-{suffix}.md"
        suffix += 1

    skills = _clean_lines(suggested_skills)
    frontmatter: dict[str, Any] = {
        "id": path.stem,
        "created": now.isoformat(timespec="seconds"),
        "focus": _LiteralStr(focus),
        "repo": _normalize_repo(repo) or None,
        "remote": remote or None,
        "base-branch": base_branch or None,
        "status": "queued",
    }
    route = _route_for(focus, recommended_route)
    if skills:
        frontmatter["suggested-skills"] = skills
    if route:
        frontmatter["recommended-route"] = route
    for key, value in (
        ("implementation-intent", implementation_intent),
        ("change-kind", change_kind),
        ("target-spec", target_spec),
        ("triage", triage),
        ("seeded-from", seeded_from),
    ):
        if value:
            frontmatter[key] = value
    for key, values in (("blocked-by", blocked_by_refs), ("watch", watch)):
        cleaned = _clean_lines(values)
        if cleaned:
            frontmatter[key] = cleaned

    content = "---\n" + yaml.safe_dump(
        frontmatter, sort_keys=False, default_flow_style=False, allow_unicode=True
    ) + "---\n\n" + _brief_body(focus, context, approach, artifacts, questions, skills)
    path.write_text(content, encoding="utf-8")
    valid, error = validate_brief(path)
    if not valid:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"created brief failed validation: {error}")

    previous_env = os.environ.get("WORK_QUEUE_DIR")
    os.environ["WORK_QUEUE_DIR"] = str(base)
    try:
        scored = score_candidates.score_candidates(path, base)
        linked: list[str] = []
        for candidate in scored.get("auto_link", []):
            result = work_queue.link(path.stem, str(candidate["id"]))
            if result.get("status") == "linked":
                linked.append(str(candidate["id"]))
        return {
            "status": "created",
            "path": str(path),
            "id": path.stem,
            "recommended_route": route,
            "auto_linked": linked,
            "confirm": scored.get("confirm", []),
        }
    finally:
        if previous_env is None:
            os.environ.pop("WORK_QUEUE_DIR", None)
        else:
            os.environ["WORK_QUEUE_DIR"] = previous_env


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Create a Worktrail handoff brief")
    parser.add_argument("--focus", required=True, help="the deferred work to capture")
    parser.add_argument("--queue-dir", help="queue base containing queue/ and picked/")
    parser.add_argument("--repo")
    parser.add_argument("--remote")
    parser.add_argument("--base-branch")
    parser.add_argument("--suggested-skill", action="append", default=[])
    parser.add_argument("--context")
    parser.add_argument("--approach")
    parser.add_argument("--artifacts")
    parser.add_argument("--questions")
    parser.add_argument("--recommended-route")
    parser.add_argument("--implementation-intent", choices=("requested", "planning-only", "unknown"))
    parser.add_argument("--change-kind", choices=("new", "delta", "bugfix"))
    parser.add_argument("--target-spec")
    parser.add_argument(
        "--triage",
        choices=("blocker", "deferred"),
        help="release scoping: blocker = must land before the current release gate; "
        "deferred = explicitly scoped to a later release",
    )
    parser.add_argument("--blocked-by", action="append", default=[])
    parser.add_argument("--watch", action="append", default=[])
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    try:
        result = create_handoff(
            args.focus,
            queue_base=Path(args.queue_dir) if args.queue_dir else None,
            repo=args.repo,
            remote=args.remote,
            base_branch=args.base_branch,
            suggested_skills=args.suggested_skill,
            context=args.context,
            approach=args.approach,
            artifacts=args.artifacts,
            questions=args.questions,
            recommended_route=args.recommended_route,
            implementation_intent=args.implementation_intent,
            change_kind=args.change_kind,
            target_spec=args.target_spec,
            triage=args.triage,
            blocked_by=args.blocked_by,
            watch=args.watch,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result) if args.json else result["path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
