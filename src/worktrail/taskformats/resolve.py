"""Pick the right `TaskSource` for a spec/change path.

The orchestrator is handed a filesystem path to a unit of work and must not care
which authoring format produced it. This module is the single place that decides,
so `orchestrator/` never imports a concrete adapter.

Both supported formats remain first-class adapters. The legacy devkit adapter is
kept for existing repositories, but callers outside this module never need to
know which adapter owns a spec.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from worktrail.taskformats.devkit import source as devkit
from worktrail.taskformats.openspec import source as openspec
from worktrail.taskformats.speckit import source as speckit

FORMAT_OPENSPEC = "openspec"
FORMAT_DEVKIT = "devkit"
FORMAT_SPECKIT = "speckit"


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
        if parts[i] == ".specify" and parts[i + 1] == "specs":
            return FORMAT_SPECKIT
    return FORMAT_DEVKIT


def _split(spec_path: Path, fmt: str) -> tuple[Path, str]:
    """Split an absolute spec path into `(repo_root, spec_ref)`.

    Both adapters are constructed with a repo root and addressed by a short ref,
    but the orchestrator only ever holds the joined path -- this is the inverse.
    """
    spec_path = Path(spec_path)
    if fmt == FORMAT_DEVKIT:
        # Devkit change specs live below the parent spec:
        # docs/specs/<parent>/changes/<change>/tasks/. Retain the full path
        # below docs/specs instead of collapsing it to the leaf name.
        parts = spec_path.parts
        for i in range(len(parts) - 1):
            if parts[i : i + 2] == ("docs", "specs"):
                repo_root = Path(*parts[:i]) if i else Path(".")
                spec_ref = Path(*parts[i + 2 :]).as_posix()
                return repo_root, spec_ref
        spec_ref = spec_path.name
    else:
        spec_ref = spec_path.name
    depth = len(
        Path(
            openspec.DEFAULT_SPEC_ROOT
            if fmt == FORMAT_OPENSPEC
            else speckit.DEFAULT_SPEC_ROOT
            if fmt == FORMAT_SPECKIT
            else devkit.DEFAULT_SPEC_ROOT
        ).parts
    )
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
    if fmt == FORMAT_SPECKIT:
        return speckit.SpecKitTaskSource(repo_root)
    return devkit.DevkitSpecTaskSource(repo_root)


def load_spec(spec_path: Path | str) -> tuple[str, list[dict[str, Any]]]:
    """Format-agnostic replacement for `devkit.source.load_spec()`.

    Same `(spec_id, tasks)` contract, so the orchestrator's existing call sites
    change only which module they import from.
    """
    spec_path = Path(spec_path)
    fmt = detect_format(spec_path)
    if fmt == FORMAT_OPENSPEC:
        repo_root, spec_ref = _split(spec_path, fmt)
        return openspec.OpenSpecTaskSource(repo_root).load(spec_ref)
    if fmt == FORMAT_SPECKIT:
        repo_root, spec_ref = _split(spec_path, fmt)
        return speckit.SpecKitTaskSource(repo_root).load(spec_ref)
    # Keep support for repository-local fixture/spec roots such as
    # ``specs/001-test`` used by the precheck harness. These paths are not
    # addressed through the repo's docs/specs adapter root.
    if "docs" not in spec_path.parts:
        return devkit.load_spec(str(spec_path))
    repo_root, spec_ref = _split(spec_path, fmt)
    return devkit.DevkitSpecTaskSource(repo_root).load(spec_ref)


def spec_ref_for(spec_path: Path | str) -> str:
    """Return the adapter-local reference for a joined spec/change path."""
    spec_path = Path(spec_path)
    return _split(spec_path, detect_format(spec_path))[1]


def task_brief_ref_for(spec_path: Path | str, task_id: str) -> tuple[str, str]:
    """`(repo-relative path, anchor)` for the brief a worker should open.

    `anchor` is `""` when the file is the whole brief. Feeds the cold-worker
    prompt, which otherwise hardcodes devkit's file-per-task layout and sends an
    OpenSpec worker to a path that does not exist.
    """
    spec_path = Path(spec_path)
    source = task_source_for(spec_path)
    return source.task_brief_ref(task_id, spec_ref_for(spec_path))


def spec_root_prefix_for(spec_path: Path | str) -> str:
    """The path prefix a worker must never write to, for this spec's format.

    Feeds `verify.FORBIDDEN_WORKER_PATH_PREFIXES` and the worker prompt's hard
    rules. Getting this wrong is a real safety hole, not a cosmetic one: it is
    what stops a worker rewriting the spec it is implementing against.
    """
    fmt = detect_format(spec_path)
    if fmt == FORMAT_OPENSPEC:
        return openspec.OpenSpecTaskSource(Path(".")).spec_root_prefix()
    if fmt == FORMAT_SPECKIT:
        return speckit.SpecKitTaskSource(Path(".")).spec_root_prefix()
    return devkit.DevkitSpecTaskSource(Path(".")).spec_root_prefix()


def resolve_external_dependency(spec_path: Path | str, dep_ref: str) -> dict[str, Any]:
    """Resolve a dependency using the adapter that owns ``spec_path``."""
    spec_path = Path(spec_path)
    if detect_format(spec_path) == FORMAT_DEVKIT and "docs" not in spec_path.parts:
        # Some legacy callers pass a repository-local fixture path such as
        # `specs/001-test`; sibling tasks still live under docs/specs/.
        source = devkit.DevkitSpecTaskSource(spec_path.parent.parent)
    else:
        source = task_source_for(spec_path)
    return source.resolve_external_dependency(dep_ref)


def mark_status_completed(path: Path | str) -> bool:
    """Compatibility status write for the historical helper API."""
    from worktrail.taskformats.devkit.schema import set_status_completed

    return bool(set_status_completed(Path(path)))


def resolve_external_dependency_for_repo(
    repo_root: Path | str, dep_ref: str
) -> dict[str, Any]:
    """Resolve a dependency when only the repository and reference are known."""
    repo_root = Path(repo_root)
    spec_id = dep_ref.partition("/")[0]
    for candidate in (
        repo_root / openspec.DEFAULT_SPEC_ROOT / spec_id,
        repo_root / speckit.DEFAULT_SPEC_ROOT / spec_id,
        repo_root / devkit.DEFAULT_SPEC_ROOT / spec_id,
    ):
        if candidate.is_dir():
            return resolve_external_dependency(candidate, dep_ref)
    return resolve_external_dependency(
        repo_root / openspec.DEFAULT_SPEC_ROOT / spec_id, dep_ref
    )


def task_for(spec_path: Path | str, task_id: str) -> dict[str, Any] | None:
    """Find one task through its owning adapter."""
    source = task_source_for(spec_path)
    _, tasks = source.load(spec_ref_for(spec_path))
    return next((task for task in tasks if task.get("id") == task_id), None)


def file_sections_for(path: Path | str, text: str) -> tuple[list[str], list[str]]:
    """Extract format-specific file sections without importing an adapter."""
    return task_source_for(path).file_sections(text)
