#!/usr/bin/env python3
"""
Parallel SDD Orchestrator -- integration + grouped PRs.

After the per-task fan-out, integrate the task branches into per-group branches
(design 13.2: base first, feature groups stacked on base), push to a sandbox
GitHub repo, open one PR per group, and (optionally) drop the branches+PRs.

`finish()` integrates -> pushes -> PRs -> cleans up against a sandbox repo.
`synthetic_fanout()` fabricates per-task branches with trivial commits (NO agent
spawns), so the integration/PR mechanics can be validated for free before
spending a live run.

CLI:
  python3 integrate.py validate --sandbox owner/repo [--keep]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from . import coordinator
from . import dispatch
from . import live
from . import progress
from . import worktree
from ..taskformats import resolve as taskformats

TAIL = ("e2e", "cleanup")
TERMINAL_GROUP_STATES = {"OPEN", "MERGED", "QUARANTINED"}

# Structured classification for *why* a group was quarantined, written into
# the journal's groups[name].quarantine_reason alongside state=="QUARANTINED".
# Distinguishes a group that simply never got to run (safely resumable by a
# plain re-run) from one that hit a real failure needing human review.
QUARANTINE_BUDGET_EXHAUSTED = "budget_exhausted"
QUARANTINE_TASK_FAILURE = "task_failure"
QUARANTINE_MERGE_CONFLICT = "merge_conflict"
QUARANTINE_INTEGRATION_ERROR = "integration_error"
QUARANTINE_DEPENDENCY_QUARANTINED = "dependency_quarantined"


_HERE = Path(__file__).resolve().parent


def _spec_path_for(iw: Path, spec_id: str) -> Path:
    """Find a materialized spec, including nested devkit change specs."""
    candidates = [
        iw / "openspec" / "changes" / spec_id,
        iw / "docs" / "specs" / spec_id,
        iw / ".specify" / "specs" / spec_id,
    ]
    nested = sorted((iw / "docs" / "specs").glob(f"*/changes/{spec_id}"))
    # Preserve the historical devkit fallback when mocked or not-yet-materialized
    # specs have no on-disk path to select.
    return next((p for p in [*candidates, *nested] if p.exists()), candidates[1])


def _resolve_pre_pr_gate(here: Path = _HERE) -> Optional[Path]:
    """Locate Worktrail's own pre-PR gate, with an explicit override for tests."""
    candidate = os.environ.get("PRE_PR_GATE_SCRIPT")
    if candidate and Path(candidate).is_file():
        return Path(candidate).resolve()
    installed = shutil.which("worktrail-pre-pr-gate")
    if installed:
        return Path(installed).resolve()
    local = here.parent.parent / "router" / "pre_pr_gate.py"
    if local.is_file():
        return local.resolve()
    return None


def _extract_risk_from_labels(pr_labels: list[str]) -> Optional[str]:
    for label in pr_labels:
        if label.startswith("go:risk-"):
            return label.removeprefix("go:risk-")
    return None


def _refresh_pr_labels(
    repo: Path,
    pr_labels: Optional[list[str]],
    target_branch: str,
    gates: str = "",
    route: Optional[str] = None,
) -> Optional[list[str]]:
    """Refresh PR labels by re-running the pre-PR gate's label resolution.

    Returns fresh labels from the current policy and required-check state,
    or None when the gate script cannot be resolved or no risk label is
    present (caller should fall back to the original pr_labels).

    route: the classified route letter, forwarded to pre_pr_gate.py's
    --route so policy's require_human_routes check applies to orchestrator
    group PRs the same way it applies to one-off PRs (worktrail-preflight
    run already passes --route there). Omit only when the route is unknown.
    """
    if not pr_labels:
        return None
    gate_script = _resolve_pre_pr_gate()
    if gate_script is None:
        return None
    risk = _extract_risk_from_labels(pr_labels)
    if risk is None:
        return None
    try:
        cmd = [
            sys.executable, str(gate_script),
            "--repo", str(repo),
            "--labels-only",
            "--risk", risk,
            "--gates", gates,
            "--target-branch", target_branch,
        ]
        if route:
            cmd += ["--route", route]
        r = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return None
        labels = [l for l in r.stdout.strip().split() if l]
        return labels if labels else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _pr_label_args(pr_labels: Optional[list[str]]) -> list[str]:
    """Build the exact label flags resolved by the GO pre-PR gate.

    Labels are resolved by _refresh_pr_labels immediately before creating
    each new group PR, so every fresh PR carries the current policy and
    required-check state.  Existing OPEN/MERGED PRs keep their original
    labels (deterministic).
    """
    return [arg for label in (pr_labels or []) for arg in ("--label", label)]

# Max attempts to dispatch a resolve worker when task branches conflict at assembly time.
# Assembly conflicts are deterministic (same two branches will conflict every time), so a
# single attempt is the most useful budget: if the worker can't resolve it in one pass, a
# second attempt with identical input is unlikely to succeed differently.
ASSEMBLY_RESOLVE_STRIKES = 1


def _git(repo, *args, check=True):
    return live._git(Path(repo), *args, check=check)


