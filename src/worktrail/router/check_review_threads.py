#!/usr/bin/env python3
"""CI-watch-loop's PR review-thread resolution gate (`ci-watch-loop.md` case 1).

Incident: datalena PR #2133 (router-capability-guard-coverage) accumulated 9
unresolved `security-review-llm` review threads across 4 rounds of findings,
even after every finding was either fixed in code or explicitly investigated
and decided not-to-fix. `gh pr checks --watch` only sees required-check pass/
fail, never GraphQL `reviewThreads.isResolved` -- so the CI watch loop
reported all-green while a human still had to notice the open threads and
reply+resolve each one by hand via `gh api graphql` /
`resolveReviewThread`. This module closes that gap: it queries a PR's review
threads, correlates each unresolved one against commits pushed in this run
(same file touched after the thread's first comment) or an explicit
`decisions` entry in the run record (the "investigated and deliberately not
fixed" case), auto-replies and resolves the ones it can correlate, and
reports the rest as `blocking` -- the same way a red required check blocks
`ci-watch-loop.md` case 1 from finishing.

This is a **gate**, not an advisory check like its siblings
(`check_brief_staleness.py`, `check_spec_collision.py`): a definitive
`blocking: true` is meant to stop `finish()` the same way a failing check
does. It only degrades to `checked: false` (never `blocking`) when the
question itself could not be answered -- `gh` missing/unauthenticated, a
malformed GraphQL response, or an unresolvable `owner/repo` -- because a
network hiccup blocking every PR-owning route forever would be worse than
occasionally missing a stale thread; the caller (`ci-watch-loop.md`) treats
`checked: false` as "no signal, proceed" and surfaces the warning.

`blocking: true` also stamps `go:no-automerge` on the PR (via
`pr_labels.ensure_pr_no_automerge_label`, additive-only, never removed).
`gh pr checks --watch` observing green only proves required checks passed --
a repo's own native auto-merge automation (`gh pr merge --auto`, armed by a
`.github/workflows/auto-merge.yml` or GitHub's native toggle) has no concept
of `reviewThreads` at all and merges the instant checks go green, racing
ahead of this gate's own `finish()`-time block (ci-watch-loop.md's stale-head
guard documents the identical race for required checks: GGB #556). Stamping
the label closes that race the same way it already closes the analogous
"automerge.enabled true but zero required status checks" gap in
`automerge_preflight.py` -- both native and repo-workflow auto-merge already
read `go:no-automerge` before arming.

Correlation is a heuristic, not proof, matching the brief's own framing
("via commit SHA/line correlation or the run record's own decision log") --
a human reviewing the auto-generated reply can always reopen a
wrongly-resolved thread.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .automerge_preflight import owner_repo_from_git
from .pr_labels import ensure_pr_no_automerge_label

Runner = Callable[..., "subprocess.CompletedProcess[str]"]

GH_TIMEOUT_SECONDS = 20
GIT_TIMEOUT_SECONDS = 5

# Defensive cap on GraphQL pagination -- 20 pages * 50 threads/page covers any
# PR that has ever existed in this codebase's history several times over;
# stops a malformed `hasNextPage: true` loop from spinning forever.
MAX_PAGES = 20
PAGE_SIZE = 50

_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: %d, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          comments(first: 20) {
            nodes { author { login } body createdAt }
          }
        }
      }
    }
  }
}
""" % PAGE_SIZE

_REPLY_MUTATION = """
mutation($threadId: ID!, $body: String!) {
  addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: $threadId, body: $body}) {
    comment { id }
  }
}
"""

_RESOLVE_MUTATION = """
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread { id isResolved }
  }
}
"""

DEFAULT_REPLY_BODY = (
    "Resolved automatically by the CI watch loop: the file this comment "
    "targets was modified by a commit pushed later in this run (or the run "
    "record documents an explicit decision), so this finding is treated as "
    "addressed. Reopen if that's wrong."
)


