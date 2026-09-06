"""Create and validate a Worktrail handoff brief."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..orchestrator.spawnlib import spawn_agent
from ..router.classify import classify
from ..router.cluster_detect import OVERLAP_THRESHOLD, _overlap_coefficient, _tokenize
from ..router.policy import load_policy, resolve_routing
from ..shared.brief_frontmatter import (
    is_canonical_style,
    serialize_frontmatter,
    validate_brief,
)
from . import repo_inference, score_candidates, work_queue
from .slug import fallback_slugify
from .work_queue import normalize_dependency_reference

# Wall-clock budget for the capture-time overlap scan's `gh pr list` call.
# A hang here must never delay (or fail) a brief capture, so the call is
# bounded and every bad outcome degrades to "no PR titles".
_OVERLAP_SCAN_GH_TIMEOUT_SECONDS = 5

# Cap on overlap candidates surfaced per capture -- enough to name every
# plausible duplicate without burying the capturer in near-misses.
_OVERLAP_WARNING_LIMIT = 5


def _slugify(text: str) -> str:
    return fallback_slugify(text, default="handoff")


def _semantic_slug_summary(focus: str, repo: str | None) -> str | None:
    """Best-effort concise summary from a provider-backed backend.

    Returns None when no usable backend is configured or when the spawn fails
    for any reason, letting capture fall back to the deterministic slug.
    """
    if not repo:
        return None
    repo_path = Path(repo).expanduser()
    if not repo_path.is_dir():
        return None
    try:
        policy = load_policy(repo_path)
        routing = resolve_routing(policy)
    except Exception:  # noqa: BLE001
        return None
    tier = routing.get("default_tier")
    if not tier:
        return None
    prompt = (
        "Summarize the underlying issue in 3 to 5 lowercase words for a "
        "filename slug. Return only the phrase, with no punctuation, code "
        "fences, labels, or explanation.\n\n"
        f"Issue:\n{focus}\n"
    )
    try:
        result = spawn_agent(
            prompt,
            cwd=repo_path,
            tier=tier,
            timeout=20,
            retries=0,
            session_limit_waits=0,
        )
    except Exception:  # noqa: BLE001
        return None
    if getattr(result, "exhausted", False):
        return None
    summary = result.text.strip()
    return summary or None


def _clean_lines(values: Iterable[str] | None) -> list[str]:
    return [value.strip() for value in (values or []) if value and value.strip()]


def _validate_blocked_by(values: Iterable[str] | None) -> list[str]:
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
            raise ValueError(
                "blocked-by values must be non-empty dependency references"
            ) from exc
    return cleaned


def _validate_target_task(value: str | None) -> str | None:
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


def _normalize_repo(repo: str | None) -> str | None:
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


def _infer_repo_from_focus(focus: str) -> str | None:
    """Infer the repo a brief's `focus` refers to.

    Briefs captured outside a checkout (a workspace-rooted /go session) carry
    `repo: null` even when the focus itself names the project ("datalena: add
    a CI guard ..."). A null repo makes the brief invisible to same-repo batch
    detection, so a whole cluster of near-identical briefs gets worked one PR
    and one CI run at a time. Delegates to `repo_inference.infer_repo`, which
    only resolves an unambiguous match -- anything else stays null rather
    than guessing.

    Falls back to a bare leading `<project>: ` prefix match against
    `~/projects/<name>` when that delegation finds nothing:
    `repo_inference` only recognizes checkouts with a `.git` entry, while a
    plain project directory (no `.git`, e.g. not yet initialized) still
    names an unambiguous local project via this prefix. The fallback only
    fires when no rule matched at all (`rule is None`) -- when a rule did
    match but stayed ambiguous (two or more candidates), that deliberate
    refusal to guess must not be overridden by the prefix fallback.
    """
    result = repo_inference.infer_repo(focus)
    if result.repo:
        return result.repo
    if result.rule is not None:
        return None
    match = _FOCUS_REPO_PREFIX.match(focus) if focus else None
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


def _open_pr_titles(remote: str | None) -> list[str]:
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
            check=False,
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
    repo_path: str | None,
    remote: str | None,
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


def _section(title: str, value: str | None) -> str:
    value = (value or "").strip()
    return f"\n## {title}\n\n{value}\n" if value else ""


def _list_section(title: str, values: Iterable[str]) -> str:
    lines = _clean_lines(values)
    return (
        f"\n## {title}\n\n" + "\n".join(f"- {line}" for line in lines) + "\n"
        if lines
        else ""
    )


def _route_for(focus: str, requested: str | None) -> str | None:
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
    context: str | None,
    approach: str | None,
    artifacts: str | None,
    questions: str | None,
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
    queue_base: Path | None = None,
    repo: str | None = None,
    remote: str | None = None,
    base_branch: str | None = None,
    suggested_skills: Iterable[str] | None = None,
    context: str | None = None,
    approach: str | None = None,
    artifacts: str | None = None,
    questions: str | None = None,
    recommended_route: str | None = None,
    implementation_intent: str | None = None,
    change_kind: str | None = None,
    target_spec: str | None = None,
    target_task: str | None = None,
    triage: str | None = None,
    blocked_by: Iterable[str] | None = None,
    watch: Iterable[str] | None = None,
    seeded_from: str | None = None,
) -> dict[str, Any]:
    """Create one queued brief and auto-link high-confidence neighbours."""
    focus = focus.strip()
    if not focus:
        raise ValueError("focus must not be empty")
    if implementation_intent and implementation_intent not in {
        "requested",
        "planning-only",
        "unknown",
    }:
        raise ValueError(
            "implementation-intent must be requested, planning-only, or unknown"
        )
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
    skills = _clean_lines(suggested_skills)
    resolved_repo = _normalize_repo(repo) or _infer_repo_from_focus(focus) or None
    summary = _semantic_slug_summary(focus, resolved_repo)
    stem = f"{now:%Y%m%d-%H%M%S}-{_slugify(summary or focus)}"
    path = queue / f"{stem}.md"
    suffix = 2
    while path.exists():
        path = queue / f"{stem}-{suffix}.md"
        suffix += 1
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
        overlap_hits = _scan_durable_artifact_overlaps(
            focus, resolved_repo, remote or None
        )
    except Exception:  # noqa: BLE001
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
        raise RuntimeError(
            "created brief did not serialize to canonical frontmatter style"
        )

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


def check_duplicate(
    focus: str,
    *,
    queue_base: Path | None = None,
    repo: str | None = None,
    context: str | None = None,
    approach: str | None = None,
    artifacts: str | None = None,
    questions: str | None = None,
    suggested_skills: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Pre-write brief-vs-brief duplicate check: is there already a same-repo,
    high-confidence match for this not-yet-captured focus?

    The brief-vs-brief overlap check (`score_candidates.score_candidates`)
    previously only ever ran AFTER the new brief file was written
    (`create_handoff`'s own post-write `auto_link`/`confirm` scoring), so a
    high-confidence match could only be auto-linked or listed for confirmation
    post-hoc -- never redirected before a duplicate file existed. This runs
    the identical scoring (same tokenizer, same repo/overlap weighting, same
    `HIGH_CONFIDENCE` bar as the post-write `auto_link` tier) against the
    same focus/body content `create_handoff` would otherwise write, before
    anything is written.

    Returns `{"match": {"path", "id", "focus", "total_score"} | None}`. The
    caller (an interactive skill) presents a three-way choice on a match:
    confirm-as-duplicate (append via `append_duplicate_signal` instead of
    creating), reject-as-false-positive (create normally, then
    `work_queue.link()` for traceability), or no human present (create
    normally, unchanged from today's behavior).
    """
    focus = focus.strip()
    base = Path(queue_base or work_queue.base_dir()).expanduser()
    resolved_repo = _normalize_repo(repo) or _infer_repo_from_focus(focus) or None
    body = _brief_body(
        context, approach, artifacts, questions, _clean_lines(suggested_skills)
    )
    match = score_candidates.precheck_duplicate(focus, body, resolved_repo, base)
    return {"match": match}


def append_duplicate_signal(
    matched_path: str | Path,
    focus: str,
    *,
    context: str | None = None,
) -> dict[str, Any]:
    """Append the new capture's focus/context onto an existing matched brief
    as a dated `## Additional signal (potential duplicate)` section, in place
    of writing a new brief file for it.

    Mirrors `work_queue.done()`'s own `## Closure Note` append pattern: the
    existing brief's frontmatter and status are untouched, only its body
    grows a new dated section. Returns `{"status": "appended", "id", "path"}`
    on success, or `{"status": "not-found"|"error", "error": ...}` otherwise
    -- never raises.
    """
    path = Path(matched_path)
    try:
        original = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"status": "not-found", "error": str(exc)}

    now = dt.datetime.now().astimezone()
    lines = [
        "## Additional signal (potential duplicate)",
        "",
        f"Captured: {now.isoformat(timespec='seconds')}",
        "",
        focus.strip(),
    ]
    if context and context.strip():
        lines += ["", context.strip()]
    section = "\n" + "\n".join(lines) + "\n"

    try:
        if not original.endswith("\n"):
            original += "\n"
        path.write_text(original + section, encoding="utf-8")
    except OSError as exc:
        return {"status": "error", "error": str(exc)}

    return {"status": "appended", "id": path.stem, "path": str(path)}


