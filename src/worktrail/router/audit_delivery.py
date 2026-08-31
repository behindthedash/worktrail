#!/usr/bin/env python3
"""audit_delivery.py — fleet-wide retroactive delivery audit.

`integrate.py`'s `detect_unreconciled_evidence()` closes the delivery-ledger gap
(brief 20260815-115257, PR #422) going forward: on every run, it flags a DONE
task whose worktree HEAD never reached the base branch. But it requires the
task worktree to still exist (`if not wt.is_dir(): continue`), and worktrees
are torn down after merge — so it cannot be pointed at history. This module
answers the same question retroactively, for every *already-finished* run: is
there a task this run's own journal recorded as reviewed-PASSED whose commit
never landed on the base branch?

Source of truth: each orchestrator run's on-disk journal
(`<repo>-worktrees/run-*.json`, written by `orchestrator/live.py`), not the
per-dispatch run records under `~/.worktrail/runs` (those carry route/risk/PR
metadata, never a per-task `head_sha`). For each task, the last `role:
"review"` entry with `report.review_status == "PASSED"` is the same
reviewed-PASSED signal the live incident (PR #419 dropping task 1.3) turned
on — its `report.head_sha` is what should be an ancestor of the base branch.

A finding is only ever reported as `confirmed_dropped` when the commit object
is verifiably present in the repo's object store and verifiably not an
ancestor of the base branch. When the object itself is gone (evicted by `git
gc`), the task is `unverifiable` — this is a deliberate false-negative bias:
absence of proof is never presented as proof of a drop.

The dominant "not an ancestor" case in practice is not a drop at all: the
group-integration squash-merge (`integrate.py`) rewrites every task's commit
into a new SHA, so the task's own original commit is *never* an ancestor of
base even when its content shipped cleanly. `content_delivered_via_rewrite()`
rules this out before anything is reported `confirmed_dropped`: if every file
the task's commit touched has, at some commit on the base branch, the exact
same blob content, the finding moves to `content_delivered_via_rewrite`
instead — content-verified delivery under a different SHA.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

__all__ = [
    "MIN_LINE_MATCH_RATIO",
    "audit_repo",
    "content_delivered_via_rewrite",
    "discover_journals",
    "extract_passed_tasks",
    "identifiers_survive_elsewhere",
    "load_journal",
    "main",
    "resolve_base_ref",
    "resolve_canonical_repo",
    "shippable_files",
    "touched_files",
    "verify_delivery",
]

# How much of a task's added, non-blank lines must still be literally present
# on the base branch for `_file_content_on_base` to call the file delivered.
# Not 1.0: a delivered task's file routinely gets a later incidental touch-up
# (a follow-up commit rewording one assertion, a formatter pass) without the
# original commit having been dropped -- see `_file_content_on_base`.
MIN_LINE_MATCH_RATIO = 0.9


def _git(repo: Path, *args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def discover_journals(worktrees_dir: Path) -> list[Path]:
    """Every `run-*.json` candidate anywhere under `worktrees_dir`.

    Journals are not only direct children: a spec's own `new`/`modify`
    pipeline run gets its own nested `<slug>-worktrees/run-<slug>.json`
    (one level down from the repo's top-level `<repo>-worktrees/`), so this
    walks the whole tree (`rglob`), not just the top level -- a non-recursive
    scan silently misses every per-spec run, including the exact
    `run-auto-dod-verification.json` journal the brief itself cites as the
    incident this tool exists to retroactively catch.

    `.status.json` (live-poll status, no `entries`) and `.prior.json`
    (superseded snapshot) siblings are excluded by name — same convention
    `live.py` itself writes. `conductor/compile.py`'s cached RunPlan cache
    (`*/runplans/run-<slug>-<fingerprint>.json`) coincidentally matches the
    same `run-*.json` glob but carries no `entries` key at all; `load_journal`
    filters those out by schema rather than by a `runplans/`-path special
    case, so this stays correct even if that cache directory is renamed.
    """
    if not worktrees_dir.is_dir():
        return []
    return sorted(
        p
        for p in worktrees_dir.rglob("run-*.json")
        if not p.name.endswith(".status.json") and not p.name.endswith(".prior.json")
    )


def load_journal(path: Path) -> dict[str, Any] | None:
    """Parse one journal file. `None` on any read/parse failure, or when the
    parsed dict carries no `entries` list at all — not every `run-*.json`
    under a worktrees tree is an orchestrator journal (see `discover_journals`
    on the RunPlan-cache lookalike); a corrupt or half-written journal is
    likewise skipped, never treated as evidence of anything."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return None
    return data


def extract_passed_tasks(journal: dict[str, Any]) -> list[dict[str, Any]]:
    """The last `role: "review"` entry with `report.review_status == "PASSED"`
    per task — the exact "reviewed-PASSED, journal-done" signal that PR #419's
    dropped task 1.3 satisfied. Entries are journal-ordered (append-only), so
    the last matching entry per task is its latest review verdict.

    Returns `[{"task": task_id, "head_sha": sha, "started_at": ts}, ...]`.
    A task with no PASSED review entry (never reviewed, or last review
    FAILED) carries no delivery obligation from this signal and is omitted —
    a known scope limit, not a claim it is safe.
    """
    latest: dict[str, dict[str, Any]] = {}
    for entry in journal.get("entries") or []:
        if not isinstance(entry, dict) or entry.get("role") != "review":
            continue
        report = entry.get("report") or {}
        if report.get("review_status") != "PASSED":
            continue
        task = entry.get("task")
        sha = report.get("head_sha")
        if not task or not sha:
            continue
        latest[task] = {
            "task": task,
            "head_sha": sha,
            "started_at": entry.get("started_at"),
        }
    return sorted(latest.values(), key=lambda e: e["task"])


def resolve_canonical_repo(worktrees_dir: Path) -> Path:
    """The canonical checkout sibling of a `<repo>-worktrees` directory."""
    name = worktrees_dir.name
    if name.endswith("-worktrees"):
        name = name[: -len("-worktrees")]
    return worktrees_dir.parent / name


def resolve_base_ref(repo: Path) -> str | None:
    """`origin/<HEAD-branch>` for `repo`, or `None` if it can't be determined
    (no remote, offline, not a git repo)."""
    result = _git(repo, "remote", "show", "origin")
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("HEAD branch:"):
            branch = line.split(":", 1)[1].strip()
            if branch and branch != "(unknown)":
                return f"origin/{branch}"
    return None


def _object_present(repo: Path, sha: str) -> bool:
    return _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def _is_ancestor(repo: Path, sha: str, base_ref: str) -> bool:
    return (
        _git(repo, "merge-base", "--is-ancestor", sha, base_ref).returncode == 0
    )


def touched_files(repo: Path, sha: str) -> list[str]:
    """Files `sha`'s own commit touched, or `[]` if the object is gone or the
    diff can't be computed — never raises."""
    result = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha)
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