def _run_gh(args: List[str], timeout: int = GH_TIMEOUT_SECONDS,
            runner: Runner = subprocess.run) -> Optional["subprocess.CompletedProcess[str]"]:
    try:
        return runner(["gh", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def fetch_review_threads(
    owner: str, name: str, number: int, runner: Runner = subprocess.run,
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """All review threads on `owner/name#number`, paginated. `(threads, warning)`;
    `threads is None` means the question could not be answered at all."""
    threads: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    for _ in range(MAX_PAGES):
        args = [
            "api", "graphql",
            "-f", f"query={_THREADS_QUERY}",
            "-f", f"owner={owner}",
            "-f", f"name={name}",
            "-F", f"number={number}",
        ]
        if cursor:
            args += ["-f", f"cursor={cursor}"]
        result = _run_gh(args, runner=runner)
        if result is None:
            return None, "gh api graphql timed out or gh is not on PATH"
        if result.returncode != 0:
            return None, f"gh api graphql failed: {result.stderr.strip()[:300]}"
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None, "gh api graphql returned unparseable JSON"
        try:
            review_threads = data["data"]["repository"]["pullRequest"]["reviewThreads"]
        except (KeyError, TypeError):
            return None, f"unexpected GraphQL response shape: {result.stdout[:300]}"
        threads.extend(review_threads.get("nodes") or [])
        page_info = review_threads.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
    return threads, None


def _commit_touched_path_since(
    repo: Path, path: str, since_iso: str, runner: Runner = subprocess.run,
) -> bool:
    try:
        result = runner(
            ["git", "-C", str(repo), "log", f"--since={since_iso}", "--format=%H", "--", path],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _decision_mentions_thread(decisions: List[str], thread: Dict[str, Any]) -> bool:
    thread_id = thread.get("id") or ""
    path = thread.get("path") or ""
    for decision in decisions:
        if not isinstance(decision, str):
            continue
        if thread_id and thread_id in decision:
            return True
        if path and path in decision:
            return True
    return False


def correlate(
    repo: Path, threads: List[Dict[str, Any]], decisions: List[str],
    runner: Runner = subprocess.run,
) -> Dict[str, List[Dict[str, Any]]]:
    """Split a PR's threads into unresolved-and-addressed (safe to auto-reply
    and resolve) vs unresolved-and-unaddressed (blocking). A thread already
    marked `isResolved` by GitHub is excluded from both -- nothing to do."""
    unresolved = [t for t in threads if not t.get("isResolved")]
    addressed: List[Dict[str, Any]] = []
    unaddressed: List[Dict[str, Any]] = []
    for thread in unresolved:
        comments = (thread.get("comments") or {}).get("nodes") or []
        created_at = comments[0].get("createdAt") if comments else None
        path = thread.get("path")
        is_addressed = False
        if path and created_at and _commit_touched_path_since(repo, path, created_at, runner=runner):
            is_addressed = True
        elif _decision_mentions_thread(decisions, thread):
            is_addressed = True
        (addressed if is_addressed else unaddressed).append(thread)
    return {"unresolved": unresolved, "addressed": addressed, "unaddressed": unaddressed}


def reply_and_resolve(
    thread_id: str, body: str, runner: Runner = subprocess.run,
) -> Tuple[bool, Optional[str]]:
    """Post `body` as a reply on `thread_id`, then resolve it. Both steps must
    succeed for `True`; a reply that lands but fails to resolve is reported,
    never silently swallowed (the thread stays open on GitHub either way)."""
    reply = _run_gh(
        ["api", "graphql", "-f", f"query={_REPLY_MUTATION}",
         "-f", f"threadId={thread_id}", "-f", f"body={body}"],
        runner=runner,
    )
    if reply is None or reply.returncode != 0:
        detail = reply.stderr.strip()[:300] if reply is not None else "gh unavailable"
        return False, f"reply failed: {detail}"

    resolve = _run_gh(
        ["api", "graphql", "-f", f"query={_RESOLVE_MUTATION}", "-f", f"threadId={thread_id}"],
        runner=runner,
    )
    if resolve is None or resolve.returncode != 0:
        detail = resolve.stderr.strip()[:300] if resolve is not None else "gh unavailable"
        return False, f"reply posted but resolve failed: {detail}"
    return True, None


def _load_decisions(run_record_path: Optional[Path]) -> Tuple[List[str], Optional[str]]:
    if run_record_path is None:
        return [], None
    try:
        from .run_record import _load as load_run_record
    except Exception as exc:  # noqa: BLE001 - best-effort, never raise to caller
        return [], f"could not import run_record: {exc!r}"
    try:
        record = load_run_record(Path(run_record_path))
    except Exception as exc:  # noqa: BLE001 - best-effort, never raise to caller
        return [], f"could not read run record {run_record_path}: {exc!r}"
    decisions = record.get("decisions") or []
    return [str(d) for d in decisions if isinstance(decisions, list)], None


def check(
    repo: Path, pr_number: int, run_record_path: Optional[Path] = None,
    owner: Optional[str] = None, name: Optional[str] = None, dry_run: bool = False,
    runner: Runner = subprocess.run,
) -> Dict[str, Any]:
    """Did this PR leave any unresolved review thread that isn't yet
    addressed? Never raises. `checked: false` means the question could not be
    answered (gh unavailable, unresolvable owner/repo, bad GraphQL response)
    -- callers must treat that as "no signal", never as "nothing unresolved".
    `blocking: true` (only possible when `checked: true`) means at least one
    unresolved thread has no corresponding commit or run-record decision and
    should stop `finish()` the same way a red required check would -- it also
    stamps `go:no-automerge` on the PR (skipped when `dry_run`) so native
    auto-merge can't race ahead of this gate; see module docstring.
    """
    result: Dict[str, Any] = {
        "checked": False,
        "blocking": False,
        "unresolved_count": 0,
        "resolved_now": [],
        "unaddressed": [],
        "no_automerge_label_applied": None,
        "warning": None,
    }

    resolved_owner, resolved_name = owner, name
    if resolved_owner is None or resolved_name is None:
        owner_repo = owner_repo_from_git(repo, runner=runner)
        if not owner_repo or "/" not in owner_repo:
            result["warning"] = "could not resolve owner/repo from the git remote"
            return result
        resolved_owner, resolved_name = owner_repo.split("/", 1)

    threads, fetch_warning = fetch_review_threads(resolved_owner, resolved_name, pr_number, runner=runner)
    if threads is None:
        result["warning"] = fetch_warning
        return result

    decisions, decisions_warning = _load_decisions(run_record_path)

    grouped = correlate(repo, threads, decisions, runner=runner)
    result["checked"] = True
    result["unresolved_count"] = len(grouped["unresolved"])

    for thread in grouped["addressed"]:
        entry: Dict[str, Any] = {"id": thread.get("id"), "path": thread.get("path"), "line": thread.get("line")}
        if dry_run:
            entry["dry_run"] = True
        else:
            ok, err = reply_and_resolve(thread["id"], DEFAULT_REPLY_BODY, runner=runner)
            entry["resolved"] = ok
            if err:
                entry["error"] = err
        result["resolved_now"].append(entry)

    for thread in grouped["unaddressed"]:
        comments = (thread.get("comments") or {}).get("nodes") or []
        first = comments[0] if comments else {}
        result["unaddressed"].append({
            "id": thread.get("id"),
            "path": thread.get("path"),
            "line": thread.get("line"),
            "author": (first.get("author") or {}).get("login"),
            "body": (first.get("body") or "")[:300],
        })

    result["blocking"] = bool(result["unaddressed"])

    if result["blocking"] and not dry_run:
        result["no_automerge_label_applied"] = ensure_pr_no_automerge_label(
            str(repo), str(pr_number), eligible=False, runner=runner)

    warnings = [w for w in (fetch_warning, decisions_warning) if w]
    if warnings:
        result["warning"] = "; ".join(warnings)

    return result


def _format_human(res: Dict[str, Any]) -> str:
    if not res["checked"]:
        return f"unknown: {res.get('warning') or 'review-thread status could not be determined'}"
    if not res["unresolved_count"]:
        return "clean: no unresolved review threads"
    lines = [f"{res['unresolved_count']} unresolved thread(s) found"]
    for entry in res["resolved_now"]:
        status = "resolved" if entry.get("resolved") or entry.get("dry_run") else f"FAILED ({entry.get('error')})"
        lines.append(f"  addressed+{status}: {entry.get('path')}:{entry.get('line')}")
    for entry in res["unaddressed"]:
        lines.append(f"  BLOCKING: {entry.get('path')}:{entry.get('line')} ({entry.get('author')}): {entry.get('body')}")
    if res["blocking"]:
        lines.append("  -> unresolved+unaddressed threads present; do not finish() this route yet")
        if res.get("no_automerge_label_applied"):
            lines.append(f"  applied label: {res['no_automerge_label_applied']}")
    if res.get("warning"):
        lines.append(f"  warning: {res['warning']}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--owner", default=None, help="override the git-remote-derived owner")
    p.add_argument("--name", default=None, help="override the git-remote-derived repo name")
    p.add_argument("--run", default=None, help="run record path, for decision-log correlation")
    p.add_argument("--dry-run", action="store_true",
                    help="report only; never post replies or resolve threads")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    res = check(
        Path(args.repo), args.pr,
        run_record_path=Path(args.run) if args.run else None,
        owner=args.owner, name=args.name, dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(res))
    else:
        print(_format_human(res))

    # Always 0: this is a signal source the caller (ci-watch-loop.md) reads
    # and acts on via `blocking`/`checked`, the same way it already reads
    # `gh pr checks`/`gh pr view` JSON rather than a script exit code.
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
