#!/usr/bin/env python3
"""Unified pre-PR preflight gate CLI — the single implementation behind both
the /go orchestrator's mandatory gate (pre_pr_gate.py) and the machine-level
PreToolUse hook that blocks `gh pr create` / `gh pr ready` outside of /go
(devops scripts/claude-hooks/preflight-gate.py, symlinked at
~/.claude/hooks/preflight-gate.py).

`worktrail-preflight check` is the marker-aware, JSON-verdict entry point the
hook shells out to: no pre_pr_cmd configured, an explicit `pre_pr_cmd: skip`,
a docs-only diff (docs_only_paths), or an existing pass marker recorded
against the exact current tree state all resolve to "allow"; anything else is
"deny" with instructions to run `worktrail-preflight run`. It reuses
pre_pr_gate.py's own resolve_cmd/is_docs_only so the hook stops maintaining a
second, line-based go-policy.yaml reader that can drift from the real one.

`worktrail-preflight run` executes the full pre_pr_gate.py gate in-process
(spec-sync drift, clarification-integrity, DoD-verification, the
docs_only_paths fast path, then the resolved pre_pr_cmd) and, on a zero exit,
records the pass marker keyed to the tree state (HEAD sha + working-tree
status + diff digest) in the worktree's private git dir. That marker contract
is unchanged from the standalone devops hook script's, so passes recorded
before this migration remain valid after it.

Both subcommands' verdict also carries an optional "warning" key
(`duplicate_work_warning()`): the current branch's name is compared, by word
overlap, against sibling worktrees' branches and open PR head branches, and
its touched files are separately compared against each candidate's touched
files (independent of the name match -- catches differently-named branches
that independently edit the same files). A close match never flips the
decision to "deny" -- a legitimate resume must still proceed -- it only
surfaces a heads-up that a matching PR or worktree may already exist.

When `run --risk` is supplied, the pass marker also records the exact
`go:risk-*`/`go:no-automerge` label set `pre_pr_gate.resolve_pr_labels()`
computes for that risk/gates/target-branch. `check --command "<gh pr create
...>"` then holds that specific `gh pr create` invocation to those labels: if
one or more required labels are absent from the command, the verdict denies
even though the test gate itself passed. This is what makes the AUTOMERGE
LABELS line code-enforced -- previously it was printed for the calling agent
to copy into `gh pr create --label ...` by hand, and a skipped copy was
indistinguishable from a genuinely label-free PR (see
docs/specs/research/classify-gate-enforcement-audit.md). `gh pr ready` and
any command without `--risk`-recorded labels are unaffected.

Usage:
  worktrail-preflight check [--repo PATH] [--command "<gh pr create ...>"]
  worktrail-preflight run [--repo PATH] [--risk low|medium|high|critical]
                           [--gates G1,G2] [--target-branch BRANCH]
                           [--run RUN_RECORD]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from . import pre_pr_gate
from .policy import load_policy

MARKER_NAME = "preflight-pass.json"

# Duplicate-work warning: how much of the smaller branch's word set must
# overlap the other branch's before we warn. Two words minimum on each side
# keeps a single generic word (e.g. "fix") from tripping a false positive.
_SLUG_WORD_RE = re.compile(r"[a-z0-9]+")
_DUPLICATE_OVERLAP_THRESHOLD = 0.6
_DUPLICATE_MIN_WORDS = 2

# `gh pr ready` arms auto-merge on a PR that already carries its labels from
# creation, so label enforcement only applies to `gh pr create` itself.
PR_CREATE_RE = re.compile(r"\bgh\s+pr\s+create\b")


def labels_in_command(command: str) -> Set[str]:
    """`--label`/`--label=` values present in a shell command line.

    `shlex.split` (not a flag regex) so quoted label values and the rest of
    the command's quoting are handled the same way a real shell would; a
    malformed/unparseable command yields no labels, which fails *closed* on
    the label check below (a missing label denies) rather than open.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return set()
    found: Set[str] = set()
    for i, token in enumerate(tokens):
        if token == "--label" and i + 1 < len(tokens):
            found.add(tokens[i + 1])
        elif token.startswith("--label="):
            found.add(token[len("--label="):])
    return found