# Paths this repo's own SDD artifact policy (`worktrail-sdd-workflow/SKILL.md`
# "Artifact policy": "gitignore point-in-time scratch (reviews, run scratch)")
# never expects on the base branch in the first place. A task whose only
# touched paths fall here was never going to land on base regardless of
# whether it was ever reviewed-PASSED -- flagging it as dropped would be
# reporting policy-as-usual as a defect.
NEVER_SHIPPED_PATH_MARKERS = ("/reviews/",)


def _never_shipped(path: str) -> bool:
    return path.endswith(".compile-ok") or any(
        marker in f"/{path}" for marker in NEVER_SHIPPED_PATH_MARKERS
    )


def shippable_files(files: list[str]) -> list[str]:
    """`files` minus any path this repo's own artifact policy never commits
    to the base branch (see `NEVER_SHIPPED_PATH_MARKERS`)."""
    return [f for f in files if not _never_shipped(f)]


def verify_delivery(repo: Path, base_ref: str, sha: str) -> str:
    """`"delivered"` (ancestor of base), `"confirmed_dropped"` (object present,
    not an ancestor), or `"unverifiable"` (object no longer in the store —
    never reported as a confirmed drop)."""
    if not _object_present(repo, sha):
        return "unverifiable"
    if _is_ancestor(repo, sha, base_ref):
        return "delivered"
    return "confirmed_dropped"


def _blob_at(repo: Path, ref: str, path: str) -> str | None:
    """The blob sha `path` has at `ref`, or `None` if `ref` doesn't have that
    path (deleted, renamed, or `ref` unresolvable)."""
    result = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}:{path}")
    return result.stdout.strip() or None if result.returncode == 0 else None


def _added_lines(repo: Path, sha: str, path: str) -> list[str]:
    """Lines `sha`'s own commit added to `path` (unified-diff `+` lines,
    header lines excluded). `[]` if the diff can't be computed."""
    result = _git(repo, "diff-tree", "-p", "--root", sha, "--", path)
    if result.returncode != 0:
        return []
    lines = []
    for line in result.stdout.splitlines():
        if line.startswith("+++"):
            continue
        if line.startswith("+"):
            lines.append(line[1:])
    return lines