def current_branch(repo) -> str:
    return live._git(Path(repo), "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


@contextlib.contextmanager
def _integration_worktree(repo: Path, branch: str, start_ref: str, git_lock=None):
    """Build a group branch in a throwaway worktree, never moving `repo`'s HEAD.

    The old code ran `git checkout -B <group> <start>` *inside* `repo`, which is the
    spec worktree -- hijacking HEAD off the spec branch and discarding any uncommitted
    `files:` edits, so every integration resume needed a manual `git switch` back.
    Instead we `git worktree add -B <group> <tmp> <start>`: the group branch ref is
    created/reset in an isolated checkout that we tear down afterward (the branch ref
    persists; only the temp checkout is removed). This also makes per-group integration
    safe to run concurrently (pipeline mode) -- each group merges in its own tree.

    worktree add/prune/remove mutate the shared .git registry, so they are serialized
    under `git_lock` when one is supplied (the pipeline's registry lock); the merges
    and push happen in the isolated tree and need no lock. Yields the worktree Path.
    """
    repo = Path(repo)
    lock = git_lock if git_lock is not None else contextlib.nullcontext()
    # `git worktree add` creates intermediate parent dirs, so we don't pre-mkdir
    # (which would also touch the real filesystem under mocked unit tests).
    wt = repo.parent / f"{repo.name}-integrate" / f"{branch.replace('/', '-')}-{uuid.uuid4().hex[:8]}"
    with lock:
        _git(repo, "worktree", "prune", check=False)  # clear stale registrations from crashes
        _git(repo, "worktree", "add", "-f", "-B", branch, str(wt), start_ref)
    try:
        yield wt
    finally:
        with lock:
            _git(repo, "worktree", "remove", "--force", str(wt), check=False)
        shutil.rmtree(wt, ignore_errors=True)  # belt-and-suspenders if remove no-op'd


def _strip_spec_folder_to_base(iw: Path, spec_id: str, base_ref: str) -> None:
    """Reset the active spec folder to base_ref in a non-spec-carrier branch.

    Sibling independent groups all carry TASK-*.md status updates, causing add/add
    (or modify/modify) conflicts when the first sibling merges into the real base.
    Only the designated spec-carrier group (the "base" group, or the first feature
    group when no base group exists) carries spec folder changes forward. All other
    independent siblings reset it to base_ref so their PRs introduce zero spec-folder
    diff and cannot conflict on those files.
    """
    spec_path = _spec_path_for(iw, spec_id)
    p = _git(iw, "checkout", base_ref, "--", str(spec_path.relative_to(iw)), check=False)
    if p.returncode != 0:
        return
    diff = _git(iw, "diff", "--cached", "--quiet", check=False)
    if diff.returncode != 0:  # staged changes present after reset
        _git(iw, "commit", "-m",
             f"chore: reset spec folder to {base_ref} (non-spec-carrier group)")


def _write_group_task_status(
    iw: Path, spec_id: str, group: dict, status: dict
) -> None:
    """Write ``completed`` into this group's own task files, once, on the group
    branch -- the single point where run bookkeeping reaches the spec artifact.

    Task branches deliberately carry no ``docs/specs/**`` diff (see
    ``live.cleanup_task_in_python``): status lives in the run journal during a
    run. Without this step a merged PR would leave the artifact stale.

    Scoped to ``group["tasks"]``, so sibling group branches touch disjoint task
    files and cannot add/add conflict on them -- which is why this runs *after*
    ``_strip_spec_folder_to_base()`` rather than instead of it: the strip still
    owns resetting any *other* spec-folder drift, and this re-applies only the
    status this group is entitled to write.

    Goes through ``TaskSource.mark_status`` rather than editing frontmatter
    inline so a non-devkit task format (e.g. an OpenSpec ``tasks.md`` checkbox)
    plugs in here without changing integrate.
    """
    # The integration worktree is the source of truth for the spec artifact at
    # this point. Resolve its adapter from the artifact path instead of
    # assuming the legacy devkit layout; OpenSpec stores every task in one
    # `tasks.md`, while devkit stores one file per task.
    spec_path = _spec_path_for(iw, spec_id)
    source = taskformats.task_source_for(spec_path)
    spec_ref = taskformats.spec_ref_for(spec_path)
    changed = []
    for tid in group.get("tasks", []):
        # DONE ({"done","completed"}), NOT ALREADY_INTEGRATED ({"completed"}):
        # a task finished in THIS run reaches integrate as "done" — filtering on
        # ALREADY_INTEGRATED made this write a silent no-op on every fresh run
        # (both schedulers), which is why merged runs kept needing manual
        # "sync: mark tasks completed post-orchestrator" doc PRs. Found by the
        # lifecycle harness; the unit tests passed "completed" directly and
        # never exercised the caller's real status values.
        if status.get(tid) not in coordinator.DONE:
            continue
        try:
            if source.mark_status(tid, "completed", spec_ref=spec_ref):
                changed.append(tid)
        except (FileNotFoundError, OSError):
            continue  # task file absent on this branch; nothing to write
    if not changed:
        return
    spec_rel = source.spec_root(spec_ref).relative_to(iw)
    _git(iw, "add", "--", str(spec_rel), check=False)
    if _git(iw, "diff", "--cached", "--quiet", check=False).returncode != 0:
        _git(iw, "commit", "-q", "-m",
             f"chore({group['name']}): mark {len(changed)} task(s) completed")


def synthetic_fanout(repo: Path, spec_id: str, tasks: list, base: str) -> None:
    """Fabricate per-task branches with trivial commits (no agents)."""
    for t in [t for t in tasks if t.get("kind", "impl") not in TAIL]:
        branch = f"{spec_id}/{t['id'].lower()}"
        _git(repo, "checkout", "-q", "-B", branch, base)
        for f in t.get("files") or [f"src/{t['id'].lower()}.txt"]:
            fp = Path(repo) / f
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(f"// {t['id']}: {t.get('title', '')}\n")
            _git(repo, "add", f)
        _git(repo, "commit", "-q", "-m", f"feat({t['id']}): synthetic stub")
    _git(repo, "checkout", "-q", base)


def finish(
    repo: Path,
    spec_id: str,
    tasks: list,
    sandbox: str,
    run_id: str,
    base: str,
    cleanup: bool = True,
) -> tuple:
    """Integrate per group -> push to sandbox -> one PR per group -> cleanup."""
    groups = coordinator.plan_groups(tasks)
    _git(repo, "remote", "remove", "sandbox", check=False)
    _git(repo, "remote", "add", "sandbox", f"https://github.com/{sandbox}.git")
    # remote main == this run's base commit, so group PRs show only the group diff
    _git(repo, "push", "-q", "-f", "sandbox", f"{base}:main")

    status = {t["id"]: t.get("status", "done") for t in tasks}
    group_branch: dict = {}
    group_tasks: dict = {}  # group name -> deliverable task ids
    quarantined: dict = {}  # group name -> reason (needs a human)

    for g in groups:
        name = g["name"]
        dep_q = next((d for d in g["depends_on"] if d in quarantined), None)
        if dep_q:  # can't build on a quarantined base
            quarantined[name] = f"depends on quarantined group '{dep_q}'"
            print(f"  SKIP [{name:9}] -- {quarantined[name]}")
            continue
        deliverable, dropped = coordinator.deliverable_subset(g["tasks"], tasks, status)
        if not deliverable:  # nothing shippable -> whole group waits
            quarantined[name] = f"incomplete task(s): {', '.join(dropped)}"
            print(f"  SKIP [{name:9}] -- {quarantined[name]}")
            continue
        if dropped:  # split failed task(s) out; ship the rest
            quarantined[f"{name}/dropped"] = f"split out of {name}: {', '.join(dropped)}"
            print(
                f"  SPLIT [{name:9}] -- shipping {', '.join(deliverable)}; "
                f"quarantined {', '.join(dropped)}"
            )
        group_tasks[name] = deliverable
        gb = f"{run_id}/{name}"
        start = base if not g["depends_on"] else group_branch[g["depends_on"][0]]
        _git(repo, "checkout", "-q", "-B", gb, start)
        conflict = None
        for tid in deliverable:  # catch worker scope-drift collisions
            m = _git(repo, "merge", "--no-edit", f"{spec_id}/{tid.lower()}", check=False)
            if m.returncode != 0:
                _git(repo, "merge", "--abort", check=False)
                conflict = tid
                break
        if conflict:
            quarantined[name] = f"merge conflict integrating {conflict}"
            print(f"  SKIP [{name:9}] -- {quarantined[name]}")
            continue
        group_branch[name] = gb
        _git(repo, "push", "-q", "sandbox", f"{gb}:{gb}")

    prs = []
    for g in groups:
        name = g["name"]
        if name not in group_branch:  # quarantined -> no PR
            continue
        gb = group_branch[name]
        target = "main" if not g["depends_on"] else group_branch.get(g["depends_on"][0], "main")
        r = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                sandbox,
                "--base",
                target,
                "--head",
                gb,
                "--title",
                f"[{run_id}] {name}: {', '.join(group_tasks[name])}",
                "--body",
                f"Group **{name}** | reqs: {', '.join(g['reqs']) or '-'} "
                f"| base: `{target}`\n\nparallel-orchestrator {run_id}.",
            ],
            capture_output=True,
            text=True,
        )
        out = (
            (r.stdout or r.stderr).strip().splitlines()[-1]
            if (r.stdout or r.stderr)
            else "(no output)"
        )
        prs.append((name, target, out))
        print(f"  PR [{name:9}] base={target:18} -> {out}")

    if quarantined:
        print("QUARANTINED groups (need human review):")
        for n, reason in quarantined.items():
            print(f"  - {n}: {reason}")

    if cleanup:
        print("cleanup: closing PRs + deleting remote branches ...")
        for g in reversed(groups):
            gb = group_branch.get(g["name"])
            if gb:
                subprocess.run(
                    ["gh", "pr", "close", "--repo", sandbox, gb, "--delete-branch"],
                    capture_output=True,
                    text=True,
                )
    return prs, group_branch, quarantined