def _git(repo: Path, *args: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _current_branch(repo: Path) -> Optional[str]:
    ref = _git(repo, "symbolic-ref", "--short", "-q", "HEAD")
    if ref is None:
        return None
    ref = ref.strip()
    return ref or None


def _sibling_worktree_branches(repo: Path) -> Dict[str, str]:
    """branch name -> worktree path, for every OTHER worktree tracked by
    repo's canonical checkout.

    Reads `git worktree list --porcelain` rather than scanning
    `<repo>-worktrees/` directory names (as dashboard.py's cleanup listing
    does): that convention breaks for branch names containing "/", and this
    check must not silently miss a sibling because of that.
    """
    output = _git(repo, "worktree", "list", "--porcelain")
    if output is None:
        return {}
    branches: Dict[str, str] = {}
    path: Optional[str] = None
    for line in output.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):].strip()
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            if ref.startswith("refs/heads/"):
                ref = ref[len("refs/heads/"):]
            if path is not None:
                branches[ref] = path
    return branches


def _open_pr_branches(repo: Path) -> List[str]:
    """Open PR head branch names for repo's GitHub remote, via `gh pr list`.

    Best-effort only: `gh` missing, unauthenticated, offline, or lacking a
    GitHub remote all resolve to an empty list. This feeds a warning, never
    a deny, so it must never block on tooling/network availability.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--json", "headRefName",
             "--limit", "200"],
            capture_output=True, text=True, timeout=15, cwd=str(repo),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return [row["headRefName"] for row in rows if row.get("headRefName")]


def _slug_words(branch: str) -> set:
    return set(_SLUG_WORD_RE.findall(branch.lower()))


def _resolve_base_ref(repo: Path) -> Optional[str]:
    """Find a ref to diff branches against for the touched-file check,
    preferring the repo policy's configured base_branch. Mirrors
    pre_pr_gate._resolve_base_ref / check_dod_verification._resolve_base_ref
    -- each caller owns its own copy rather than importing another module's
    private helper.
    """
    configured = load_policy(repo).get("base_branch")
    candidates = (
        (f"origin/{configured}", configured)
        if configured else pre_pr_gate.CANDIDATE_BASE_REFS
    )
    for ref in candidates:
        if _git(repo, "rev-parse", "--verify", "--quiet", ref) is not None:
            return ref
    return None


def _touched_files(repo: Path, base_ref: str, ref: str) -> Optional[frozenset]:
    """Files `ref` has touched relative to its merge-base with `base_ref`,
    via `git diff base_ref...ref --name-only`. None means unresolvable (e.g.
    `ref` shares no history with `base_ref`) -- callers must treat that as
    "no signal", never as an error.
    """
    diff = _git(repo, "diff", "--name-only", f"{base_ref}...{ref}")
    if diff is None:
        return None
    return frozenset(line for line in diff.splitlines() if line.strip())


def _pr_touched_files(repo: Path, branch: str) -> Optional[frozenset]:
    """Files touched by the open PR whose head branch is `branch`, via `gh
    pr diff --name-only`. Best-effort like `_open_pr_branches`: any failure
    (gh missing, unauthenticated, offline, a ref gh can't resolve) returns
    None, never raises -- this feeds a warning, never a deny.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "diff", branch, "--name-only"],
            capture_output=True, text=True, timeout=15, cwd=str(repo),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return frozenset(line for line in result.stdout.splitlines() if line.strip())


