"""Dependency freshness precondition for triage.

Before a triage evaluator reproduces a brief's failing `npm test`, check that
each tracked npm package root's `node_modules/` actually matches its
`package-lock.json`. A stale or half-installed tree produces failures that say
nothing about the brief, so the result is fed to the premise check (which
skips `npm test`) and rendered into the evaluator prompt. Read-only: this
module never writes into the checkout.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

_LOCKFILE = "package-lock.json"


def _tracked_lockfiles(repo_path: Path) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--", _LOCKFILE, f"*/{_LOCKFILE}"],
            cwd=repo_path,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _check_root(repo_path: Path, lockfile_rel: str) -> dict[str, Any]:
    lockfile = repo_path / lockfile_rel
    root = lockfile.parent
    app_dir = str(root.relative_to(repo_path)) if root != repo_path else "."
    result: dict[str, Any] = {
        "app_dir": app_dir,
        "lockfile": lockfile_rel,
        "status": "unknown",
        "mismatches": [],
        "detail": "",
    }

    data = _read_json(lockfile)
    if not isinstance(data, dict):
        result["detail"] = f"{lockfile_rel}: unparseable JSON"
        return result
    packages = data.get("packages")
    if not isinstance(packages, dict):
        result["detail"] = f"{lockfile_rel}: no `packages` map (lockfileVersion < 2?)"
        return result

    root_entry = packages.get("", {})
    if not isinstance(root_entry, dict):
        root_entry = {}
    names: list[str] = []
    for key in ("dependencies", "devDependencies"):
        deps = root_entry.get(key)
        if isinstance(deps, dict):
            names.extend(n for n in deps if n not in names)

    mismatches: list[dict[str, str]] = []
    for name in names:
        locked_entry = packages.get(f"node_modules/{name}")
        locked = locked_entry.get("version") if isinstance(locked_entry, dict) else None
        if not isinstance(locked, str):
            continue
        installed_dir = root / "node_modules" / name
        if not installed_dir.is_dir():
            mismatches.append({"name": name, "locked": locked, "installed": "missing"})
            continue
        installed_data = _read_json(installed_dir / "package.json")
        installed = (
            installed_data.get("version") if isinstance(installed_data, dict) else None
        )
        if not isinstance(installed, str):
            # Directory present but its manifest is unreadable: we cannot tell
            # whether the tree is fresh or stale, so the root is `unknown`.
            result["detail"] = (
                f"{app_dir}/node_modules/{name}/package.json: unparseable JSON"
                " or no `version`"
            )
            return result
        if installed != locked:
            mismatches.append({"name": name, "locked": locked, "installed": installed})

    result["mismatches"] = mismatches
    if mismatches:
        result["status"] = "stale"
        summary = ", ".join(
            f"{m['name']} {m['locked']} -> {m['installed']}" for m in mismatches
        )
        result["detail"] = f"{app_dir}: {len(mismatches)} mismatch(es): {summary}"
    else:
        result["status"] = "fresh"
        result["detail"] = (
            f"{app_dir}: {len(names)} pinned package(s) match node_modules"
        )
    return result


def check_dependency_freshness(repo_path: str | Path) -> list[dict[str, Any]]:
    """Compare each tracked `package-lock.json` root against its `node_modules/`.

    Returns one entry per root: `{app_dir, lockfile, status, mismatches, detail}`
    where `status` is `fresh`, `stale`, or `unknown`.
    """
    repo = Path(repo_path)
    return [_check_root(repo, rel) for rel in _tracked_lockfiles(repo)]


def format_freshness_block(results: list[dict[str, Any]]) -> str:
    """Render freshness results as prompt-ready lines."""
    if not results:
        return "- no npm package roots found (no tracked package-lock.json)"
    lines: list[str] = []
    for entry in results:
        lines.append(f"- {entry['app_dir']} ({entry['lockfile']}): {entry['status']}")
        if entry.get("detail"):
            lines.append(f"  {entry['detail']}")
        for m in entry.get("mismatches", []):
            lines.append(
                f"  - {m['name']}: locked {m['locked']}, installed {m['installed']}"
            )
    return "\n".join(lines)
