#!/usr/bin/env python3
"""
Cross-repo live-activity view: "what is running right now, across every repo."

`dashboard.py`'s `_repo_busy_reason` only answers this for the ONE repo a
specific queue brief targets, via a lock-file probe. No single command answers
"what orchestrator runs are live right now, across every repo on this
machine" -- ground truth from the process table means future lock-convention
drift (new nesting depth, a new orchestrator variant) can't reopen that bug
class the way a lock-file-only view can.

Scans `/proc` (stdlib-only, matching this plugin's convention -- no psutil
dependency) for `parallel-orchestrator/scripts/live.py ... full-real`
processes, extracts `--repo`/`--spec`, an approximate start time (the /proc/
<pid> directory's mtime -- close enough for a "how long has this been
running" diagnostic, not exact clock-tick accounting), and counts immediate
child processes whose argv[0] is one of the supported agent CLIs (claude/
codex/opencode) as the active worker count. Cross-references with a recursive
lock scan under each repo's `<repo>-worktrees/` tree so a lock with no
matching live process (crashed run, stale file) is surfaced too, not silently
dropped.

Non-Linux (no /proc): degrades to an empty process list rather than raising --
best-effort, matching every other guard in this plugin.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

_AGENT_BINARIES = {"claude", "codex", "opencode"}


# --------------------------------------------------------------------------- #
# Pure /proc parsing (proc_root is injectable for tests -- no real subprocess
# needed to exercise this logic; see test_live_status.py's fake /proc trees).
# --------------------------------------------------------------------------- #
def _read_cmdline(pid_dir: Path) -> list[str]:
    try:
        raw = (pid_dir / "cmdline").read_bytes()
    except OSError:
        return []
    return [p for p in raw.decode("utf-8", errors="replace").split("\0") if p]


def _read_ppid(pid_dir: Path) -> int | None:
    try:
        text = (pid_dir / "status").read_text(errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("PPid:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def list_procs(proc_root: Path = Path("/proc")) -> list[dict[str, Any]]:
    """Every live process visible under `proc_root`: `{pid, ppid, argv, start_time}`.

    `start_time` is the pid directory's mtime (epoch seconds) -- an
    approximation of process start, not exact `/proc/[pid]/stat` clock-tick
    accounting; sufficient for a "how long has this run been going" diagnostic.
    """
    procs: list[dict[str, Any]] = []
    if not proc_root.is_dir():
        return procs
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        argv = _read_cmdline(entry)
        if not argv:
            continue
        try:
            start_time = entry.stat().st_mtime
        except OSError:
            continue
        procs.append(
            {
                "pid": int(entry.name),
                "ppid": _read_ppid(entry),
                "argv": argv,
                "start_time": start_time,
            }
        )
    return procs


def _arg_value(argv: list[str], flag: str) -> str | None:
    for i, tok in enumerate(argv):
        if tok == flag and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith(flag + "="):
            return tok.split("=", 1)[1]
    return None


def find_live_runs(procs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Live `full-real` orchestrator runs among `procs`, each with its active
    worker count (immediate children whose argv[0] basename is a supported
    agent CLI binary)."""
    runs: list[dict[str, Any]] = []
    for p in procs:
        argv = p["argv"]
        joined = " ".join(argv)
        if "live.py" not in joined or "full-real" not in argv:
            continue
        repo = _arg_value(argv, "--repo")
        spec = _arg_value(argv, "--spec")
        workers = [
            c
            for c in procs
            if c["ppid"] == p["pid"] and Path(c["argv"][0]).name in _AGENT_BINARIES
        ]
        runs.append(
            {
                "pid": p["pid"],
                "repo": repo,
                "spec": spec,
                "start_time": p["start_time"],
                "active_workers": len(workers),
            }
        )
    return runs


# --------------------------------------------------------------------------- #
# Lock cross-reference (reuses dashboard.py's flock probe -- one definition,
# not a second copy of the non-blocking-flock-then-release dance).
# --------------------------------------------------------------------------- #
def scan_locks(repos_dir: Path, runlock_held) -> list[dict[str, Any]]:
    """Every currently-held `.lock` file under `<repo>-worktrees/` for each
    repo directly under `repos_dir` (non-recursive at the repos_dir level;
    recursive within each repo's own worktrees tree -- mirrors dashboard.py's
    `_repo_busy_reason` nested-lock fix)."""
    held: list[dict[str, Any]] = []
    if not repos_dir.is_dir():
        return held
    for repo in sorted(repos_dir.iterdir()):
        if not repo.is_dir() or repo.name.endswith("-worktrees"):
            continue
        worktrees = repo.parent / f"{repo.name}-worktrees"
        if not worktrees.is_dir():
            continue
        for lock in sorted(worktrees.rglob("*.lock")):
            if runlock_held(lock):
                held.append({"repo": str(repo), "lock": str(lock)})
    return held


def compute(repos_dir: Path, proc_root: Path, runlock_held) -> dict[str, Any]:
    procs = list_procs(proc_root)
    runs = find_live_runs(procs)
    locks = scan_locks(repos_dir, runlock_held)
    run_lock_paths = set()
    for r in runs:
        if r.get("repo") and r.get("spec"):
            run_lock_paths.add((str(Path(r["repo"]).resolve()), r["spec"]))
    # A held lock with no matching live.py process is either a crashed run or
    # a stale (not actually held -- scan_locks already filtered those out via
    # runlock_held) file; surfaced distinctly so it isn't silently dropped.
    orphaned = [
        l for l in locks if not any(l["repo"] in rp[0] for rp in run_lock_paths)
    ]
    return {"runs": runs, "held_locks": locks, "orphaned_locks": orphaned}


def _fmt_age(start_time: float, now: float) -> str:
    mins = max(0, int((now - start_time) / 60))
    if mins < 60:
        return f"{mins}m"
    return f"{mins // 60}h{mins % 60}m"


def render(result: dict[str, Any]) -> str:
    if not result["runs"] and not result["orphaned_locks"]:
        return "No live orchestrator runs found."
    lines: list[str] = []
    now = time.time()
    for r in result["runs"]:
        repo_name = Path(r["repo"]).name if r.get("repo") else "?"
        lines.append(
            f"  {repo_name} {r.get('spec') or '?'} — PID {r['pid']}, "
            f"{_fmt_age(r['start_time'], now)} running, {r['active_workers']} active worker(s)"
        )
    for o in result["orphaned_locks"]:
        lines.append(
            f"  ⚠️  {Path(o['repo']).name} — held lock with no matching process: {o['lock']}"
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Cross-repo live-activity view")
    p.add_argument(
        "--repos", default="~/projects", help="parent dir containing repo checkouts"
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    from . import dashboard  # local import: avoids a module-load cost when unused

    result = compute(
        Path(args.repos).expanduser(), Path("/proc"), dashboard._runlock_held
    )
    if args.json:
        print(json.dumps(result))
    else:
        print(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
