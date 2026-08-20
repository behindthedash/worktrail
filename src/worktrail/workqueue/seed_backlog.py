"""Proactively seed the work queue from backlog the drain cannot otherwise see.

`worktrail-go auto` only ever claims work-queue briefs. Two backlog shapes are
therefore invisible to unattended drains until a human notices them on the
dashboard and captures a brief by hand:

- **Specs that need tasks** — a spec folder in the `needs-tasks` dashboard
  stage (approved `spec.md`, no unresolved clarification markers, no task DAG).
  The next action is deterministic (`spec-to-tasks`), so a planning-only brief
  can be captured mechanically.
- **Epics with unspecced features** — an epic document under
  `docs/specs/epics/` whose `### Feature N` decomposition lists more features
  than there are specs citing the epic's id. Route B doctrine assigns feature
  spec ids only at Route C pickup ("do not spec all features up front"), so the
  gap is measured by citation count, not by diffing pre-assigned ids.

Each seeded brief carries a `seeded-from:` frontmatter key. A key that already
exists anywhere in the queue (queue/ or picked/, any status) is never seeded
again — for a spec the key is stable (`<repo>:spec:<id>`), so a seeded brief
that was claimed and finished without actually fixing the stage never loops;
for an epic the key embeds the citation count (`<repo>:epic:<id>:cited=<n>`),
so progress (a new citing spec) re-arms seeding for the next feature while a
finished-but-fruitless brief still terminates the sequence.

Epic seeding is additionally self-healing for stale bookkeeping: an epic whose
features all shipped but whose `**Status:**` line was never flipped seeds
exactly one reconcile brief, whose focus explicitly asks the picking session to
update the status line — after which the terminal status stops all seeding.

Deterministic and bounded: candidates are ordered (needs-tasks specs first,
then epics, each sorted by repo then id) and capped per sweep, with the dropped
count reported rather than silently truncated.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..router import dashboard
from ..router.policy import load_policy
from ..router.policy_selfcheck import discover_repo_names
from ..shared.brief_frontmatter import split_frontmatter
from .create_handoff import create_handoff
from . import work_queue

# One drain pass should top the queue up, not flood it: a first run against a
# backlog that accumulated for months must not convert the whole dashboard into
# briefs at once. Dropped candidates are reported (never silent) and the next
# sweep picks them up in the same deterministic order.
DEFAULT_MAX_SEEDS = 5


def resolve_spec_rel(repo: Path, spec_id: str) -> Optional[str]:
    """The spec's repo-relative path for whichever task format authored it.

    Same contract as `drain.drain.resolve_spec_rel` (kept here rather than
    imported: drain imports this module for its pre-loop seeding step, so an
    import in the other direction would be circular). None if neither location
    exists.
    """
    if (repo / "docs" / "specs" / spec_id).is_dir():
        return f"docs/specs/{spec_id}"
    if (repo / "openspec" / "changes" / spec_id).is_dir():
        return f"openspec/changes/{spec_id}"
    return None


def _base_branch_for(repo: Path) -> Optional[str]:
    try:
        return load_policy(repo).get("base_branch") or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Finders


def find_needs_tasks_specs(
    repos_root: Path, go_repo: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Every (repo, spec) pair in the `needs-tasks` dashboard stage.

    `needs-clarification` is deliberately excluded: its next action requires
    human answers, not a mechanical spec-to-tasks pass.
    """
    names = discover_repo_names(repos_root)
    if go_repo:
        names = [n for n in names if n == go_repo]
    found: List[Dict[str, Any]] = []
    for name in names:
        repo_path = repos_root / name
        rows = dashboard.scan(repo_path / "docs" / "specs")
        for row in sorted(rows, key=lambda r: str(r.get("id") or "")):
            if row.get("stage") != "needs-tasks":
                continue
            spec_id = row.get("id")
            if not spec_id:
                continue
            spec_rel = resolve_spec_rel(repo_path, spec_id)
            if spec_rel is None:
                continue
            found.append({
                "kind": "needs-tasks",
                "repo": repo_path, "repo_name": name,
                "id": spec_id, "spec_rel": spec_rel,
                "seed_key": f"{name}:spec:{spec_id}",
            })
    return found


