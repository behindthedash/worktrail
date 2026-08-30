#!/usr/bin/env python3
"""A `gh` stand-in for the lifecycle harness.

Placed first on PATH so the REAL integrate.py / verify.py code paths run
end-to-end against a local bare remote with no network and no GitHub. This is
deliberately a subprocess-level fake, not a Python seam: the bug classes the
harness exists to catch (journal state across integrate/verify, CI-green
detection, merge confirmation) live in code that shells out to `gh`, and an
injected Python fake would bypass the exact argument-building and JSON-parsing
code that has broken in production (#163, #164, #207).

State lives in a JSON file at $GH_FAKE_STATE:

    {
      "remote": "/abs/path/to/bare-remote.git",   # set by the harness
      "base": "main",
      "next_number": 1,
      "prs": {"<head-branch>": {"number": N, "url": "...", "state": "OPEN",
                                 "checks": "SUCCESS"}}
    }

`pr merge` advances the bare remote's base ref to the head branch's sha
(update-ref), which is what lets verify.py's real post-merge confirmation and
base-advance logic observe a genuinely moved base — the point of the harness.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path


def _load():
    p = Path(os.environ["GH_FAKE_STATE"])
    return p, json.loads(p.read_text())


def _save(p, state):
    p.write_text(json.dumps(state, indent=2))


def _branch_sha(remote: str, branch: str) -> str:
    out = subprocess.run(
        ["git", "-C", remote, "rev-parse", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def _resolve_pr(state, ref: str):
    """Find a PR by head branch name or URL."""
    prs = state["prs"]
    if ref in prs:
        return ref, prs[ref]
    for branch, pr in prs.items():
        if pr["url"] == ref or str(pr["number"]) == ref:
            return branch, pr
    return None, None


def _json_fields(argv):
    if "--json" in argv:
        return argv[argv.index("--json") + 1].split(",")
    return []


def _pr_payload(branch, pr, fields, state):
    sha = _branch_sha(state["remote"], branch)
    full = {
        "number": pr["number"],
        "state": pr["state"],
        "url": pr["url"],
        "headRefName": branch,
        "headRefOid": sha,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "autoMergeRequest": None,
        "mergedBy": {"login": "fake-gh"} if pr["state"] == "MERGED" else None,
        "labels": [],
        "baseRefName": state["base"],
        # One completed check so classify_checks sees a settled, passing rollup.
        "statusCheckRollup": [
            {
                "__typename": "CheckRun",
                "name": "Lint, Test & Build",
                "status": "COMPLETED",
                "conclusion": pr.get("checks", "SUCCESS"),
            }
        ],
    }
    return {k: full.get(k) for k in fields} if fields else full


def main(argv) -> int:
    # Sibling task groups dispatch `gh` concurrently; without this lock a
    # `_load()` can land inside another process's `write_text()` truncation
    # window and read an empty state file (JSONDecodeError).
    lock_path = Path(os.environ["GH_FAKE_STATE"]).with_suffix(".lock")
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r+") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        return _dispatch(argv)


def _dispatch(argv) -> int:
    p, state = _load()
    if not argv:
        return 1

    if argv[0] == "pr" and argv[1] == "create":
        head = None
        if "--head" in argv:
            head = argv[argv.index("--head") + 1]
        else:  # gh infers head from the current branch; harness always passes cwd's branch
            head = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
            ).stdout.strip()
        n = state["next_number"]
        state["next_number"] = n + 1
        pr = {
            "number": n,
            "url": f"https://fake.local/pr/{n}",
            "state": "OPEN",
            "checks": "SUCCESS",
        }
        state["prs"][head] = pr
        _save(p, state)
        print(pr["url"])
        return 0

    if argv[0] == "pr" and argv[1] == "view":
        branch, pr = _resolve_pr(state, argv[2])
        if pr is None:
            print("no pull requests found", file=sys.stderr)
            return 1
        print(json.dumps(_pr_payload(branch, pr, _json_fields(argv), state)))
        return 0

    if argv[0] == "pr" and argv[1] == "list":
        print("[]")
        return 0

    if argv[0] == "pr" and argv[1] == "merge":
        branch, pr = _resolve_pr(state, argv[2])
        if pr is None:
            print("no pull requests found", file=sys.stderr)
            return 1
        sha = _branch_sha(state["remote"], branch)
        if not sha:
            print(f"head branch {branch} not on remote", file=sys.stderr)
            return 1
        # REAL squash merge into base via a scratch clone: matches the merge
        # methods this fake's own `repo view` advertises (squash-only) and the
        # production repos that hit the incident this harness reproduces --
        # `git merge --squash` collapses the head branch's commits into a new,
        # independent commit on base, so a dependent group's task branch (still
        # carrying the pre-squash commits) is no longer an ancestor of base once
        # its own branch is deleted (the journaled `head_sha`-ancestry gap).
        # Sibling group branches also diverge (each stacked on base, not on each
        # other), so a ref fast-forward would silently drop the earlier
        # sibling's work — the exact class of shortcut this harness exists to
        # avoid.
        import tempfile

        with tempfile.TemporaryDirectory() as scratch:
            clone = Path(scratch) / "clone"
            subprocess.run(
                ["git", "clone", "-q", state["remote"], str(clone)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(clone), "config", "user.email", "gh@fake"], check=True
            )
            subprocess.run(
                ["git", "-C", str(clone), "config", "user.name", "fake gh"], check=True
            )
            subprocess.run(
                ["git", "-C", str(clone), "checkout", "-q", state["base"]],
                check=True,
                capture_output=True,
            )
            m = subprocess.run(
                ["git", "-C", str(clone), "merge", "--squash", "-q", sha],
                capture_output=True,
                text=True,
            )
            if m.returncode != 0:
                print(f"merge conflict merging {branch}: {m.stderr}", file=sys.stderr)
                return 1
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(clone),
                    "commit",
                    "-q",
                    "-m",
                    f"Merge PR #{pr['number']} ({branch})",
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(clone), "push", "-q", "origin", state["base"]],
                check=True,
                capture_output=True,
            )
        pr["state"] = "MERGED"
        if "--delete-branch" in argv:
            subprocess.run(
                [
                    "git",
                    "-C",
                    state["remote"],
                    "update-ref",
                    "-d",
                    f"refs/heads/{branch}",
                ],
                check=False,
            )
        _save(p, state)
        return 0

    if argv[0] == "pr" and argv[1] in ("edit", "close"):
        return 0

    if argv[0] == "repo" and argv[1] == "view":
        print(
            json.dumps(
                {
                    "mergeCommitAllowed": False,
                    "squashMergeAllowed": True,
                    "rebaseMergeAllowed": False,
                    "autoMergeAllowed": False,
                }
            )
        )
        return 0

    if argv[0] == "run":  # gh run list/view — no CI runs in the harness
        print("[]")
        return 0

    if argv[0] == "api":
        endpoint = argv[1] if len(argv) > 1 else ""
        if "/rules/branches/" in endpoint:
            print(
                json.dumps(
                    [
                        {
                            "type": "required_status_checks",
                            "parameters": {
                                "required_status_checks": [
                                    {"context": "Lint, Test & Build"}
                                ]
                            },
                        }
                    ]
                )
            )
            return 0
        if endpoint.startswith("repos/") and endpoint.count("/") == 2:
            print(json.dumps({"allow_auto_merge": True}))
            return 0
        print("{}")
        return 0

    print(f"fake gh: unhandled args {argv}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