def _file_content_on_base(repo: Path, base_ref: str, sha: str, path: str) -> bool:
    """True if `path`'s content from `sha` is, right now, present on
    `base_ref` -- either the whole file is byte-identical (a clean
    squash/rebase/cherry-pick), or, when a later commit further edited the
    same file (a sibling task in the same squash group touching it too, or a
    follow-up change), every non-blank line `sha` added is still a literal
    line in `path`'s current content on `base_ref`. The second check is
    intentionally looser -- it is what actually resolves the dominant false
    positive in practice: two tasks in the same group both editing e.g.
    `pyproject.toml`, where the squash commit's file no longer byte-matches
    either task's individual diff in isolation.

    The line check is a >=90% match, not 100%: a live sample (task 3.2 of
    worktrail's own `backlog-seeding-epic-sequencing-gate` run) landed
    cleanly with 29 of its 30 added non-blank lines intact and only one
    later hand-adjusted (an assertion's wording tightened in a follow-up
    commit) -- a single incidental post-landing edit is not evidence the
    commit was dropped, and requiring every line verbatim rejected a
    delivered task on exactly that noise. `MIN_LINE_MATCH_RATIO` bounds how
    much drift is tolerated before this stops being "the same commit,
    lightly touched up" and starts being "can't actually tell."
    """
    target_blob = _blob_at(repo, sha, path)
    if target_blob is None:
        return False
    base_blob = _blob_at(repo, base_ref, path)
    if base_blob is None:
        return False
    if base_blob == target_blob:
        return True
    added = [line for line in _added_lines(repo, sha, path) if line.strip()]
    if not added:
        return False
    base_content = _git(repo, "show", f"{base_ref}:{path}")
    if base_content.returncode != 0:
        return False
    base_lines = set(base_content.stdout.splitlines())
    matched = sum(1 for line in added if line in base_lines)
    return (matched / len(added)) >= MIN_LINE_MATCH_RATIO


def content_delivered_via_rewrite(
    repo: Path, base_ref: str, sha: str, files: list[str]
) -> bool:
    """True if every file `sha` touched is, right now, provably present on
    `base_ref` (see `_file_content_on_base`) -- i.e. the task's own commit
    was squash-merged into a different SHA (a group-integration squash, a
    rebase, a cherry-pick), so its content *is* on the base branch even
    though `sha` itself is not an ancestor.

    `files == []` (an empty diff — a no-op or merge-only task commit) can
    never be confirmed this way and returns `False`: there is no content to
    match against, so `verify_delivery`'s not-an-ancestor verdict stands.
    """
    if not files:
        return False
    return all(_file_content_on_base(repo, base_ref, sha, path) for path in files)


# Matches a def/class/function/const/interface/type declaration line across
# Python, JS/TS, and similar C-family syntaxes. A 6+ char name floor keeps
# this to distinctive identifiers, not generic short names (`x`, `run`) that
# would coincidentally match unrelated code and produce false confidence.
_DEFINITION_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?"
    r"(?:def|class|function|const|interface|type)\s+([A-Za-z_][A-Za-z0-9_]{5,})"
)


def _defined_identifiers(added_lines: list[str]) -> set[str]:
    """Distinctive names `added_lines` newly defines (function/class/const/
    type declarations), per `_DEFINITION_RE`."""
    identifiers = set()
    for line in added_lines:
        match = _DEFINITION_RE.match(line)
        if match:
            identifiers.add(match.group(1))
    return identifiers


def identifiers_survive_elsewhere(
    repo: Path, base_ref: str, sha: str, files: list[str]
) -> bool:
    """True if every distinctive identifier the task's commit newly defined
    (across all `files`) appears **somewhere** in `base_ref`'s current tree —
    not necessarily at the same path.

    This is the fallback for `content_delivered_via_rewrite`'s remaining
    blind spot: a task's module renamed or reorganized during later
    implementation (e.g. `spec_sync_sweep_stale_bookkeeping_check.py` shipped
    as `spec_sync_sweep_check.py` instead — same functions, different file).
    Weaker evidence than `content_delivered_via_rewrite` (a name match, not a
    content match), so callers should bucket it separately.

    `False` when no identifiers could be extracted at all (a diff with no
    def/class-shaped additions — pure config, data, or edits to existing
    function bodies) — there is nothing distinctive to search for, so this
    check has no opinion; it never counts silence as survival.
    """
    identifiers: set[str] = set()
    for path in files:
        identifiers |= _defined_identifiers(_added_lines(repo, sha, path))
    if not identifiers:
        return False
    return all(
        _git(repo, "grep", "-q", "-F", "-e", ident, base_ref, timeout=60).returncode
        == 0
        for ident in identifiers
    )


