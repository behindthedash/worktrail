"""Create and validate a Worktrail handoff brief."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

from ..router.classify import classify
from ..router.cluster_detect import OVERLAP_THRESHOLD, _overlap_coefficient, _tokenize
from ..shared.brief_frontmatter import is_canonical_style, serialize_frontmatter, validate_brief
from . import score_candidates
from . import work_queue
from .work_queue import normalize_dependency_reference


_SLUG_MAX_CHARS = 60

# Wall-clock budget for the capture-time overlap scan's `gh pr list` call.
# A hang here must never delay (or fail) a brief capture, so the call is
# bounded and every bad outcome degrades to "no PR titles".
_OVERLAP_SCAN_GH_TIMEOUT_SECONDS = 5

# Cap on overlap candidates surfaced per capture -- enough to name every
# plausible duplicate without burying the capturer in near-misses.
_OVERLAP_WARNING_LIMIT = 5


def _slugify(focus: str) -> str:
    # Strip a trailing possessive "'s" from each word before tokenizing, so
    # "md's" yields the single word "md" instead of splitting into "md" and
    # a stray "s" that burns a slot in the word-count budget below.
    text = re.sub(r"(?<=[a-z0-9])'s(?=\s|$)", "", focus.lower())
    words = [w for w in re.findall(r"[a-z0-9]+", text) if len(w) > 1][:5]
    slug = "-".join(words) or "handoff"
    return slug[:_SLUG_MAX_CHARS].rstrip("-") or "handoff"


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


def _validate_target_task(value: Optional[str]) -> Optional[str]:
    """Return a trimmed target-task reference, rejecting empty or comma-joined values."""
    if not value:
        return None
    try:
        return normalize_dependency_reference(value)
    except ValueError as exc:
        message = str(exc)
        if "comma-joined" in message:
            raise ValueError(
                "target-task accepts exactly one task reference; it must not be comma-joined"
            ) from exc
        raise ValueError("target-task must be a non-empty task reference") from exc


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


_FOCUS_REPO_PREFIX = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*):\s")


def _infer_repo_from_focus(focus: str) -> Optional[str]:
    """Infer the repo from a leading `<project>: ` focus prefix.

    Briefs captured outside a checkout (a workspace-rooted /go session) carry
    `repo: null` even when the focus itself names the project ("datalena: add
    a CI guard ..."). A null repo makes the brief invisible to same-repo batch
    detection, so a whole cluster of near-identical briefs gets worked one PR
    and one CI run at a time. Only a token that resolves to an existing
    `~/projects/<name>` directory is accepted -- anything else stays null
    rather than guessing.
    """
    match = _FOCUS_REPO_PREFIX.match(focus)
    if not match:
        return None
    candidate = Path.home() / "projects" / match.group(1)
    if candidate.is_dir():
        return str(candidate.resolve())
    return None


def _subdirectory_names(base: Path) -> list[str]:
    """Sorted subdirectory names under `base`; [] when absent or unreadable."""
    try:
        return sorted(entry.name for entry in base.iterdir() if entry.is_dir())
    except OSError:
        return []


def _open_pr_titles(remote: Optional[str]) -> list[str]:
    """Open PR titles for `remote`, via a short-timeout `gh pr list` call.

    Silently skipped when unavailable -- a null/empty remote, missing gh
    binary, nonzero exit, timeout, or unparseable JSON each degrade to an
    empty list so the capture-time overlap scan never blocks on it.
    """
    if not remote:
        return []
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                str(remote),
                "--state",
                "open",
                "--json",
                "title,number",
            ],
            capture_output=True,
            text=True,
            timeout=_OVERLAP_SCAN_GH_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    try:
        entries = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(entries, list):
        return []
    titles: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if title:
            titles.append(title)
    return titles


def _scan_durable_artifact_overlaps(
    focus: str,
    repo_path: Optional[str],
    remote: Optional[str],
) -> list[dict[str, Any]]:
    """Durable artifacts overlapping the focus at/above OVERLAP_THRESHOLD.

    Capture-time dedup advisory (layer 3 of add-durable-artifact-dedup-gate),
    run before writing a new brief. Scans three durable-artifact surfaces for
    candidates: `<repo>/docs/specs/*/` slugs, `<repo>/openspec/changes/*/`
    change names, and open PR titles for `remote`. Focus and candidate text
    are compared with router/cluster_detect's tokenization and overlap
    coefficient at its OVERLAP_THRESHOLD -- imported, not duplicated, so the
    capture-time warning and consume-time cluster detection agree on what
    "overlapping" means.

    Returns hits best-first (score descending, label ascending on ties) as
    `{"kind": "spec-slug"|"openspec-change"|"open-pr", "label", "score"}`.
    Purely advisory and fail-open: no repo path, an unresolvable/unreadable
    path, or any `gh` failure simply yields fewer or zero hits -- never an
    exception.
    """
    tokens = _tokenize(focus)
    if not tokens:
        return []
    candidates: list[tuple[str, str]] = []
    if repo_path:
        root = Path(repo_path).expanduser()
        if root.is_dir():
            candidates.extend(
                ("spec-slug", name)
                for name in _subdirectory_names(root / "docs" / "specs")
            )
            candidates.extend(
                ("openspec-change", name)
                for name in _subdirectory_names(root / "openspec" / "changes")
            )
    for title in _open_pr_titles(remote):
        candidates.append(("open-pr", title))
    hits: list[dict[str, Any]] = []
    for kind, label in candidates:
        score = _overlap_coefficient(tokens, _tokenize(label))
        if score >= OVERLAP_THRESHOLD:
            hits.append({"kind": kind, "label": label, "score": score})
    return sorted(hits, key=lambda hit: (-hit["score"], hit["label"]))


def _format_overlap_warning(warning: dict[str, Any]) -> str:
    """One human-readable stderr line for an overlap candidate."""
    return (
        f"overlap warning: [{warning['kind']}] {warning['label']} "
        f"(score {warning['score']:.2f})"
    )


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
    context: Optional[str],
    approach: Optional[str],
    artifacts: Optional[str],
    questions: Optional[str],
    skills: Iterable[str],
) -> str:
    return (
        _section("Discovery context", context)
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
    target_task: Optional[str] = None,
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
    target_task = _validate_target_task(target_task)

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
    resolved_repo = _normalize_repo(repo) or _infer_repo_from_focus(focus) or None
    frontmatter: dict[str, Any] = {
        "id": path.stem,
        "created": now.isoformat(timespec="seconds"),
        "focus": focus,
        "repo": resolved_repo,
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
        ("target-task", target_task),
        ("triage", triage),
        ("seeded-from", seeded_from),
    ):
        if value:
            frontmatter[key] = value
    for key, values in (("blocked-by", blocked_by_refs), ("watch", watch)):
        cleaned = _clean_lines(values)
        if cleaned:
            frontmatter[key] = cleaned

    # Layer 3 capture-time dedup advisory (add-durable-artifact-dedup-gate):
    # scan durable artifacts -- spec slugs, OpenSpec change names, open PR
    # titles -- for focus overlap before the brief is written, using the same
    # resolved repo path the frontmatter records. Purely advisory: the scan
    # itself fails open, and any unexpected error is swallowed here too --
    # warnings are reported but never block or fail the capture.
    try:
        overlap_hits = _scan_durable_artifact_overlaps(focus, resolved_repo, remote or None)
    except Exception:
        overlap_hits = []
    overlap_warnings = overlap_hits[:_OVERLAP_WARNING_LIMIT]

    content = (
        "---\n"
        + serialize_frontmatter(frontmatter)
        + "---\n\n"
        + _brief_body(context, approach, artifacts, questions, skills)
    )
    path.write_text(content, encoding="utf-8")
    valid, error = validate_brief(path)
    if not valid:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"created brief failed validation: {error}")
    if not is_canonical_style(content):
        path.unlink(missing_ok=True)
        raise RuntimeError("created brief did not serialize to canonical frontmatter style")

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
            "overlap_warnings": overlap_warnings,
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
    parser.add_argument("--target-task")
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
            target_task=args.target_task,
            triage=args.triage,
            blocked_by=args.blocked_by,
            watch=args.watch,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not args.json:
        # Human mode: surface overlap candidates on stderr without blocking --
        # the capture itself still succeeds and exits zero. JSON consumers get
        # the same candidates in-band as "overlap_warnings".
        for warning in result.get("overlap_warnings", []):
            print(_format_overlap_warning(warning), file=sys.stderr)
    print(json.dumps(result) if args.json else result["path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