def _write_group_journal(
    journal_path: Optional[str],
    name: str,
    pr_url: str,
    head_branch: str,
    state: str,
    quarantine_reason: str = "",
) -> None:
    """Atomically write one group's integrate record into the journal.

    quarantine_reason: one of the QUARANTINE_* categories, recorded only when
    non-empty (i.e. state == "QUARANTINED"). Rebuilding the record fresh each
    call means a group that later transitions out of QUARANTINED drops any
    stale reason automatically.
    """
    if not journal_path:
        return
    try:
        journal_file = Path(journal_path)
        journal: dict = {}
        if journal_file.exists():
            journal = json.loads(journal_file.read_text())
        if "groups" not in journal:
            journal["groups"] = {}
        record = {"pr_url": pr_url, "head_branch": head_branch, "state": state}
        if quarantine_reason:
            record["quarantine_reason"] = quarantine_reason
        journal["groups"][name] = record
        progress.atomic_write_text(
            journal_file, json.dumps(journal, indent=2, sort_keys=True) + "\n"
        )
    except Exception as e:
        print(f"  WARNING: Failed to write journal for group {name}: {e}")


# Why integrate_complete can be True while tail-kind tasks remain unrun. Recorded
# verbatim in the journal so a cold `continue` reads intent in one shot instead of
# re-deriving it from the absence of tail entries (see _pending_tail_task_ids).
PENDING_TAIL_REASON = (
    "tail-kind tasks (e2e/cleanup) are held out of the parallel fan-out by design and "
    "run after the group PRs merge; integrate_complete reflects the impl groups only"
)


def _pending_tail_task_ids(tasks: Optional[list]) -> list:
    """Task ids held out by the fan-out and not yet terminal.

    This includes tail-kind tasks (e2e/cleanup) plus pending non-tail tasks whose
    only unmet deps are tail-kind tasks. Those tasks cannot be dispatched during
    the parallel fan-out, so the dashboard should surface them with the tail work.
    """
    return coordinator.tail_held_out_task_ids(tasks or [])


def _mark_integrate_complete_if_terminal(
    journal_path: Optional[str], groups: list, tasks: Optional[list] = None
) -> bool:
    """Set integrate_complete only when every planned group has terminal state.

    When `tasks` is supplied and the impl groups are complete, also records the
    tail-kind tasks (e2e/cleanup) the fan-out held out -- as `pending_tail_tasks`
    (the ids) plus `pending_tail_reason` (why integrate completed with them
    outstanding). This makes "done" honest: a resumed run / `go` dashboard reads
    the outstanding tail work directly instead of diffing journal entries against
    tasks.md. Fields are cleared when no tail work remains (idempotent on resume).
    """
    if not journal_path:
        return False
    try:
        journal_file = Path(journal_path)
        journal: dict = {}
        if journal_file.exists():
            journal = json.loads(journal_file.read_text())
        records = journal.get("groups", {})
        expected = [g["name"] for g in groups]
        complete = bool(expected) and all(
            records.get(name, {}).get("state") in TERMINAL_GROUP_STATES for name in expected
        )
        if complete:
            journal["integrate_complete"] = True
            tail_ids = _pending_tail_task_ids(tasks)
            if tail_ids:
                journal["pending_tail_tasks"] = tail_ids
                journal["pending_tail_reason"] = PENDING_TAIL_REASON
            else:
                journal.pop("pending_tail_tasks", None)
                journal.pop("pending_tail_reason", None)
            progress.atomic_write_text(
                journal_file, json.dumps(journal, indent=2, sort_keys=True) + "\n"
            )
        return complete
    except Exception as e:
        print(f"  WARNING: Failed to finalize integrate journal: {e}")
        return False


def detect_unreconciled_tail_evidence(
    repo: Path, remote: Optional[str], base: Optional[str], spec_id: str,
    wt_base: Path, tasks: list,
) -> list:
    """Flag terminal tail-kind (e2e/cleanup) tasks whose own worktree branch
    carries commits that never made it onto the base branch.

    Tail tasks are held out of the parallel fan-out's group/PR machinery by
    design (see PENDING_TAIL_REASON) and each runs in its own stacked
    per-task worktree (`live.py`'s `ensure_wt`/`_dispatch_pending_tail`).
    Nothing merges, pushes, or opens a PR for that worktree afterward, so a
    spawned worker's real commit there (an evidence file, a doc update, the
    task's own tasks.md checkbox flip) sits on a throwaway branch that a
    later worktree-cleanup pass deletes with zero trace -- while the task
    itself still reports DONE and the run claims full completion (reproduced
    2026-08-12: a fixture-backed dry-run evidence commit sat on an unpushed,
    unmerged task branch after a 12/12-done run).

    Detection only -- this never merges, pushes, or deletes anything. A task
    with an empty `files:` scope that genuinely made no commits (HEAD never
    moved past its stacked-base) is not flagged; only one whose HEAD is not
    an ancestor of `<remote>/<base>` is.
    """
    if not remote or not base:
        return []
    findings: list = []
    remote_base_ref = f"{remote}/{base}"
    for task in tasks:
        if task.get("kind") not in TAIL:
            continue
        if task.get("status") not in coordinator.DONE:
            continue
        wt = worktree.worktree_path(wt_base, spec_id, task["id"])
        if not wt.is_dir():
            continue
        head = _git(wt, "rev-parse", "HEAD", check=False).stdout.strip()
        if not head:
            continue
        p = _git(wt, "merge-base", "--is-ancestor", head, remote_base_ref, check=False)
        if p.returncode != 0:
            findings.append({"task": task["id"], "worktree": str(wt), "head_sha": head[:8]})
    return findings


def _record_unreconciled_tail_evidence(journal_path: Optional[str], findings: list) -> None:
    """Persist `detect_unreconciled_tail_evidence`'s findings into the run
    journal as `unreconciled_tail_evidence`, mirroring how `pending_tail_tasks`
    is recorded -- `journal_selfcheck.check_repo()` reads this field into a
    `unreconciled-tail-evidence` finding, which the `go` dashboard's existing
    "Stranded runs" section already renders generically by kind (no dashboard
    change needed). Cleared when there is nothing to report (idempotent).
    """
    if not journal_path:
        return
    try:
        journal_file = Path(journal_path)
        journal: dict = {}
        if journal_file.exists():
            journal = json.loads(journal_file.read_text())
        if findings:
            journal["unreconciled_tail_evidence"] = findings
        else:
            journal.pop("unreconciled_tail_evidence", None)
        progress.atomic_write_text(
            journal_file, json.dumps(journal, indent=2, sort_keys=True) + "\n"
        )
    except Exception as e:
        print(f"  WARNING: Failed to record unreconciled tail evidence: {e}")


