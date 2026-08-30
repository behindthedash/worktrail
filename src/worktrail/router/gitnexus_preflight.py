"""Bounded, read-only GitNexus capability check for orchestrated runs.

The MCP registry describes canonical checkouts, while task worktrees are
deliberately not indexed.  This check therefore answers only whether the
canonical repository has a usable base-branch index; it never indexes a
worktree and never treats an unavailable index as a task failure.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

Runner = Callable[..., subprocess.CompletedProcess[str]]
McpRunner = Callable[..., subprocess.CompletedProcess[str]]


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _run_mcp(
    command: list[str], *, input: str, timeout: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        input=input,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def canonical_repo_root(repo: Path, runner: Runner = _run_git) -> Path | None:
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


def _registry_path(registry_path: Path | None) -> Path:
    raw = registry_path or Path(
        os.environ.get("GITNEXUS_REGISTRY", "~/.gitnexus/registry.json")
    )
    return raw.expanduser()


def _mcp_command() -> list[str]:
    configured = os.environ.get("GITNEXUS_MCP_COMMAND")
    return shlex.split(configured) if configured else ["gitnexus", "mcp"]


def _mcp_probe_payload(repo_name: str) -> str:
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "worktrail-capability-probe", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "resources/read",
            "params": {"uri": f"gitnexus://repo/{repo_name}/context"},
        },
    ]
    return "".join(json.dumps(message) + "\n" for message in messages)


def _mcp_responded(stdout: str) -> bool:
    responses: dict[Any, dict[str, Any]] = {}
    for line in stdout.splitlines():
        try:
            response = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(response, dict) and "id" in response:
            responses[response["id"]] = response
    initialized = responses.get(1, {}).get("result", {}).get("serverInfo", {})
    resource = responses.get(2, {}).get("result", {}).get("contents")
    return (
        initialized.get("name") == "gitnexus"
        and isinstance(resource, list)
        and bool(resource)
    )


def check(
    repo: Path,
    registry_path: Path | None = None,
    *,
    runner: Runner = _run_git,
    mcp_runner: McpRunner = _run_mcp,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Return an explicit ``available`` or ``unavailable`` capability result."""
    repo = Path(repo).resolve()
    canonical = canonical_repo_root(repo, runner=runner)
    result: dict[str, Any] = {
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
        if indexed != canonical or (
            storage and not Path(storage).expanduser().exists()
        ):
            continue

        name = entry.get("name")
        indexed_commit = entry.get("lastCommit")
        result.update({"name": name, "indexed_commit": indexed_commit})
        if not isinstance(name, str) or not name:
            result["reason"] = "registry-invalid"
            return result

        try:
            head = runner(canonical, "rev-parse", "HEAD")
        except (OSError, subprocess.SubprocessError):
            head = None
        if (
            indexed_commit
            and head is not None
            and head.returncode == 0
            and indexed_commit != head.stdout.strip()
        ):
            result.update(
                {"reason": "registry-stale", "current_commit": head.stdout.strip()}
            )
            return result

        try:
            probe = mcp_runner(
                _mcp_command(), input=_mcp_probe_payload(name), timeout=timeout
            )
        except subprocess.TimeoutExpired:
            result["reason"] = "mcp-timeout"
            return result
        except (OSError, subprocess.SubprocessError, ValueError):
            result["reason"] = "mcp-unavailable"
            return result

        if probe.returncode != 0 or not _mcp_responded(probe.stdout):
            result["reason"] = "mcp-unavailable"
            return result

        result.update({"status": "available", "reason": "mcp-context-readable"})
        return result

    result["reason"] = "canonical-base-index-missing"
    return result


def prompt_note(capability: dict[str, Any]) -> str:
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