def duplicate_work_warning(repo: Path) -> Optional[str]:
    """Warn (never deny) when the current branch closely overlaps an open
    PR's or a sibling worktree's branch, by branch-name word overlap or by
    touched-file overlap.

    This is the signal PR #63/#64 slipped through undetected: two sessions
    independently branched near-identical work
    (`wire-plan-audit-into-verify` / `investigate/wire-plan-audit-into-verify`)
    and neither was warned before opening its PR. The branch-name check
    catches that cheaply (word overlap, no diff inspection) but misses two
    differently-named branches that independently touch the same files --
    the deeper form of the same problem (PR #67) -- so the touched-file
    check runs alongside it: one `git diff base...branch --name-only` per
    worktree candidate, one `gh pr diff --name-only` per PR candidate.
    """
    branch = _current_branch(repo)
    if not branch:
        return None
    own_words = _slug_words(branch)
    name_check_active = len(own_words) >= _DUPLICATE_MIN_WORDS

    here = str(repo.resolve())
    candidates: Dict[str, Dict[str, str]] = {}
    for other_branch, path in _sibling_worktree_branches(repo).items():
        if other_branch == branch:
            continue
        try:
            if str(Path(path).resolve()) == here:
                continue
        except OSError:
            pass
        candidates.setdefault(
            other_branch, {"source": f"worktree at {path}", "kind": "worktree"}
        )
    for other_branch in _open_pr_branches(repo):
        if other_branch == branch:
            continue
        candidates.setdefault(other_branch, {"source": "an open PR", "kind": "pr"})
    if not candidates:
        return None

    base_ref = _resolve_base_ref(repo)
    own_files = _touched_files(repo, base_ref, branch) if base_ref else None

    for other_branch, info in candidates.items():
        source, kind = info["source"], info["kind"]

        if name_check_active:
            other_words = _slug_words(other_branch)
            if len(other_words) >= _DUPLICATE_MIN_WORDS:
                overlap = own_words & other_words
                ratio = len(overlap) / min(len(own_words), len(other_words))
                if ratio >= _DUPLICATE_OVERLAP_THRESHOLD:
                    return (
                        f"branch '{branch}' closely overlaps '{other_branch}' "
                        f"({source}) -- possible duplicate work, verify before "
                        "opening a PR"
                    )

        if own_files and base_ref:
            other_files = (
                _touched_files(repo, base_ref, other_branch) if kind == "worktree"
                else _pr_touched_files(repo, other_branch)
            )
            if other_files:
                shared_files = own_files & other_files
                if shared_files:
                    example = sorted(shared_files)[0]
                    return (
                        f"branch '{branch}' touches the same file(s) as "
                        f"'{other_branch}' ({source}), e.g. '{example}' -- "
                        "possible duplicate work, verify before opening a PR"
                    )
    return None


def tree_state(repo: Path) -> Optional[str]:
    """HEAD sha + working-tree status + diff digest.

    This is the exact marker contract the devops preflight hook established
    (see its module docstring) — kept byte-for-byte identical so markers
    recorded before this migration still validate afterward.
    """
    head = _git(repo, "rev-parse", "HEAD")
    status = _git(repo, "status", "--porcelain=v1", "-uall")
    diff = _git(repo, "diff", "HEAD")
    if head is None or status is None or diff is None:
        return None
    digest = hashlib.sha256()
    for part in (head, status, diff):
        digest.update(part.encode("utf-8", "replace"))
        digest.update(b"\0")
    return digest.hexdigest()


def marker_path(repo: Path) -> Optional[Path]:
    git_dir = _git(repo, "rev-parse", "--absolute-git-dir")
    if git_dir is None:
        return None
    return Path(git_dir.strip()) / MARKER_NAME


def read_marker(repo: Path) -> Optional[Dict[str, Any]]:
    path = marker_path(repo)
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_marker(
    repo: Path, state: str, cmd: Optional[str], labels: Optional[List[str]] = None,
) -> Optional[Path]:
    """Record a pass marker. ``labels`` (when the gate ran with ``--risk``) is
    the exact `go:risk-*`/`go:no-automerge` set the PR must carry -- computed
    once here so `check()` can hold `gh pr create` to it instead of trusting
    the calling agent to have copied the AUTOMERGE LABELS line by hand."""
    path = marker_path(repo)
    if path is None:
        return None
    path.write_text(
        json.dumps({"state": state, "cmd": cmd, "labels": labels or []}) + "\n",
        encoding="utf-8",
    )
    return path