def main(argv: list[str] | None = None) -> int:
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
    parser.add_argument(
        "--implementation-intent", choices=("requested", "planning-only", "unknown")
    )
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


def main_check_duplicate(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pre-write check: is there already a high-confidence duplicate brief?"
    )
    parser.add_argument("--focus", required=True, help="the deferred work to capture")
    parser.add_argument("--queue-dir", help="queue base containing queue/ and picked/")
    parser.add_argument("--repo")
    parser.add_argument("--context")
    parser.add_argument("--approach")
    parser.add_argument("--artifacts")
    parser.add_argument("--questions")
    parser.add_argument("--suggested-skill", action="append", default=[])
    args = parser.parse_args(argv)
    result = check_duplicate(
        args.focus,
        queue_base=Path(args.queue_dir) if args.queue_dir else None,
        repo=args.repo,
        context=args.context,
        approach=args.approach,
        artifacts=args.artifacts,
        questions=args.questions,
        suggested_skills=args.suggested_skill,
    )
    print(json.dumps(result))
    return 0


def main_append_duplicate_signal(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Append a new capture's focus/context onto an existing matched brief"
    )
    parser.add_argument("--path", required=True, help="matched brief's path")
    parser.add_argument("--focus", required=True, help="the new capture's focus text")
    parser.add_argument("--context")
    args = parser.parse_args(argv)
    result = append_duplicate_signal(args.path, args.focus, context=args.context)
    print(json.dumps(result))
    return 0 if result.get("status") == "appended" else 1


if __name__ == "__main__":
    raise SystemExit(main())
