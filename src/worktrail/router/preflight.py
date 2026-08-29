#!/usr/bin/env python3
"""Unified pre-PR preflight gate CLI — the single implementation behind both
the /go orchestrator's mandatory gate (pre_pr_gate.py) and the machine-level
PreToolUse hook that blocks `git push` / `gh pr create` / `gh pr ready`
outside of /go (devops scripts/claude-hooks/preflight-gate.py, symlinked at
~/.claude/hooks/preflight-gate.py).

`worktrail-preflight check` is the marker-aware, JSON-verdict entry point the
hook shells out to. It denies unconditionally when tracked files carry
uncommitted changes (`dirty_tree_reason()`) -- `git push` only ever sends
committed history, so a pass marker matching a dirty tree (working-tree
status is part of its own key) provides no assurance about what actually
ships. Past that: no pre_pr_cmd configured, an explicit `pre_pr_cmd: skip`,
a docs-only diff (docs_only_paths), or an existing pass marker recorded
against the exact current (clean) tree state all resolve to "allow"; anything
else is "deny" with instructions to run `worktrail-preflight run`. It reuses
pre_pr_gate.py's own resolve_cmd/is_docs_only so the hook stops maintaining a
second, line-based worktrail-go-policy.yaml reader that can drift from the real one.

`worktrail-preflight run` refuses immediately (`DIRTY_TREE_EXIT`, no gate
command spawned) when `dirty_tree_reason()` finds uncommitted changes to
tracked files -- a pass marker recorded against a dirty tree can never match
the tree state `check()` sees after the commit that's needed to ship the
change, so running the gate against one is guaranteed wasted work. Past that,
it executes the full pre_pr_gate.py gate in-process
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

`worktrail-preflight run` also holds a running-lock file (pid + start time) in
the worktree's private git dir for the duration of the gate, removed in a
`finally` block on exit. `worktrail-preflight wait` polls that lock, not a
process-name match, to answer "is a gate currently running against this
repo" -- the intended replacement for a hand-rolled `pgrep -f
"worktrail-preflight run"` poll, which can match its own polling command's
literal text and never observe completion (see `RUNNING_LOCK_NAME`).

Usage:
  worktrail-preflight check [--repo PATH] [--command "<gh pr create ...>"]
  worktrail-preflight run [--repo PATH] [--risk low|medium|high|critical]
                           [--gates G1,G2] [--target-branch BRANCH]
                           [--run RUN_RECORD]
  worktrail-preflight wait [--repo PATH] [--timeout SECONDS] [--interval SECONDS]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from worktrail.addons.runner import AddOnFailure, run_addons

from . import pre_pr_gate
from .policy import load_policy

MARKER_NAME = "preflight-pass.json"

# Returned by `run` when a `required: true` add-on (`policy["add_ons"]`) fails
# -- mirrors pre_pr_gate's UNCONFIGURED_EXIT/failing-cmd exit codes (2 and the
# command's own nonzero returncode): no pass marker is written, so `check()`'s
# `gh pr create` guard denies the same as it would for a failed smoke gate.
ADDON_REQUIRED_FAILURE_EXIT = 6

# Returned by `run` when `dirty_tree_reason()` finds uncommitted changes to
# tracked files, before `pre_pr_cmd` is even invoked. `tree_state()` folds
# working-tree status into the pass-marker key, so a marker recorded against
# a dirty tree can never match the tree state after the next `git commit` --
# running the gate (`pytest` + integration checks, often several minutes) on
# an uncommitted tree is structurally guaranteed to be wasted work, since the
# very commit that's needed to ship the change invalidates the marker
# `check()` will look for. Refusing here, before the expensive command runs,
# mirrors `check()`'s own dirty-tree-first ordering (see its docstring) --
# it just moves the same refusal from push time to gate-run time.
DIRTY_TREE_EXIT = 7

# Written for the duration of `run`'s gate execution and removed in a
# `finally` block on exit (pass, fail, or exception) -- gives callers a
# race-free way to ask "is a gate currently running against this repo"
# without process-name matching. A `pgrep -f "worktrail-preflight run"`
# poll looks equivalent but is not: the polling shell's own command line
# (constructed via `eval` inside an agent harness) commonly contains that
# same literal string, so the pattern matches the poller itself and the
# loop never observes "not running" -- exactly the failure this lock
# exists to make impossible. See `is_running()`/`wait` below.
RUNNING_LOCK_NAME = "preflight-running.json"

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


def is_unparseable_command(command: str) -> bool:
    """True when `shlex.split(command)` can't tokenize it (e.g. an apostrophe
    in unquoted prose inside a `--body $(cat <<'EOF' ...)` heredoc trips
    shlex's own quote-tracking, even though the real shell parses it fine).

    Mirrors `preflight-gate.py`'s `_is_unparseable_command` byte-for-byte.
    Callers use this to give a distinct, actionable deny reason instead of
    the generic "label not passed" message when the real cause is a parse
    failure, not a missing `--label` flag.
    """
    try:
        shlex.split(command)
    except ValueError:
        return True
    return False


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


def dirty_tree_reason(repo: Path) -> Optional[str]:
    """Deny reason when tracked files carry uncommitted changes (staged or
    unstaged) relative to HEAD, or None when the tree is clean.

    Untracked files are deliberately excluded: they were never going to be
    part of any commit regardless, so they carry none of the risk this
    guards against. Staged/unstaged changes to *tracked* files are that
    risk -- `tree_state()` folds working-tree status into its own marker
    key, so a marker recorded against a dirty tree can never match the tree
    state `git push` actually sends (only committed history), and can never
    match the tree state after the very commit that's needed to ship the
    change either. `run` now refuses immediately on a dirty tree (see
    `DIRTY_TREE_EXIT`) rather than spending time on a doomed gate run.
    Incident: datalena PR #2478 (2026-08-22) merged missing 112 files of
    gate-verified fixes because they were made after the gate's last passing
    run but never committed before `git push` + `gh pr create` -- the marker
    matched the (dirty) tree at push time, so nothing caught the gap until a
    manual `git worktree remove` refused over leftover uncommitted changes.
    This check closes that gap at the same choke point `check()` already
    guards, and (via `run`'s own refusal) before the gap can even open.
    """
    diff = _git(repo, "diff", "HEAD", "--name-only")
    if diff is None:
        return None  # unresolvable (e.g. no commits yet) -- no signal, never a false deny
    changed = [line for line in diff.splitlines() if line.strip()]
    if not changed:
        return None
    sample = ", ".join(changed[:5])
    more = f" (+{len(changed) - 5} more)" if len(changed) > 5 else ""
    return (
        f"{len(changed)} tracked file(s) have uncommitted changes not captured by any "
        f"commit: {sample}{more}. `git push`/`gh pr create`/`gh pr ready` only ever act on "
        "committed history -- these edits would be silently dropped from what actually "
        "ships, even though a preflight gate run just validated a tree state that included "
        "them. Commit them (`git add` + `git commit`) or stash them, then retry."
    )


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


def running_lock_path(repo: Path) -> Optional[Path]:
    git_dir = _git(repo, "rev-parse", "--absolute-git-dir")
    if git_dir is None:
        return None
    return Path(git_dir.strip()) / RUNNING_LOCK_NAME


def write_running_lock(repo: Path) -> Optional[Path]:
    path = running_lock_path(repo)
    if path is None:
        return None
    path.write_text(
        json.dumps({"pid": os.getpid(), "started_at": time.time()}) + "\n",
        encoding="utf-8",
    )
    return path


def remove_running_lock(repo: Path) -> None:
    path = running_lock_path(repo)
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def read_running_lock(repo: Path) -> Optional[Dict[str, Any]]:
    path = running_lock_path(repo)
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness check via `os.kill(pid, 0)` (sends no signal).

    A permission error still means the process exists (owned by another
    user) -- treated as alive. Any other unexpected OSError also fails
    closed (alive) rather than declaring a real gate stale by mistake.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def is_running(repo: Path) -> bool:
    """True when a `worktrail-preflight run` gate is currently executing
    against this repo, per the running-lock file -- not a process-name
    scan, so it cannot self-match a caller's own polling command line.

    A lock whose recorded pid is no longer alive (the process crashed
    without reaching the `finally` cleanup, e.g. `kill -9` or a host
    reboot) is stale: it is removed here and treated as not-running,
    rather than wedging every future check against this repo forever.
    """
    lock = read_running_lock(repo)
    if lock is None:
        return False
    pid = lock.get("pid")
    if not isinstance(pid, int):
        remove_running_lock(repo)
        return False
    if _pid_alive(pid):
        return True
    remove_running_lock(repo)
    return False


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

    Before any of that, `dirty_tree_reason()` gets the first say: uncommitted
    changes to tracked files deny unconditionally, regardless of policy,
    docs-only status, or an otherwise-matching pass marker. This applies to
    `git push` too (the hook now gates it the same as `gh pr create`/`ready`)
    -- see `dirty_tree_reason()`'s own docstring for the incident this closes.
    """
    if not repo.is_dir():
        return {"decision": "deny", "reason": f"repo path does not exist: {repo}"}

    warning = duplicate_work_warning(repo)

    def _verdict(decision: str, reason: str) -> Dict[str, Any]:
        verdict: Dict[str, Any] = {"decision": decision, "reason": reason}
        if warning:
            verdict["warning"] = warning
        return verdict

    dirty_reason = dirty_tree_reason(repo)
    if dirty_reason is not None:
        return _verdict("deny", dirty_reason)

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
            if is_unparseable_command(command):
                return _verdict(
                    "deny",
                    "this `gh pr create` command could not be parsed to verify its "
                    f"required label(s) ({', '.join(required)}) -- likely an apostrophe or "
                    "unescaped quote in the PR title/body breaking shell-token parsing "
                    "(e.g. inside a `--body $(cat <<'EOF' ...)` heredoc). Use "
                    "`--body-file <path>` instead of an inline `--body` and retry.",
                )
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
    dirty_reason = dirty_tree_reason(repo)
    if dirty_reason is not None:
        print(f"PRE-PR GATE: FAIL — {dirty_reason}", file=sys.stderr)
        print(
            "  Any pass marker recorded now would be keyed to this dirty tree "
            "and can never match the tree state after the commit `check()` "
            "requires -- commit first, then re-run.",
            file=sys.stderr,
        )
        return DIRTY_TREE_EXIT
    policy = load_policy(repo)
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

    write_running_lock(repo)
    try:
        try:
            run_addons(repo, repo, policy)
        except AddOnFailure as e:
            print(
                f"PRE-PR GATE: FAIL — required add-on {e.name!r} failed: {e.detail}",
                file=sys.stderr,
            )
            print("  Fix the add-on failure and re-run the gate.", file=sys.stderr)
            return ADDON_REQUIRED_FAILURE_EXIT
        exit_code = pre_pr_gate.main(gate_argv)
    finally:
        remove_running_lock(repo)
    if exit_code != 0:
        return exit_code

    state = tree_state(repo)
    if state is None:
        print("preflight: gate passed but tree state could not be recorded", file=sys.stderr)
        return 0
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


def _wait(args: argparse.Namespace) -> int:
    """Block until no `run` gate is executing against this repo, per the
    running-lock file. This is the intended replacement for a hand-rolled
    `pgrep -f "worktrail-preflight run"` poll: a poller's own command line
    routinely contains that same literal string (see RUNNING_LOCK_NAME's
    docstring), which makes such a loop match itself and never terminate.
    """
    repo = Path(args.repo).resolve()
    deadline = time.monotonic() + args.timeout
    while is_running(repo):
        if time.monotonic() >= deadline:
            print(json.dumps({"running": True, "timed_out": True}))
            return 1
        time.sleep(args.interval)
    print(json.dumps({"running": False}))
    return 0


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

    wait_p = sub.add_parser(
        "wait",
        help="block until no `run` gate is executing against this repo "
             "(running-lock file, not process-name matching)",
    )
    wait_p.add_argument("--repo", default=".", help="worktree root to watch (default: cwd)")
    wait_p.add_argument(
        "--timeout", type=float, default=900.0,
        help="max seconds to wait before giving up (default: 900)",
    )
    wait_p.add_argument(
        "--interval", type=float, default=2.0,
        help="seconds between liveness checks (default: 2)",
    )
    wait_p.set_defaults(func=_wait)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