def audit_repo(
    repo_name: str, worktrees_dir: Path, *, base_ref: str | None = None
) -> dict[str, Any]:
    """Audit every journal under one repo's `<repo>-worktrees` directory.

    `base_ref` overrides auto-detection (`origin/<HEAD-branch>`) — pass it
    when the repo has no reachable remote (e.g. `--base main` for an
    offline/local-only check).
    """
    canonical = resolve_canonical_repo(worktrees_dir)
    result: dict[str, Any] = {
        "repo": repo_name,
        "canonical_repo": str(canonical),
        "journals_scanned": 0,
        "tasks_checked": 0,
        "confirmed_dropped": [],
        "content_delivered_via_rewrite": [],
        "content_delivered_via_reorg": [],
        "never_shipped_by_policy": [],
        "unverifiable": [],
    }
    if not canonical.is_dir():
        result["error"] = f"canonical repo not found: {canonical}"
        return result

    resolved_base = base_ref or resolve_base_ref(canonical)
    if not resolved_base:
        result["error"] = "could not resolve base ref (no remote / offline)"
        return result
    result["base_ref"] = resolved_base

    journals = discover_journals(worktrees_dir)
    for journal_path in journals:
        journal = load_journal(journal_path)
        if journal is None:
            continue
        result["journals_scanned"] += 1
        run_id = journal.get("run_id") or journal_path.stem
        spec_id = journal.get("spec_id")
        for task_entry in extract_passed_tasks(journal):
            result["tasks_checked"] += 1
            sha = task_entry["head_sha"]
            verdict = verify_delivery(canonical, resolved_base, sha)
            if verdict == "delivered":
                continue
            record = {
                "run_id": run_id,
                "journal": journal_path.name,
                "spec_id": spec_id,
                "task": task_entry["task"],
                "head_sha": sha,
            }
            if verdict == "confirmed_dropped":
                files = touched_files(canonical, sha)
                record["files"] = files
                checkable = shippable_files(files)
                if not checkable:
                    result["never_shipped_by_policy"].append(record)
                elif content_delivered_via_rewrite(
                    canonical, resolved_base, sha, checkable
                ):
                    result["content_delivered_via_rewrite"].append(record)
                elif identifiers_survive_elsewhere(
                    canonical, resolved_base, sha, checkable
                ):
                    result["content_delivered_via_reorg"].append(record)
                else:
                    result["confirmed_dropped"].append(record)
            else:
                result["unverifiable"].append(record)
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--repos-root",
        default=str(Path.home() / "projects"),
        help="parent directory holding <repo> and <repo>-worktrees siblings "
        "(default: ~/projects)",
    )
    p.add_argument(
        "--repo",
        action="append",
        dest="repos",
        required=True,
        help="repo name to audit (its <repo>-worktrees dir under --repos-root); "
        "repeatable",
    )
    p.add_argument(
        "--base",
        help="override auto-detected base ref (e.g. 'origin/main') for every "
        "--repo passed this invocation",
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    root = Path(args.repos_root).expanduser()
    results = [
        audit_repo(name, root / f"{name}-worktrees", base_ref=args.base)
        for name in args.repos
    ]
    total_dropped = sum(len(r["confirmed_dropped"]) for r in results)
    total_rewritten = sum(len(r["content_delivered_via_rewrite"]) for r in results)
    total_reorg = sum(len(r["content_delivered_via_reorg"]) for r in results)
    total_unverifiable = sum(len(r["unverifiable"]) for r in results)

    if args.json:
        print(json.dumps({"results": results}, indent=2))
    else:
        for r in results:
            if r.get("error"):
                print(f"{r['repo']}: ERROR {r['error']}")
                continue
            print(
                f"{r['repo']}: {r['journals_scanned']} journal(s), "
                f"{r['tasks_checked']} PASSED task(s) checked, "
                f"{len(r['confirmed_dropped'])} confirmed dropped, "
                f"{len(r['content_delivered_via_rewrite'])} delivered via rewrite, "
                f"{len(r['content_delivered_via_reorg'])} delivered via reorg, "
                f"{len(r['never_shipped_by_policy'])} never-shipped-by-policy, "
                f"{len(r['unverifiable'])} unverifiable"
            )
            for finding in r["confirmed_dropped"]:
                print(
                    f"  CONFIRMED DROPPED: run={finding['run_id']} "
                    f"task={finding['task']} sha={finding['head_sha']} "
                    f"files={finding['files']}"
                )
        print(
            f"audit_delivery: {total_dropped} confirmed dropped task(s), "
            f"{total_rewritten} delivered via rewrite, "
            f"{total_reorg} delivered via reorg, "
            f"{total_unverifiable} unverifiable across {len(results)} repo(s)"
        )
    return 1 if total_dropped else 0


if __name__ == "__main__":
    sys.exit(main())