SMOKE_TIMEOUT_DEFAULT = int(os.environ.get("ORCH_SMOKE_TIMEOUT", "1800"))


def _run_integration_smoke(iw: Path, name: str, smoke_cmd: str) -> tuple:
    """Run the policy smoke command in a group's integration worktree.

    Returns (ok, detail). `ok` is True on exit 0; on failure `detail` carries a
    short tail of stderr/stdout for the quarantine reason. A timeout or spawn
    error counts as failure (fail-closed: never open a PR on an unverified merge).
    """
    print(f"  SMOKE [{name:9}] running integrated test: {smoke_cmd}")
    try:
        r = subprocess.run(
            smoke_cmd,
            shell=True,
            cwd=str(iw),
            capture_output=True,
            text=True,
            timeout=SMOKE_TIMEOUT_DEFAULT,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {SMOKE_TIMEOUT_DEFAULT}s"
    except OSError as e:
        return False, f"could not run smoke command: {e}"
    if r.returncode == 0:
        return True, "ok"
    tail = ((r.stderr or "") + (r.stdout or "")).strip()[-300:]
    return False, f"exit {r.returncode}: {tail}"


# Conflicted files we auto-resolve at assembly time without a worker: package
# `__init__.py` markers that two task branches each ADDED independently (add/add).
# Every task that creates a new package adds the same `pkg/__init__.py`, so this is a
# recurring quarantine cause; their contents (docstring / imports / `__all__`) union
# cleanly and must all survive. Scope is deliberately narrow -- basename match AND
# add/add only -- so a real modify/modify code conflict is never silently unioned.
AUTO_RESOLVE_BASENAMES = {"__init__.py"}


def _union_init(ours: str, theirs: str) -> str:
    """Content-preserving union of two independently ADDED package-init files.

    Identical sides collapse to one copy; otherwise both sides are kept (ours then
    theirs, newline-separated) so every symbol survives. The resulting PR still runs
    CI, which is the backstop if the union is not import-clean -- strictly better than
    today's hard quarantine + manual hand-consolidation.
    """
    if ours == theirs:
        return ours
    if not ours.strip():
        return theirs
    if not theirs.strip():
        return ours
    sep = "" if ours.endswith("\n") else "\n"
    return ours + sep + theirs


def _auto_resolve_addadd_inits(iw: Path) -> bool:
    """Resolve an in-progress conflicted merge IFF every conflicted path is an
    add/add (`AA`) on a whitelisted package-init file, by union-merging the two
    added blobs and committing.

    Returns True only when the merge is fully resolved and committed. Returns False
    (leaving the conflict state intact) when there is nothing to resolve or any
    conflicted path is off-pattern, so the caller falls back to a resolve worker or
    quarantine unchanged.
    """
    porcelain = _git(iw, "status", "--porcelain", check=False).stdout
    conflicted = []
    for line in porcelain.splitlines():
        xy, path = line[:2], line[3:].strip()
        if "U" in xy or xy in {"AA", "DD"}:  # any unmerged / add-add / del-del state
            if path.startswith('"'):  # quoted (space/unicode) path -> safe fallback
                return False
            conflicted.append((xy, path))
    if not conflicted:
        return False
    # Every conflicted path must be an add/add on a whitelisted init file, else bail.
    for xy, path in conflicted:
        if xy != "AA" or os.path.basename(path) not in AUTO_RESOLVE_BASENAMES:
            return False
    for _, path in conflicted:
        ours = _git(iw, "show", f":2:{path}", check=False).stdout
        theirs = _git(iw, "show", f":3:{path}", check=False).stdout
        (iw / path).write_text(_union_init(ours, theirs))
        _git(iw, "add", "--", path)
    _git(iw, "commit", "--no-edit", check=False)
    return True


def _assembly_resolve_salvage(iw: Path, conflicted_files: list) -> bool:
    """Bounded git-state salvage for an unparseable resolve report-back.

    A resolve worker that actually finished leaves the integration worktree
    with the merge concluded (no MERGE_HEAD), a clean tree, and no conflict
    markers left in the files that were conflicted. When all three hold, the
    resolution is real even though the report-back could not be parsed -- the
    same graceful degradation implement/fix get from their git-commit salvage
    path. Unlike review/cleanup (whose verdicts cannot be inferred from git
    state), a concluded conflict-free merge IS the outcome being reported.
    """
    if _git(iw, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).returncode == 0:
        return False  # merge still in progress
    if _git(iw, "status", "--porcelain", check=False).stdout.strip():
        return False  # dirty tree
    for f in conflicted_files:
        p = Path(iw) / f
        if p.is_file() and "<<<<<<<" in p.read_text(errors="replace"):
            return False
    return True


def _attempt_assembly_resolve(
    iw: Path,
    name: str,
    tid: str,
    spec_id: str,
    g: dict,
    spawn_fn,
) -> bool:
    """Dispatch a resolve worker to fix an assembly-time merge conflict.

    Called when `git merge --no-edit <task-branch>` returns non-zero inside the
    integration worktree.  The merge state (conflict markers) is still in place
    when this function is entered.  Returns True if the worker resolved and
    committed the merge; False otherwise.  On False the merge is left aborted so
    the caller can immediately quarantine.
    """
    task_branch = f"{spec_id}/{tid.lower()}"
    conflicted_files = [
        ln.strip()
        for ln in _git(iw, "diff", "--name-only", "--diff-filter=U",
                       check=False).stdout.splitlines()
        if ln.strip()
    ]
    prompt = dispatch.build_group_prompt(
        dispatch.ROLE_ASSEMBLY_RESOLVE,
        g,
        {
            "spec_id": spec_id,
            "group_branch": name,
            "worktree_path": str(iw),
            "conflicting_branch": task_branch,
            "base_branch": "",
            "remote": "",
        },
    )
    for strike in range(ASSEMBLY_RESOLVE_STRIKES):
        print(f"  RESOLVE [{name:9}] merge conflict on {task_branch} "
              f"-- spawning resolve worker (strike {strike + 1}/{ASSEMBLY_RESOLVE_STRIKES})")
        parsed_failure = False
        try:
            raw = spawn_fn(prompt, iw)
            rep = dispatch.parse_report_back(raw)
            if rep.get("status") == "success":
                return True
            parsed_failure = True  # worker explicitly reported failure; trust it
        except Exception as exc:
            print(f"  RESOLVE [{name:9}] worker error: {exc!r}")
        if not parsed_failure and _assembly_resolve_salvage(iw, conflicted_files):
            print(f"  RESOLVE [{name:9}] salvaged from git state (merge concluded, "
                  f"clean tree, no conflict markers; report-back unparseable)")
            return True
        # Worker failed; abort the conflicted merge so subsequent re-attempts or the
        # outer quarantine path start from a clean tree.
        _git(iw, "merge", "--abort", check=False)
        if strike < ASSEMBLY_RESOLVE_STRIKES - 1:
            # Re-issue the conflicting merge to restore conflict state for the next strike.
            m = _git(iw, "merge", "--no-edit", task_branch, check=False)
            if m.returncode == 0:
                return True  # Merged cleanly on the retry (unlikely but safe)
    return False


def integrate_one(
    g: dict,
    repo: Path,
    spec_id: str,
    tasks: list,
    remote: str,
    run_id: str,
    base: str,
    journal_path: Optional[str],
    status: dict,
    group_branch: dict,
    quarantined: dict,
    _record_group=None,
    git_lock=None,
    strip_spec_folder: bool = False,
    smoke_cmd: Optional[str] = None,
    assembly_resolve_spawn=None,
    pr_labels: Optional[list[str]] = None,
    route: Optional[str] = None,
    gates: str = "",
) -> Optional[tuple]:
    """Integrate exactly one group and record its result in the journal.

    Computes the deliverable subset for the named group, creates or reuses its
    stacked group branch, pushes, and opens or reuses its PR. Writes the group's
    per-group record (pr_url, head_branch, state) to the journal atomically.

    Returns a (name, target, pr_url) tuple when the group receives a PR, or None
    when the group is quarantined or already merged.

    Modifies group_branch and quarantined in-place; callers rely on these for
    dependent-group stacking and dep-on-quarantined cascade.

    Reconcile-safe: skips a MERGED group, reuses an existing OPEN PR (including
    operator-PR discovery), reuses an existing remote branch, and creates only
    what is missing.

    _record_group: optional callable(name, pr_url, head_branch, state, quarantine_reason="")
    injected by the pipeline scheduler so every group journal write is serialized under
    state_lock and merged with the in-memory entries list (AC-015). When None, falls
    back to _write_group_journal (sequential/non-pipeline path).

    smoke_cmd: optional shell command (from go-policy.yaml's integrate_smoke_cmd) run
    in the freshly-merged integration worktree -- where the group's task branches first
    coexist -- BEFORE pushing/opening the PR. A non-zero exit quarantines the group
    instead of opening a red-CI PR, catching cross-task drift a CI round-trip earlier.
    None = skip. Only runs when the branch is freshly built (not on a remote-branch reuse).
    pr_labels: initial labels resolved by the GO pre-PR gate. These are
    REFRESHED immediately before each new group PR creation (by re-running
    pre_pr_gate.py --labels-only) so every fresh PR reflects the current
    policy and required-check state. Existing OPEN/MERGED PRs keep their
    original labels (deterministic). Pass None to skip label handling.
    route, gates: the classified route letter and comma-joined gates for
    this run, forwarded to the label refresh so require_human_routes (and
    any gate-driven eligibility check) reaches orchestrator-created group
    PRs the same way it reaches one-off PRs. Omit when unknown.
    """
    name = g["name"]

    def _ensure_pr_ready(pr_data: dict) -> bool:
        """Convert a reused draft PR before it enters the CI/auto-merge path.

        Worktrail-created PRs are ready by construction (the create command does
        not pass ``--draft``), but a resumed run can encounter a draft created by
        another publisher.  Treat that state explicitly: GitHub's auto-merge
        automation commonly skips draft PRs, so silently recording it as OPEN can
        strand the run indefinitely.
        """
        if not pr_data.get("isDraft"):
            return True
        pr_ref = str(pr_data.get("number") or pr_data.get("url") or "")
        if not pr_ref:
            print(f"  SKIP [{name:9}] -- draft PR has no resolvable number or URL")
            return False
        ready_cmd = ["gh", "pr", "ready", pr_ref]
        ready = subprocess.run(ready_cmd, capture_output=True, text=True, cwd=str(repo))
        if ready.returncode != 0:
            detail = (ready.stderr or ready.stdout or "unable to mark PR ready").strip()
            print(f"  SKIP [{name:9}] -- could not mark draft PR ready: {detail[:240]}")
            return False
        print(f"  READY [{name:9}] draft PR {pr_ref} converted to ready-for-review")
        return True

    def _do_journal(nm: str, pu: str, hb: str, st: str, quarantine_reason: str = "") -> None:
        if _record_group is not None:
            _record_group(nm, pu, hb, st, quarantine_reason)
        else:
            _write_group_journal(journal_path, nm, pu, hb, st, quarantine_reason)

    # Dep-on-quarantined cascade
    dep_q = next((d for d in g["depends_on"] if d in quarantined), None)
    if dep_q:
        quarantined[name] = f"depends on quarantined group '{dep_q}'"
        print(f"  SKIP [{name:9}] -- {quarantined[name]}")
        _do_journal(name, "", f"{run_id}/{name}", "QUARANTINED", QUARANTINE_DEPENDENCY_QUARANTINED)
        return None

    # Deliverable subset
    deliverable, dropped = coordinator.deliverable_subset(g["tasks"], tasks, status)
    if not deliverable:
        # If every task in the group is already integrated (completed from a prior run),
        # the group's work is already on the base branch — treat it as implicitly merged
        # so dependent groups can target the base branch directly (no quarantine cascade).
        if all(status.get(tid) in coordinator.ALREADY_INTEGRATED for tid in g["tasks"]):
            gb_implicit = f"{run_id}/{name}"
            print(f"  MERGED [{name:9}] (all tasks already integrated from prior run)")
            _do_journal(name, "", gb_implicit, "MERGED")
            group_branch[name] = gb_implicit
            return None
        quarantined[name] = f"incomplete task(s): {', '.join(dropped)}"
        print(f"  SKIP [{name:9}] -- {quarantined[name]}")
        _do_journal(name, "", f"{run_id}/{name}", "QUARANTINED", QUARANTINE_TASK_FAILURE)
        return None
    if dropped:
        quarantined[f"{name}/dropped"] = f"split out of {name}: {', '.join(dropped)}"
        print(
            f"  SPLIT [{name:9}] -- shipping {', '.join(deliverable)}; "
            f"quarantined {', '.join(dropped)}"
        )
        _do_journal(
            f"{name}/dropped", "", f"{run_id}/{name}/dropped", "QUARANTINED",
            QUARANTINE_TASK_FAILURE,
        )

    gb = f"{run_id}/{name}"
    target = base if not g["depends_on"] else group_branch.get(g["depends_on"][0], base)
    # PR base ref — must always be a real branch (GitHub rejects a commit SHA as a PR
    # base). `target` is the worktree start-ref and may be reassigned to a commit in the
    # dep-branch-gone fallback below; pr_base tracks the branch to open the PR against.
    pr_base = target

    # When the dep group's branch is the target but has since been deleted (merged +
    # cleanup), git worktree add fails with "src refspec does not match any". Detect the
    # missing ref and fall back to the pre-squash merge-base of the first task branch and
    # the remote base, then reconcile squash-merge divergence after integrating tasks.
    squash_reconcile_ref: Optional[str] = None
    if g["depends_on"] and target != base:
        ref_ok = _git(repo, "rev-parse", "--verify", target, check=False)
        if ref_ok.returncode != 0:
            remote_base_ref = f"{remote}/{base}"
            if deliverable:
                first_task = f"{spec_id}/{deliverable[0].lower()}"
                mb = _git(repo, "merge-base", first_task, remote_base_ref, check=False)
                if mb.returncode == 0 and mb.stdout.strip():
                    target = mb.stdout.strip()
                    squash_reconcile_ref = remote_base_ref
                else:
                    target = remote_base_ref
            else:
                target = remote_base_ref
            # The dep group has been merged into `base` and its branch deleted; this
            # dependent group's PR must target the real base branch, not the (now-gone)
            # dep branch or the pre-squash commit `target` — GitHub requires a branch ref.
            pr_base = base
            print(f"  FALLBACK [{name:9}] dep branch '{g['depends_on'][0]}' gone — "
                  f"start {'pre-squash ' + target[:8] if squash_reconcile_ref else remote_base_ref}")

    # Reconcile: MERGED? Skip branch/PR creation and write MERGED record.
    pr_view = subprocess.run(
        ["gh", "pr", "view", gb, "--json", "number,state,url,headRefName,isDraft"],
        capture_output=True,
        text=True,
        cwd=str(repo),
    )
    if pr_view.returncode == 0:
        try:
            pr_data = json.loads(pr_view.stdout)
            if pr_data.get("state") == "MERGED":
                pr_url = pr_data.get("url", "")
                print(f"  MERGED [{name:9}] (already integrated, skipping)")
                _do_journal(name, pr_url, gb, "MERGED")
                group_branch[name] = gb
                return None
        except Exception:
            pass

    # Reconcile: remote branch (reuse or create)
    ls_check = _git(repo, "ls-remote", remote, gb, check=False)
    if ls_check.returncode == 0 and ls_check.stdout.strip():
        # Remote branch exists; fetch locally so dependent groups can use it as a start ref.
        _git(repo, "fetch", "-q", remote, f"{gb}:{gb}")
        print(f"  REUSE [{name:9}] remote branch {gb}")
    else:
        # Build `gb` in an isolated worktree so `repo` (the spec worktree) keeps its
        # HEAD and any uncommitted edits. The merges + push run inside that tree.
        with _integration_worktree(repo, gb, target, git_lock=git_lock) as iw:
            conflict = None
            for tid in deliverable:
                m = _git(iw, "merge", "--no-edit", f"{spec_id}/{tid.lower()}", check=False)
                if m.returncode != 0:
                    if _auto_resolve_addadd_inits(iw):
                        print(f"  AUTORESOLVE [{name:9}] add/add package-init conflict "
                              f"on {spec_id}/{tid.lower()} -- union-merged")
                        continue  # deterministic resolve; loop to next task
                    if assembly_resolve_spawn is not None and \
                       _attempt_assembly_resolve(iw, name, tid, spec_id, g,
                                                 assembly_resolve_spawn):
                        continue  # worker resolved the conflict; loop to next task
                    _git(iw, "merge", "--abort", check=False)
                    conflict = tid
                    break
            if conflict:
                quarantined[name] = f"merge conflict integrating {conflict}"
                print(f"  SKIP [{name:9}] -- {quarantined[name]}")
                _do_journal(name, "", gb, "QUARANTINED", QUARANTINE_MERGE_CONFLICT)
                return None
            # When we started from a pre-squash merge-base (dep branch was deleted after
            # squash-merge), bring in the squashed remote base with -X ours. The task
            # branches carry the base group's content from before the squash; -X ours
            # resolves apparent conflicts in our favor (content is byte-identical).
            if squash_reconcile_ref:
                _git(iw, "merge", "--no-edit", "-X", "ours", squash_reconcile_ref, check=False)
            if strip_spec_folder:
                _strip_spec_folder_to_base(iw, spec_id, target)
            _write_group_task_status(iw, spec_id, g, status)
            if smoke_cmd:
                ok, detail = _run_integration_smoke(iw, name, smoke_cmd)
                if not ok:
                    quarantined[name] = f"integrated smoke test failed: {detail}"
                    print(f"  SKIP [{name:9}] -- {quarantined[name]}")
                    _do_journal(name, "", gb, "QUARANTINED", QUARANTINE_INTEGRATION_ERROR)
                    return None
            push = _git(iw, "push", "-q", remote, f"{gb}:{gb}", check=False)
            if push.returncode != 0:
                # Base may have advanced since we branched — retry once after rebasing.
                print(f"  RETRY [{name:9}] push failed, rebasing onto {remote}/{base} ...")
                _git(iw, "fetch", "-q", remote, base, check=False)
                rebase = _git(iw, "rebase", f"{remote}/{base}", check=False)
                if rebase.returncode != 0:
                    _git(iw, "rebase", "--abort", check=False)
                    quarantined[name] = f"rebase failed after push rejection: {push.stderr.strip()[:200]}"
                    print(f"  SKIP [{name:9}] -- {quarantined[name]}")
                    _do_journal(name, "", gb, "QUARANTINED", QUARANTINE_MERGE_CONFLICT)
                    return None
                retry = _git(iw, "push", "-q", remote, f"{gb}:{gb}", check=False)
                if retry.returncode != 0:
                    quarantined[name] = f"push still failing after rebase: {retry.stderr.strip()[:200]}"
                    print(f"  SKIP [{name:9}] -- {quarantined[name]}")
                    _do_journal(name, "", gb, "QUARANTINED", QUARANTINE_INTEGRATION_ERROR)
                    return None

    group_branch[name] = gb

    # Reconcile: PR (reuse OPEN, discover operator PR, or create new)
    pr_view = subprocess.run(
        ["gh", "pr", "view", gb, "--json", "number,state,url,headRefName,isDraft"],
        capture_output=True,
        text=True,
        cwd=str(repo),
    )
    head_branch = gb

    if pr_view.returncode == 0:
        try:
            pr_data = json.loads(pr_view.stdout)
            pr_state = pr_data.get("state")
            if pr_state == "OPEN":
                if not _ensure_pr_ready(pr_data):
                    quarantined[name] = "existing draft PR could not be marked ready"
                    _do_journal(name, "", gb, "QUARANTINED", QUARANTINE_INTEGRATION_ERROR)
                    return None
                pr_url = pr_data.get(
                    "url", f"https://github.com/o/r/pull/{pr_data.get('number', '?')}"
                )
                head_branch = pr_data.get("headRefName", gb)
                print(f"  PR [{name:9}] base={pr_base:18} -> {pr_url} (existing OPEN)")
                _do_journal(name, pr_url, head_branch, "OPEN")
                return (name, pr_base, pr_url)
            elif pr_state == "MERGED":
                print(f"  MERGED [{name:9}] (already integrated, skipping)")
                return None
        except Exception:
            pass

    # No open PR on conventional branch; try operator PR discovery
    search_result = subprocess.run(
        [
            "gh", "pr", "list", "--search", f"{name} {spec_id}",
            "--json", "number,state,url,headRefName,isDraft", "--state", "open",
        ],
        capture_output=True,
        text=True,
        cwd=str(repo),
    )
    if search_result.returncode == 0:
        try:
            matches = json.loads(search_result.stdout)
            if matches:
                match = matches[0]
                if not _ensure_pr_ready(match):
                    quarantined[name] = "discovered draft PR could not be marked ready"
                    _do_journal(name, "", gb, "QUARANTINED", QUARANTINE_INTEGRATION_ERROR)
                    return None
                pr_url = match.get("url")
                head_branch = match.get("headRefName", gb)
                print(f"  PR [{name:9}] base={pr_base:18} -> {pr_url} (discovered operator PR)")
                _do_journal(name, pr_url, head_branch, "OPEN")
                return (name, pr_base, pr_url)
        except Exception:
            pass

    # No PR found (conventional or operator); refresh labels and create new one
    fresh_labels = _refresh_pr_labels(repo, pr_labels, pr_base, gates=gates, route=route)
    effective_labels = fresh_labels if fresh_labels is not None else pr_labels

    remote_url = _git(repo, "remote", "get-url", remote).stdout.strip()
    gh_repo = (
        remote_url.removesuffix(".git").split("github.com", 1)[-1].lstrip(":/")
        if "github.com" in remote_url
        else None
    )
    escalations = [
        f"- `{task['id']}`: {', '.join(f'`{path}`' for path in task['_scope_escalation_files'])}"
        for task in tasks
        if task.get("id") in deliverable and task.get("_scope_escalation_files")
    ]
    escalation_note = (
        "\n\n### Automatic scope escalation\n\n" + "\n".join(escalations)
        if escalations else ""
    )
    cmd = [
        "gh", "pr", "create",
        "--base", pr_base,
        "--head", gb,
        *_pr_label_args(effective_labels),
        "--title", f"[{run_id}] {name}: {', '.join(deliverable)}",
        "--body",
        f"Group **{name}** | reqs: {', '.join(g['reqs']) or '-'} "
        f"| base: `{pr_base}`\n\nparallel-orchestrator {run_id}.{escalation_note}",
    ]
    if gh_repo:
        cmd += ["--repo", gh_repo]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(repo))
    out = (
        (r.stdout or r.stderr).strip().splitlines()[-1]
        if (r.stdout or r.stderr)
        else "(no output)"
    )
    # `gh pr create` fails the WHOLE command (no PR created, non-zero exit) on
    # an unresolvable --label, e.g. "could not add label: 'go:risk-medium' not
    # found" -- catch that here rather than recording the error text itself as
    # `pr_url` with state OPEN, which corrupts the journal and (on --pipeline)
    # reads back as a truthy pr_url, wrongly treated as "already integrated"
    # (see brief 20260723-102500, Bug 1 + Bug 2).
    if r.returncode != 0 or not out.startswith("http"):
        quarantined[name] = f"gh pr create failed: {out}"
        print(f"  SKIP [{name:9}] -- {quarantined[name]}")
        _do_journal(name, "", gb, "QUARANTINED", QUARANTINE_INTEGRATION_ERROR)
        return None
    print(f"  PR [{name:9}] base={pr_base:18} -> {out}")
    _do_journal(name, out, gb, "OPEN")
    return (name, pr_base, out)


