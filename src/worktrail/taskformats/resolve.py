"""Pick the right `TaskSource` for a spec/change path.

The orchestrator is handed a filesystem path to a unit of work and must not care
which authoring format produced it. This module is the single place that decides,
so `orchestrator/` never imports a concrete adapter.

**This is transition-scoped.** The devkit `docs/specs/` format is being converted
to OpenSpec; once no repo has a `docs/specs/` tree left, `taskformats/devkit/`
and the `"devkit"` branch below are deleted together and this module collapses to
constructing `OpenSpecTaskSource`. Detection is deliberately a single function
with one branch so that removal is a deletion, not an untangling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from worktrail.taskformats.devkit import source as devkit
from worktrail.taskformats.openspec import source as openspec

FORMAT_OPENSPEC = "openspec"
FORMAT_DEVKIT = "devkit"


def detect_format(spec_path: Path | str) -> str:
    """Return the format that owns *spec_path*.

    Decided by the containing directory, not by inspecting contents: the caller
    may be pointing at a spec folder that does not exist yet (a fresh worktree
    branched before its tasks commit landed), and detection must still work.
    """
    parts = Path(spec_path).parts
    for i in range(len(parts) - 1):
        if parts[i] == "openspec" and parts[i + 1] == "changes":
            return FORMAT_OPENSPEC
    return FORMAT_DEVKIT


def _split(spec_path: Path, fmt: str) -> Tuple[Path, str]:
    """Split an absolute spec path into `(repo_root, spec_ref)`.

    Both adapters are constructed with a repo root and addressed by a short ref,
    but the orchestrator only ever holds the joined path -- this is the inverse.
    """
    spec_path = Path(spec_path)
    spec_ref = spec_path.name
    depth = len(Path(
        openspec.DEFAULT_SPEC_ROOT if fmt == FORMAT_OPENSPEC else devkit.DEFAULT_SPEC_ROOT
    ).parts)
    repo_root = spec_path
    for _ in range(depth + 1):  # +1 for the spec_ref segment itself
        repo_root = repo_root.parent
    return repo_root, spec_ref


def task_source_for(spec_path: Path | str):
    """Construct the `TaskSource` that owns *spec_path*."""
    spec_path = Path(spec_path)
    fmt = detect_format(spec_path)
    repo_root, _ = _split(spec_path, fmt)
    if fmt == FORMAT_OPENSPEC:
        return openspec.OpenSpecTaskSource(repo_root)
    return devkit.DevkitSpecTaskSource(repo_root)


def load_spec(spec_path: Path | str) -> Tuple[str, List[Dict[str, Any]]]:
    """Format-agnostic replacement for `devkit.source.load_spec()`.

    Same `(spec_id, tasks)` contract, so the orchestrator's existing call sites
    change only which module they import from.
    """
    spec_path = Path(spec_path)
    fmt = detect_format(spec_path)
    if fmt == FORMAT_OPENSPEC:
        repo_root, spec_ref = _split(spec_path, fmt)
        return openspec.OpenSpecTaskSource(repo_root).load(spec_ref)
    # devkit's loader takes the folder path directly and tolerates the several
    # tasks/ layouts it has accumulated (see _find_tasks_dir); go through it
    # rather than reimplementing that lookup here.
    return devkit.load_spec(str(spec_path))


def spec_root_prefix_for(spec_path: Path | str) -> str:
    """The path prefix a worker must never write to, for this spec's format.

    Feeds `verify.FORBIDDEN_WORKER_PATH_PREFIXES` and the worker prompt's hard
    rules. Getting this wrong is a real safety hole, not a cosmetic one: it is
    what stops a worker rewriting the spec it is implementing against.
    """
    fmt = detect_format(spec_path)
    if fmt == FORMAT_OPENSPEC:
        return openspec.OpenSpecTaskSource(Path(".")).spec_root_prefix()
    return devkit.DevkitSpecTaskSource(Path(".")).spec_root_prefix()