def find_ready_specs(
    repos_root: Path, go_repo: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Every (repo, spec) pair in the `ready-to-implement` dashboard stage, for
    repos that have opted into Route D implementation seeding.

    Gated per repo by `load_policy(repo)["allow_seeded_implementation"]`: a
    repo that hasn't opted in never has its `ready-to-implement` specs scanned
    at all, let alone seeded.
    """
    names = discover_repo_names(repos_root)
    if go_repo:
        names = [n for n in names if n == go_repo]
    found: List[Dict[str, Any]] = []
    for name in names:
        repo_path = repos_root / name
        if not load_policy(repo_path).get("allow_seeded_implementation"):
            continue
        rows = dashboard.scan(repo_path / "docs" / "specs")
        for row in sorted(rows, key=lambda r: str(r.get("id") or "")):
            if row.get("stage") != "ready-to-implement":
                continue
            spec_id = row.get("id")
            if not spec_id:
                continue
            spec_rel = resolve_spec_rel(repo_path, spec_id)
            if spec_rel is None:
                continue
            found.append({
                "kind": "ready-to-implement",
                "repo": repo_path, "repo_name": name,
                "id": spec_id, "spec_rel": spec_rel,
                "seed_key": f"{name}:impl:{spec_id}",
            })
    return found


def find_epic_gaps(
    repos_root: Path, go_repo: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Every epic under `docs/specs/epics/` with fewer citing specs than
    decomposed `### Feature` headings and a non-terminal `**Status:**`.

    Delegates per-epic classification to the dashboard's shared
    `dashboard.scan_epics()`/`detect_epic_stage()` so this module carries no
    duplicated status/feature-count/citation parsing of its own.

    An epic file with no parseable feature decomposition is skipped and
    reported (`unparseable: True`) rather than seeded -- with no feature count
    there is no terminal condition, and seeding it would loop forever. An
    unreadable epic file (dashboard stage `error`) is skipped silently, same
    as before this module delegated to the dashboard's scan.
    """
    names = discover_repo_names(repos_root)
    if go_repo:
        names = [n for n in names if n == go_repo]
    found: List[Dict[str, Any]] = []
    for name in names:
        repo_path = repos_root / name
        for row in dashboard.scan_epics(repo_path):
            stage = row["stage"]
            if stage in ("epic-complete", "error"):
                continue
            epic_id = row["id"]
            if stage == "epic-unparseable":
                found.append({
                    "kind": "epic", "repo": repo_path, "repo_name": name,
                    "id": epic_id, "unparseable": True,
                })
                continue
            cited = row["citing_specs"]
            found.append({
                "kind": "epic",
                "repo": repo_path, "repo_name": name,
                "id": epic_id,
                "features": row["features"],
                "cited": row["cited"],
                "citing_specs": cited,
                "seed_key": f"{name}:epic:{epic_id}:cited={row['cited']}",
            })
    return found


# ---------------------------------------------------------------------------
# Dedup


def existing_seed_keys(queue_base: Path) -> set:
    """Every `seeded-from:` value present anywhere in the queue.

    Both queue/ and picked/ count, regardless of status: a done brief with the
    same key means this exact backlog state was already worked once, and
    re-seeding it would loop a one-shot per drain against a stage the previous
    one-shot failed to clear (that failure needs a human, not a retry storm).
    """
    keys: set = set()
    for sub in ("queue", "picked"):
        d = queue_base / sub
        if not d.is_dir():
            continue
        for md in d.glob("*.md"):
            try:
                fm, _ = split_frontmatter(md.read_text(encoding="utf-8"))
            except OSError:
                continue
            value = fm.get("seeded-from")
            if value:
                keys.add(str(value))
    return keys


# ---------------------------------------------------------------------------
# Brief synthesis


def _needs_tasks_brief_kwargs(finding: Dict[str, Any]) -> Dict[str, Any]:
    spec_id, repo_name = finding["id"], finding["repo_name"]
    return {
        "focus": (
            f"Spec {spec_id} in {repo_name} has an approved spec but no task DAG "
            f"(dashboard stage: needs-tasks). Run spec-to-tasks against the "
            f"existing spec at {finding['spec_rel']} — do not re-author the spec "
            f"itself."
        ),
        "context": (
            f"Seeded automatically by the backlog seeder: the dashboard scan "
            f"reported stage=needs-tasks for {finding['spec_rel']}. The spec "
            f"has no unresolved [NEEDS CLARIFICATION] markers, so task "
            f"generation is mechanical."
        ),
        "recommended_route": "C",
        "implementation_intent": "planning-only",
        "target_spec": spec_id,
    }


def _ready_brief_kwargs(finding: Dict[str, Any]) -> Dict[str, Any]:
    spec_id, repo_name = finding["id"], finding["repo_name"]
    return {
        "focus": (
            f"Spec {spec_id} in {repo_name} is ready to implement (dashboard "
            f"stage: ready-to-implement). Run the orchestrator against its "
            f"existing, complete task DAG at {finding['spec_rel']} — do not "
            f"re-plan or re-author the tasks."
        ),
        "context": (
            f"Seeded automatically by the backlog seeder: the dashboard scan "
            f"reported stage=ready-to-implement for {finding['spec_rel']}. "
            f"The spec's task DAG is already complete, so this is an "
            f"implementation pass, not a planning pass."
        ),
        "recommended_route": "D",
        "implementation_intent": "requested",
        "target_spec": spec_id,
    }


def _epic_brief_kwargs(finding: Dict[str, Any]) -> Dict[str, Any]:
    epic_id, repo_name = finding["id"], finding["repo_name"]
    cited = finding["cited"]
    features = finding["features"]
    citing = ", ".join(finding["citing_specs"]) or "none"
    return {
        "focus": (
            f"Epic {epic_id} in {repo_name} decomposes into {features} feature(s) "
            f"but only {cited} spec(s) cite it. Spec the next unspecced feature "
            f"from its decomposition (docs/specs/epics/{epic_id}.md) via the "
            f"feature-planning pipeline. If every feature is in fact already "
            f"specced or delivered, update the epic's **Status:** line to a "
            f"terminal value (e.g. Completed) instead, so it stops re-seeding."
        ),
        "context": (
            f"Seeded automatically by the backlog seeder. Feature headings "
            f"counted: {features}. Specs citing the epic id: {citing}. Route B "
            f"doctrine assigns feature spec ids at Route C pickup, so pick the "
            f"next feature in the epic's own sequencing order."
        ),
        "recommended_route": "C",
        "implementation_intent": "planning-only",
    }


# ---------------------------------------------------------------------------
# Seeder


def seed_backlog(
    repos_root: Path,
    go_repo: Optional[str] = None,
    queue_base: Optional[Path] = None,
    max_seeds: int = DEFAULT_MAX_SEEDS,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> Dict[str, Any]:
    """Find unseeded backlog and capture up to `max_seeds` briefs for it.

    Returns a summary dict: `seeded` (one entry per created brief),
    `skipped_existing` (candidates whose seed key already exists in the
    queue), `dropped_over_cap` (candidates deferred to the next sweep),
    and `unparseable_epics` (epic files with no feature decomposition).
    """
    queue_base = Path(queue_base or work_queue.base_dir()).expanduser()
    candidates = find_needs_tasks_specs(repos_root, go_repo)
    epic_findings = find_epic_gaps(repos_root, go_repo)
    unparseable = [f for f in epic_findings if f.get("unparseable")]
    candidates += [f for f in epic_findings if not f.get("unparseable")]
    candidates += find_ready_specs(repos_root, go_repo)
    for finding in unparseable:
        log(f"seed-backlog: skipping epic {finding['repo_name']} "
            f"{finding['id']}: no '### Feature' decomposition headings found")

    known = existing_seed_keys(queue_base)
    fresh = [c for c in candidates if c["seed_key"] not in known]
    skipped_existing = len(candidates) - len(fresh)
    to_seed = fresh[:max_seeds] if max_seeds else fresh
    dropped = len(fresh) - len(to_seed)
    if dropped:
        log(f"seed-backlog: capped at {max_seeds} seeds this sweep; "
            f"{dropped} candidate(s) deferred to the next sweep")

    seeded: List[Dict[str, Any]] = []
    for finding in to_seed:
        kwargs = (_needs_tasks_brief_kwargs(finding)
                  if finding["kind"] == "needs-tasks"
                  else _ready_brief_kwargs(finding)
                  if finding["kind"] == "ready-to-implement"
                  else _epic_brief_kwargs(finding))
        entry = {
            "kind": finding["kind"], "repo": finding["repo_name"],
            "id": finding["id"], "seed_key": finding["seed_key"],
        }
        if dry_run:
            log(f"seed-backlog dry-run: would seed {finding['seed_key']}")
            seeded.append({**entry, "brief_id": None})
            continue
        try:
            result = create_handoff(
                queue_base=queue_base,
                repo=finding["repo_name"],
                base_branch=_base_branch_for(finding["repo"]),
                seeded_from=finding["seed_key"],
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001 -- one candidate must not
            #                        block the rest of the sweep
            log(f"seed-backlog error: {finding['seed_key']}: {exc}")
            continue
        log(f"seed-backlog: seeded {finding['seed_key']} -> {result['id']}")
        seeded.append({**entry, "brief_id": result["id"]})
    return {
        "seeded": seeded,
        "skipped_existing": skipped_existing,
        "dropped_over_cap": dropped,
        "unparseable_epics": [
            {"repo": f["repo_name"], "id": f["id"]} for f in unparseable
        ],
    }


# ---------------------------------------------------------------------------
# CLI


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed the work queue from needs-tasks specs and "
                    "under-specced epics.")
    parser.add_argument("--repos-root", type=Path, default=Path.home() / "projects")
    parser.add_argument("--repo", default=None,
                        help="restrict seeding to one repo name")
    parser.add_argument("--queue-dir", type=Path, default=None,
                        help="queue base containing queue/ and picked/ "
                             "(default: $WORK_QUEUE_DIR)")
    parser.add_argument("--max-seeds", type=int, default=DEFAULT_MAX_SEEDS,
                        help=f"cap per sweep (default {DEFAULT_MAX_SEEDS}; 0 = unlimited)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be seeded, create nothing")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    if not args.repos_root.is_dir():
        print(f"error: repos root {args.repos_root} does not exist", file=sys.stderr)
        return 2
    summary = seed_backlog(
        args.repos_root, args.repo, queue_base=args.queue_dir,
        max_seeds=args.max_seeds, dry_run=args.dry_run,
        log=(lambda _msg: None) if args.as_json else print,
    )
    if args.as_json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"seeded {len(summary['seeded'])}, "
              f"skipped {summary['skipped_existing']} existing, "
              f"deferred {summary['dropped_over_cap']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