def _read_group_journal_record(journal_path: Optional[str], name: str) -> dict:
    """Return group `name`'s current journal record, or `{}` if absent."""
    if not journal_path:
        return {}
    try:
        journal_file = Path(journal_path)
        if not journal_file.exists():
            return {}
        journal = json.loads(journal_file.read_text())
        return journal.get("groups", {}).get(name, {})
    except Exception:
        return {}


def reconcile_unreconciled_tail_evidence(
    findings: list,
    repo: Path,
    spec_id: str,
    tasks: list,
    remote: str,
    run_id: str,
    base: str,
    journal_path: Optional[str],
    pr_labels: Optional[list[str]] = None,
    route: Optional[str] = None,
    gates: str = "",
) -> list:
    """Open (or reuse) a group PR for each `detect_unreconciled_tail_evidence`
    finding, so a tail task's stranded evidence commit reaches `base` the same
    way an impl group's does -- instead of sitting on a throwaway worktree
    branch a later cleanup pass deletes with zero trace.

    Each finding becomes a synthetic single-task group (`tail-<task-id>`, no
    deps) fed straight into `integrate_one` -- the same merge/push/PR/quarantine
    seam every impl group already goes through, so a real merge conflict on
    the tail branch quarantines it exactly like any other group would rather
    than needing new handling here.

    Returns a new list (the input `findings` is never mutated) of dicts each
    carrying the original finding fields plus `reconcile_state` (one of
    "opened", "already-open", "merged", "quarantined") and `reconcile_pr_url`
    (empty string when there is none, e.g. quarantined). The journal is
    re-read after each `integrate_one` call rather than trusting its return
    value, since that call returns None on both a MERGED-skip and a
    QUARANTINED outcome. Distinguishing a freshly-opened PR from one reused
    from a prior call requires the group's journal state from *before* this
    call too, so that is captured first -- this makes reconciliation safe to
    retry across resumed runs.
    """
    status = {t["id"]: t.get("status", "done") for t in tasks}
    by_id = {t["id"]: t for t in tasks}
    enriched: list = []
    for finding in findings:
        task_id = finding["task"]
        name = f"tail-{task_id.lower()}"
        pre_state = _read_group_journal_record(journal_path, name).get("state")
        g = {
            "name": name,
            "tasks": [task_id],
            "depends_on": [],
            "reqs": by_id.get(task_id, {}).get("reqs", []),
        }
        integrate_one(
            g, repo, spec_id, tasks, remote, run_id, base, journal_path, status,
            group_branch={}, quarantined={},
            pr_labels=pr_labels, route=route, gates=gates,
        )
        post_record = _read_group_journal_record(journal_path, name)
        post_state = post_record.get("state")
        if post_state == "OPEN":
            reconcile_state = "already-open" if pre_state == "OPEN" else "opened"
        elif post_state == "MERGED":
            reconcile_state = "merged"
        else:
            reconcile_state = "quarantined"
        enriched.append({
            **finding,
            "reconcile_state": reconcile_state,
            "reconcile_pr_url": post_record.get("pr_url", "") or "",
        })
    return enriched


