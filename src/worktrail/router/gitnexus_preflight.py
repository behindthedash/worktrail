"""Bounded, read-only GitNexus capability check for orchestrated runs.

The MCP registry describes canonical checkouts, while task worktrees are
deliberately not indexed.  This check therefore answers only whether the
canonical repository has a usable base-branch index; it never indexes a
worktree and never treats an unavailable index as a task failure.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Optional


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=10,
    )


def canonical_repo_root(repo: Path, runner: Runner = _run_git) -> Optional[Path]:
    """Resolve a linked worktree to the checkout owning its shared git dir."""
    try:
        result = runner(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    common_dir = Path(result.stdout.strip())
    if common_dir.name == ".git":
        return common_dir.parent.resolve()
    return None


def _registry_path(registry_path: Optional[Path]) -> Path:
    raw = registry_path or Path(
        os.environ.get("GITNEXUS_REGISTRY", "~/.gitnexus/registry.json")
    )
    return raw.expanduser()


def check(repo: Path, registry_path: Optional[Path] = None) -> Dict[str, Any]:
    """Return an explicit ``available`` or ``unavailable`` capability result."""
    repo = Path(repo).resolve()
    canonical = canonical_repo_root(repo)
    result: Dict[str, Any] = {
        "capability": "gitnexus",
        "status": "unavailable",
        "canonical_repo": str(canonical) if canonical else None,
        "registry": str(_registry_path(registry_path)),
    }
    if canonical is None:
        result["reason"] = "canonical-repo-unavailable"
        return result

    path = _registry_path(registry_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        result["reason"] = "registry-missing"
        return result
    except (OSError, json.JSONDecodeError):
        result["reason"] = "registry-unavailable"
        return result

    entries = data if isinstance(data, list) else data.get("repositories", [])
    if not isinstance(entries, list):
        result["reason"] = "registry-invalid"
        return result
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("path"):
            continue
        try:
            indexed = Path(entry["path"]).expanduser().resolve()
        except (OSError, TypeError, ValueError):
            continue
        storage = entry.get("storagePath")
        if indexed == canonical and (not storage or Path(storage).expanduser().exists()):
            result.update({
                "status": "available",
                "reason": "canonical-base-index-registered",
                "name": entry.get("name"),
                "indexed_commit": entry.get("lastCommit"),
            })
            return result

    result["reason"] = "canonical-base-index-missing"
    return result


def prompt_note(capability: Dict[str, Any]) -> str:
    """Render the worker instruction for the capability result."""
    if capability.get("status") == "available":
        return (
            "GitNexus capability preflight: available for the canonical base checkout. "
            "Use it only for base-branch context; the current worktree remains ground truth."
        )
    return (
        "GitNexus capability preflight: unavailable ("
        f"{capability.get('reason', 'unknown')}). Proceed using the actual worktree's "
        "rg/read/test evidence; do not auto-index or create a worktree-local GitNexus index."
    )
