"""Codex sandbox argv for every worktrail launch site.

One helper decides the `-s workspace-write` flag, the network-access override,
and the writable roots (`--add-dir`) a Codex child gets. Call sites
(`router/skill_dispatch.py`, `orchestrator/spawnlib.py`, `drain/drain.py`)
compose their argv from it instead of hardcoding `danger-full-access`.

Default root set (design D3): the child cwd, its git common dir (a linked
worktree's objects live there), the operator state dir, the work-queue root,
`<repo>-worktrees` when `repo` is given, caller extras, and any
`WORKTRAIL_CODEX_EXTRA_WRITABLE_ROOTS` entries. `/tmp` / `$TMPDIR` are
writable by Codex default and not repeated. Nonexistent roots are still
emitted -- Codex tolerates them and several are lazily created at first write.

Escape hatches (design D5) are env vars, never policy fields:
`WORKTRAIL_CODEX_SANDBOX_MODE=danger-full-access` prints a stderr notice on
every launch; any other value than the two modes raises `ValueError`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

from worktrail.shared.homedir import worktrail_home

SANDBOX_MODE_ENV = "WORKTRAIL_CODEX_SANDBOX_MODE"
EXTRA_ROOTS_ENV = "WORKTRAIL_CODEX_EXTRA_WRITABLE_ROOTS"

WORKSPACE_WRITE = "workspace-write"
DANGER_FULL_ACCESS = "danger-full-access"
_MODES = (WORKSPACE_WRITE, DANGER_FULL_ACCESS)

NETWORK_ACCESS_OVERRIDE = "sandbox_workspace_write.network_access=true"


def git_common_dir(cwd: str | Path) -> Path | None:
    """Absolute git common dir for *cwd* (the shared `.git` a linked worktree's
    objects live in), or None when cwd is not a git checkout."""
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(cwd),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    return Path(out) if out else None


def work_queue_root() -> Path:
    """`$WORK_QUEUE_DIR`, else `~/work-queue` (the workqueue package's rule)."""
    return Path(os.environ.get("WORK_QUEUE_DIR", "~/work-queue")).expanduser()


def sandbox_mode() -> str:
    """Resolve the sandbox mode from `WORKTRAIL_CODEX_SANDBOX_MODE`.

    Unset/empty means `workspace-write`. `danger-full-access` is honored with
    a stderr notice on every call so an override cannot linger silently.
    """
    raw = os.environ.get(SANDBOX_MODE_ENV, "").strip()
    if not raw:
        return WORKSPACE_WRITE
    if raw not in _MODES:
        raise ValueError(
            f"{SANDBOX_MODE_ENV}={raw!r} is not one of {', '.join(_MODES)}"
        )
    if raw == DANGER_FULL_ACCESS:
        print(
            f"codex sandbox: {DANGER_FULL_ACCESS} ({SANDBOX_MODE_ENV} override)",
            file=sys.stderr,
        )
    return raw


def writable_roots(
    cwd: str | Path,
    repo: str | Path | None = None,
    extra_roots: Iterable[str | Path] = (),
) -> list[Path]:
    """The de-duplicated, stably ordered writable root set for a Codex child."""
    cwd = Path(cwd)
    candidates: list[Path] = [cwd]
    common = git_common_dir(cwd)
    if common is not None:
        candidates.append(common)
    candidates.append(worktrail_home())
    candidates.append(work_queue_root())
    if repo is not None:
        repo = Path(repo)
        candidates.append(repo.parent / f"{repo.name}-worktrees")
    candidates.extend(Path(r) for r in extra_roots)
    env_extra = os.environ.get(EXTRA_ROOTS_ENV, "")
    candidates.extend(
        Path(entry).expanduser() for entry in env_extra.split(os.pathsep) if entry
    )
    roots: list[Path] = []
    for candidate in candidates:
        if candidate not in roots:
            roots.append(candidate)
    return roots


def codex_sandbox_args(
    cwd: str | Path,
    repo: str | Path | None = None,
    extra_roots: Iterable[str | Path] = (),
) -> list[str]:
    """Codex argv fragment: sandbox flag, network override, one `--add-dir` per root.

    Under `danger-full-access` (env override) the flag alone is emitted; the
    roots are meaningless without a sandbox.
    """
    mode = sandbox_mode()
    args = ["-s", mode]
    if mode == DANGER_FULL_ACCESS:
        return args
    args += ["-c", NETWORK_ACCESS_OVERRIDE]
    for root in writable_roots(cwd, repo=repo, extra_roots=extra_roots):
        args += ["--add-dir", str(root)]
    return args