def _wait_for_pr_checks(
    repo: Path, pr_url: str, timeout_s: int, poll_s: int = 30
) -> str:
    """Block until `pr_url`'s checks resolve, it merges/closes, or `timeout_s`.

    PR pacing support: called between group PR creations so N stacked/sibling
    PRs don't hit a shared CI runner pool simultaneously. Best-effort by
    design — returns a short outcome string ('merged', 'closed', 'checks-done',
    'no-checks', 'timeout', 'error') and NEVER raises: a red or stuck PR must
    not deadlock the rest of the integration (quarantine/CI-fix flows own
    failures; this function only paces).

    statusCheckRollup entries are either CheckRun ({status, conclusion}) or
    StatusContext ({state}); both shapes are treated as pending until terminal.
    An empty rollup two polls in a row means the repo reports no checks for
    this PR (e.g. no CI wired) — treated as done rather than waiting out the
    full timeout.
    """
    deadline = time.monotonic() + timeout_s
    empty_polls = 0
    while time.monotonic() < deadline:
        view = subprocess.run(
            ["gh", "pr", "view", pr_url, "--json", "state,statusCheckRollup"],
            capture_output=True,
            text=True,
            cwd=str(repo),
        )
        if view.returncode != 0:
            return "error"
        try:
            data = json.loads(view.stdout)
        except Exception:
            return "error"
        state = data.get("state", "")
        if state in ("MERGED", "CLOSED"):
            return state.lower()
        rollup = data.get("statusCheckRollup") or []
        if not rollup:
            empty_polls += 1
            if empty_polls >= 2:
                return "no-checks"
        else:
            empty_polls = 0
            pending = [
                c
                for c in rollup
                if (
                    # CheckRun shape
                    (c.get("status") or "").upper() not in ("", "COMPLETED")
                    # StatusContext shape
                    or (c.get("state") or "").upper() in ("PENDING", "EXPECTED")
                )
            ]
            if not pending:
                return "checks-done"
        time.sleep(poll_s)
    return "timeout"