def check(repo: Path, command: Optional[str] = None) -> Dict[str, Any]:
    """Marker-aware verdict: {"decision": "allow"|"deny", "reason": str,
    "warning": str (optional), "required_labels": [str] (optional)}.

    "warning" is populated by `duplicate_work_warning()` when set, on every
    decision path -- it is an independent, non-blocking signal, never a
    reason to flip allow to deny.

    ``command`` is the raw `gh pr create ...` shell command being gated (the
    hook's `tool_input.command`). When the pass marker recorded required PR
    labels (`--risk` was passed to `run`) and ``command`` is a `gh pr create`
    invocation missing one or more of them, the verdict flips to "deny" --
    this is what makes the go:risk-*/go:no-automerge labels code-enforced
    instead of an agent-narrated AUTOMERGE LABELS line the calling agent must
    remember to copy. `gh pr ready` and any other command pass through
    unaffected; so does an empty `required_labels` (no --risk was recorded).
    """
    if not repo.is_dir():
        return {"decision": "deny", "reason": f"repo path does not exist: {repo}"}

    warning = duplicate_work_warning(repo)

    def _verdict(decision: str, reason: str) -> Dict[str, Any]:
        verdict: Dict[str, Any] = {"decision": decision, "reason": reason}
        if warning:
            verdict["warning"] = warning
        return verdict

    policy = load_policy(repo)
    cmd = pre_pr_gate.resolve_cmd(policy)
    if cmd is None:
        return _verdict("allow", "no pre_pr_cmd/integrate_smoke_cmd configured")
    if cmd.lower() in pre_pr_gate.SKIP_VALUES:
        return _verdict("allow", f"explicit 'pre_pr_cmd: {cmd}'")
    if pre_pr_gate.is_docs_only(repo, policy):
        return _verdict("allow", "docs-only diff per docs_only_paths")

    state = tree_state(repo)
    marker = read_marker(repo)
    if state is not None and marker is not None and marker.get("state") == state:
        required = list(marker.get("labels") or [])
        if required and command is not None and PR_CREATE_RE.search(command):
            missing = [label for label in required if label not in labels_in_command(command)]
            if missing:
                return _verdict(
                    "deny",
                    "this PR's recorded risk/gates require label(s) "
                    f"{', '.join(missing)}, but this `gh pr create` command "
                    "does not pass them. Add "
                    + " ".join(f"--label {label}" for label in missing)
                    + " and retry.",
                )
        verdict = _verdict("allow", "pass marker matches current tree")
        if required:
            verdict["required_labels"] = required
        return verdict

    return _verdict(
        "deny",
        (
            "pre-PR preflight gate has not passed against the current tree. Run "
            f"`worktrail-preflight run --repo {repo}` (or `cd {repo} && "
            "worktrail-preflight run`) to execute the gate; on success it records "
            "a pass marker for this exact tree (any later commit or edit "
            "invalidates it) and PR creation will be allowed. Docs-only diffs "
            "(per go-policy docs_only_paths) skip the gate automatically."
        ),
    )


def _run(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    gate_argv = ["--repo", str(repo)]
    if args.risk:
        gate_argv += ["--risk", args.risk]
    if args.gates:
        gate_argv += ["--gates", args.gates]
    if args.target_branch:
        gate_argv += ["--target-branch", args.target_branch]
    if args.route:
        gate_argv += ["--route", args.route]
    if args.run:
        gate_argv += ["--run", args.run]

    exit_code = pre_pr_gate.main(gate_argv)
    if exit_code != 0:
        return exit_code

    state = tree_state(repo)
    if state is None:
        print("preflight: gate passed but tree state could not be recorded", file=sys.stderr)
        return 0
    policy = load_policy(repo)
    cmd = pre_pr_gate.resolve_cmd(policy)
    labels: List[str] = []
    if args.risk:
        gates = [g for g in args.gates.split(",") if g]
        labels, _eligible, _reason = pre_pr_gate.resolve_pr_labels(
            repo, policy, args.risk, gates, args.target_branch, route=args.route
        )
    marker = write_marker(repo, state, cmd, labels)
    if marker is not None:
        print(f"preflight: marker recorded at {marker}")
        if labels:
            print(f"preflight: required PR label(s) recorded: {' '.join(labels)}")
    return 0


def _check(args: argparse.Namespace) -> int:
    verdict = check(Path(args.repo).resolve(), command=args.gh_command)
    print(json.dumps(verdict))
    return 0 if verdict["decision"] == "allow" else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    check_p = sub.add_parser(
        "check", help="marker-aware verdict as JSON (for hook consumption)",
    )
    check_p.add_argument("--repo", default=".", help="worktree root to check (default: cwd)")
    check_p.add_argument(
        "--command", default=None, dest="gh_command",
        help="the gh pr create/ready command being gated; when the pass marker "
             "recorded required PR labels and this is a `gh pr create` command "
             "missing one or more of them, the verdict denies",
    )
    check_p.set_defaults(func=_check)

    run_p = sub.add_parser(
        "run", help="execute the full pre-PR gate and record a pass marker on success",
    )
    run_p.add_argument("--repo", default=".", help="worktree root to gate (default: cwd)")
    run_p.add_argument(
        "--risk", default=None, choices=("low", "medium", "high", "critical"),
        help="classifier risk for this PR — forwarded to pre_pr_gate.py's --risk",
    )
    run_p.add_argument("--gates", default="", help="comma-separated classifier gates")
    run_p.add_argument("--target-branch", default="main", help="PR target branch")
    run_p.add_argument(
        "--route", default=None,
        help="classified route letter — forwarded to pre_pr_gate.py's --route "
             "for the require_human_routes check",
    )
    run_p.add_argument(
        "--run", default=None, metavar="RUN_RECORD",
        help="shared go run record; enables mandatory scope completeness review",
    )
    run_p.set_defaults(func=_run)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
