"""Mechanical premise check: verify a brief's factual claims before an agent
spends a turn evaluating it.

A brief's focus text routinely asserts things that are trivial to check
mechanically -- a quoted log line that should appear somewhere in the repo, a
path that should exist, a command that should reproduce a failure -- but that
an evaluating agent otherwise has to re-derive from scratch (or, worse, take
on faith). `extract_needles` pulls these claims out of free-form prose;
`run_premise_check` confirms or refutes each one against a repo checkout, and
`format_premise_block` renders the result for inclusion in an evaluation
prompt. See design D3 (autonomous-intake-brief-convergence) for the contract.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..router.brief_probes import extract_probes

# Commands recognized as safe/meaningful to actually run. Anything else that
# looks command-shaped is recorded as a needle but never executed.
_ALLOWED_COMMANDS = frozenset(
    {
        "pytest",
        "python -m pytest",
        "python3 -m pytest",
        "npm test",
        "go test",
        "cargo test",
        "ruff check",
        "mypy",
    }
)

# First-token verbs that mark a quoted string as command-shaped even though
# it is not allow-listed to actually run -- recorded as an unrunnable
# `command` needle instead of silently dropped.
_COMMAND_LOOKING_VERBS = frozenset(
    {
        "rm",
        "git",
        "make",
        "curl",
        "wget",
        "sudo",
        "docker",
        "npm",
        "pip",
        "pip3",
        "bash",
        "sh",
        "chmod",
        "chown",
        "kill",
        "mv",
        "cp",
    }
)


def _looks_command_shaped(stripped: str) -> bool:
    first = stripped.split(None, 1)[0] if stripped else ""
    return first in _COMMAND_LOOKING_VERBS or first.startswith("worktrail-")


_QUOTED_RE = re.compile(r"'([^']+)'|\"([^\"]+)\"|`([^`]+)`")

_MIN_QUOTED_LEN = 12
_MIN_FRAGMENT_LEN = 12
_MAX_GIT_GREP_HITS = 5
_MAX_OUTPUT_LINES = 20


@dataclass(frozen=True)
class Needle:
    kind: str  # "quoted" | "path" | "command"
    needle: str
    line: int


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _extract_quoted_needles(focus: str) -> list[Needle]:
    needles: list[Needle] = []
    for match in _QUOTED_RE.finditer(focus):
        value = next(g for g in match.groups() if g is not None)
        if len(value) < _MIN_QUOTED_LEN:
            continue
        needles.append(Needle("quoted", value, _line_of(focus, match.start())))
    return needles


def _extract_path_needles(focus: str) -> list[Needle]:
    probes = extract_probes(focus)
    needles: list[Needle] = []
    for path in probes.get("paths", []):
        index = focus.find(path)
        line = _line_of(focus, index) if index >= 0 else 1
        needles.append(Needle("path", path, line))
    return needles


def _extract_command_needles(focus: str) -> list[Needle]:
    needles: list[Needle] = []
    seen: set[str] = set()
    for match in _QUOTED_RE.finditer(focus):
        value = next(g for g in match.groups() if g is not None)
        stripped = value.strip()
        if not stripped or stripped in seen:
            continue
        allow_listed = any(
            stripped == allowed or stripped.startswith(allowed + " ")
            for allowed in _ALLOWED_COMMANDS
        )
        if not allow_listed and not _looks_command_shaped(stripped):
            continue
        seen.add(stripped)
        needles.append(Needle("command", stripped, _line_of(focus, match.start())))
    return needles


def extract_needles(focus: str) -> list[Needle]:
    """Extract quoted, path, and (allow-listed) command needles from `focus`.

    Returns needles in the order: quoted, then path, then command, mirroring
    the order the premise check confirms them in.
    """
    focus = focus or ""
    return (
        _extract_quoted_needles(focus)
        + _extract_path_needles(focus)
        + _extract_command_needles(focus)
    )


def _git_grep_whole_string(repo_path: Path, needle: str) -> dict[str, Any] | None:
    result = subprocess.run(
        ["git", "grep", "-nIF", "-e", needle],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    hits = result.stdout.strip("\n").split("\n")[:_MAX_GIT_GREP_HITS]
    return {
        "confirmed": True,
        "detail": f"matched whole string in: {'; '.join(hits)}",
    }


def _fragments(needle: str) -> list[str]:
    parts: list[str] = []
    for sep in ("...", "…", ": "):
        for chunk in needle.split(sep):
            chunk = chunk.strip()
            if len(chunk) >= _MIN_FRAGMENT_LEN:
                parts.append(chunk)
    return parts


def _git_grep_fragments(repo_path: Path, needle: str) -> dict[str, Any]:
    for fragment in _fragments(needle):
        result = subprocess.run(
            ["git", "grep", "-nIF", "-e", fragment],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            hits = result.stdout.strip("\n").split("\n")[:_MAX_GIT_GREP_HITS]
            first_file = hits[0].split(":", 1)[0]
            return {
                "confirmed": True,
                "detail": (
                    f"whole string not found; fragment {fragment!r} matched in "
                    f"{first_file} ({'; '.join(hits)})"
                ),
            }
    return {"confirmed": False, "detail": "no match for whole string or fragments"}


def _check_quoted(repo_path: Path, needle: str) -> dict[str, Any]:
    whole = _git_grep_whole_string(repo_path, needle)
    if whole is not None:
        return whole
    return _git_grep_fragments(repo_path, needle)


def _check_path(repo_path: Path, needle: str) -> dict[str, Any]:
    rel, _, line_str = needle.rpartition(":")
    if rel and line_str.isdigit():
        candidate, line_num = rel, int(line_str)
    else:
        candidate, line_num = needle, None
    target = repo_path / candidate
    if not target.exists():
        return {"confirmed": False, "detail": f"path does not exist: {candidate}"}
    if line_num is None:
        return {"confirmed": True, "detail": f"path exists: {candidate}"}
    try:
        line_count = sum(1 for _ in target.open("r", errors="replace"))
    except OSError as exc:
        return {
            "confirmed": True,
            "detail": f"path exists: {candidate}, but could not count lines ({exc})",
        }
    if line_num > line_count:
        return {
            "confirmed": False,
            "detail": (
                f"path exists but has only {line_count} lines, "
                f"needle cites line {line_num}"
            ),
        }
    return {
        "confirmed": True,
        "detail": f"path exists: {candidate} ({line_count} lines, line {line_num} present)",
    }


def _check_command(
    repo_path: Path, needle: str, timeout_s: int, already_ran: bool
) -> dict[str, Any]:
    if needle not in _ALLOWED_COMMANDS and not any(
        needle.startswith(allowed + " ") for allowed in _ALLOWED_COMMANDS
    ):
        return {"confirmed": False, "detail": "command not allow-listed; not run"}
    if already_ran:
        return {
            "confirmed": False,
            "detail": "skipped: another command needle already ran",
        }
    try:
        result = subprocess.run(
            shlex.split(needle),
            cwd=repo_path,
            timeout=timeout_s,
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"confirmed": False, "detail": f"timed out after {timeout_s}s"}
    output = (result.stdout or "") + (result.stderr or "")
    tail = "\n".join(output.splitlines()[-_MAX_OUTPUT_LINES:])
    return {
        "confirmed": result.returncode != 0,
        "detail": f"exit code {result.returncode}\n{tail}",
    }


def run_premise_check(
    focus: str, repo_path: str | Path, *, timeout_s: int = 120
) -> list[dict[str, Any]]:
    """Confirm or refute each needle extracted from `focus` against `repo_path`.

    Returns a list of `{kind, needle, confirmed, detail}` in extraction order.
    At most one `command` needle is actually executed; any additional command
    needles are recorded unconfirmed without running.
    """
    repo_path = Path(repo_path)
    needles = extract_needles(focus)
    results: list[dict[str, Any]] = []
    command_ran = False
    for n in needles:
        if n.kind == "quoted":
            outcome = _check_quoted(repo_path, n.needle)
        elif n.kind == "path":
            outcome = _check_path(repo_path, n.needle)
        elif n.kind == "command":
            outcome = _check_command(repo_path, n.needle, timeout_s, command_ran)
            if n.needle in _ALLOWED_COMMANDS or any(
                n.needle.startswith(a + " ") for a in _ALLOWED_COMMANDS
            ):
                command_ran = True
        else:
            outcome = {"confirmed": False, "detail": "unknown needle kind"}
        results.append(
            {
                "kind": n.kind,
                "needle": n.needle,
                "confirmed": outcome["confirmed"],
                "detail": outcome["detail"],
            }
        )
    return results


def format_premise_block(results: list[dict[str, Any]]) -> str:
    """Render `run_premise_check` results for inclusion in an evaluation prompt."""
    if not results:
        return "(none)"
    lines = []
    for r in results:
        status = "CONFIRMED" if r["confirmed"] else "UNCONFIRMED"
        lines.append(f"- [{status}] {r['kind']}: {r['needle']!r} -- {r['detail']}")
    return "\n".join(lines)