def finish_real(
    repo: Path,
    spec_id: str,
    tasks: list,
    remote: str,
    run_id: str,
    base: str,
    cleanup: bool = True,
    journal_path: Optional[str] = None,
    smoke_cmd: Optional[str] = None,
    assembly_resolve_spawn=None,
    pr_labels: Optional[list[str]] = None,
    pr_pacing_wait: int = 0,
    route: Optional[str] = None,
    gates: str = "",
    migration_patterns: Optional[list[str]] = None,
) -> tuple:
    """Integrate per-group branches into the REAL repo and open PRs against `base`.

    Unlike finish() (which force-pushes to a sandbox to isolate PR diffs), this
    function pushes group branches to the repo's existing `remote` and opens PRs
    with --base=base. The group branches are stacked the same way (foundational
    base group -> feature groups), but PRs target the real repo origin.

    On resume (when group branches/PRs already exist on remote), reconciles with
    existing state: reuses open PRs, skips merged groups, does not force-reset
    existing branches (AC-005, AC-006, AC-007).

    If journal_path is provided, writes per-group integrate records atomically as
    each group is integrated, then writes the integrate_complete marker after all
    groups are processed (AC-019, AC-021, AC-024).

    Labels are refreshed immediately before each new group PR creation via
    _refresh_pr_labels(), so every fresh PR reflects the current policy and
    required-check state. Recycled/open PRs keep their original labels.

    Expressed as a loop over integrate_one so the per-group seam is callable
    independently by the pipeline scheduler (AC-009).
    """
    groups = coordinator.plan_groups(tasks, migration_patterns=migration_patterns or ())
    status = {t["id"]: t.get("status", "done") for t in tasks}
    group_branch: dict = {}
    quarantined: dict = {}
    prs = []

    # Spec-folder ownership: only the first independent group (the "base" group when it
    # exists, otherwise the first feature group) carries docs/specs/<spec_id>/. All other
    # independent siblings reset the spec folder to avoid add/add conflicts when they merge.
    _spec_carrier = next((g["name"] for g in groups if not g.get("depends_on")), None)
    if _spec_carrier is None and groups:
        _spec_carrier = groups[0]["name"]

    for g in groups:
        # PR pacing: before integrating the next group, wait (bounded) for the
        # previous group's PR checks to resolve so sibling PRs don't slam a
        # shared CI runner pool simultaneously — the standing "open orchestrator
        # PRs sequentially" rule, enforced in code. With auto-merge repos the
        # previous PR typically merges during the wait, which also gives
        # dependent groups a real merged base instead of a moving stacked diff.
        if pr_pacing_wait > 0 and prs:
            prev_name, _, prev_url = prs[-1]
            print(f"  PACE [{prev_name:9}] waiting on PR checks "
                  f"(max {pr_pacing_wait}s): {prev_url}")
            outcome = _wait_for_pr_checks(repo, prev_url, pr_pacing_wait)
            print(f"  PACE [{prev_name:9}] {outcome}")
        _strip = not g.get("depends_on") and g["name"] != _spec_carrier
        result = integrate_one(
            g, repo, spec_id, tasks, remote, run_id, base,
            journal_path, status, group_branch, quarantined,
            strip_spec_folder=_strip,
            smoke_cmd=smoke_cmd,
            assembly_resolve_spawn=assembly_resolve_spawn,
            pr_labels=pr_labels,
            route=route,
            gates=gates,
        )
        if result is not None:
            prs.append(result)

    if quarantined:
        print("QUARANTINED groups (need human review):")
        for n, reason in quarantined.items():
            print(f"  - {n}: {reason}")

    _mark_integrate_complete_if_terminal(journal_path, groups, tasks)

    if cleanup:
        print("cleanup: closing PRs + deleting remote branches ...")
        remote_url = _git(repo, "remote", "get-url", remote).stdout.strip()
        gh_repo = (
            remote_url.removesuffix(".git").split("github.com", 1)[-1].lstrip(":/")
            if "github.com" in remote_url
            else None
        )
        for g in reversed(groups):
            gb = group_branch.get(g["name"])
            if gb and gh_repo:
                subprocess.run(
                    ["gh", "pr", "close", "--repo", gh_repo, gb, "--delete-branch"],
                    capture_output=True,
                    text=True,
                )
    return prs, group_branch, quarantined


def validate(sandbox: str, keep: bool = False) -> list:
    repo = live.instantiate(live.SAMPLE_TEMPLATE)
    spec_id, tasks = taskformats.load_spec(str(repo / live.SAMPLE_SPEC_REL))
    base = current_branch(repo)
    run_id = f"val-{int(time.time())}"
    print(
        f"=== synthetic fan-out: {len([t for t in tasks if t.get('kind','impl') not in TAIL])} impl branches ==="
    )
    synthetic_fanout(repo, spec_id, tasks, base)
    print(f"=== integrate + grouped PRs (run {run_id}) on {sandbox} ===")
    prs, _, _ = finish(repo, spec_id, tasks, sandbox, run_id, base, cleanup=not keep)
    print(
        f"=== done: {len(prs)} group PRs{' (kept for inspection)' if keep else ' created + cleaned up'} ==="
    )
    return prs


def finish_existing(dest, sandbox: str, keep: bool = False) -> list:
    """Integrate + PR an existing repo that already has per-task branches from a
    live fan-out (no synthetic commits, no spawns)."""
    repo = Path(dest).resolve()
    spec_id, tasks = taskformats.load_spec(str(repo / live.SAMPLE_SPEC_REL))
    base = current_branch(repo)
    run_id = f"live-{int(time.time())}"
    print(f"=== integrate existing fan-out ({spec_id}) run {run_id} on {sandbox} ===")
    prs, _, _ = finish(repo, spec_id, tasks, sandbox, run_id, base, cleanup=not keep)
    print(f"=== {len(prs)} group PRs{' (kept)' if keep else ' created + cleaned up'} ===")
    return prs


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Integration + grouped PRs")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="Synthetic (no-spawn) integrate+PR validation")
    v.add_argument("--sandbox", default="behindthedash/orch-sandbox")
    v.add_argument("--keep", action="store_true", help="leave the branches/PRs for inspection")
    fe = sub.add_parser("finish-existing", help="Integrate+PR an already-fanned-out repo")
    fe.add_argument("--dest", default=str(live.DEFAULT_DEST))
    fe.add_argument("--sandbox", default="behindthedash/orch-sandbox")
    fe.add_argument("--keep", action="store_true")
    args = p.parse_args(argv)
    if args.cmd == "validate":
        validate(args.sandbox, args.keep)
    if args.cmd == "finish-existing":
        finish_existing(args.dest, args.sandbox, args.keep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
