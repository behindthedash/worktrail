#!/usr/bin/env python3
"""
Parallel SDD Orchestrator -- real spec loader.

Reads a real `docs/specs/[id]/tasks/TASK-*.md` tree (the spec-to-tasks output
format) and projects each task's frontmatter into the dict the coordinator/
orchestrator consume:

    {"id", "deps", "external_deps", "external_deps_warnings", "files", "kind", "reqs",
     "status", "timeout", "title", "review", "complexity", "domain", "path",
     "frontmatter_warnings"}

Frontmatter fields used: id, title, status, dependencies, external-dependencies, files,
kind, reqs, timeout, review, complexity, domain.

`external-dependencies` is a separate, additive field alongside `dependencies`/
`depends_on` -- an External Dependency Reference (`<spec-id>/<task-id>`) declaring a
cross-spec prerequisite. It has no alias and its resolution is out of scope for this
loader (see `validate_external_dependencies`); malformed entries are kept, not dropped,
so a downstream unresolved-reference reporter can still name them.
(`files`/`kind`/`reqs`/`complexity`/`domain` are additions our fork can have
spec-to-tasks emit; the upstream template already carries id/title/status/dependencies.)

`depends_on` is accepted as an alias for `dependencies` (`dependencies` wins if both
are present): change-spec task authoring has repeatedly produced `depends_on:`
instead of the canonical `dependencies:` key (verified: 15 TASK-*.md files across
this workspace's datalena specs alone), and a silent miss here empties `deps`,
which defeats dependency-ordered fan-out and worktree stacking without any error.

Minimal dependency-free frontmatter parser -- handles scalars, inline `[a, b]`
lists, and multi-line block lists (lines starting with `  - value`).
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_VALID_COMPLEXITY_TIERS = {"trivial", "standard", "hard"}

# Every top-level frontmatter key the spec-to-tasks task template (and its
# delta/change-spec variant) is documented to emit -- see
# specs.spec-to-tasks.md's task template and dispatch.py's own references to
# success-criteria. `dependencies`/`depends_on` are BOTH listed since
# `depends_on` is an accepted alias (see module docstring), not a typo.
KNOWN_TASK_FRONTMATTER_KEYS = {
    "id",
    "title",
    "spec",
    "lang",
    "status",
    "dependencies",
    "depends_on",
    "files",
    "kind",
    "reqs",
    "timeout",
    "review",
    "complexity",
    "domain",
    "ac-mapping",
    "imp-requirements",
    "success-criteria",
    "external-dependencies",
    "dod-checks",
}


def validate_frontmatter_keys(fm: Dict[str, Any], label: str = "") -> List[str]:
    """Return one warning string per frontmatter key that looks like a typo
    of a known task field -- e.g. `depend_on` for `dependencies`, `reveiw`
    for `review` -- so a future field-name mismatch is loud instead of a
    silent empty default three layers downstream.

    A key with NO close match at all stays silent: a looser "warn on any
    unrecognized key" policy was considered and rejected as too noisy for
    legitimate future field additions that just haven't been added to
    `KNOWN_TASK_FRONTMATTER_KEYS` yet (see brief
    20260723-213000-loader-frontmatter-schema-validation, "Open questions").

    This closes a failure class root-caused TWICE independently in one
    session (incidents 20260723-191922 and 20260713-153320, nine days apart,
    both `depends_on` vs `dependencies`) -- loader.py silently drops an
    unrecognized frontmatter key with no warning anywhere downstream,
    defeating dependency-ordered fan-out with no visible error.
    """
    warnings: List[str] = []
    for key in fm:
        if key in KNOWN_TASK_FRONTMATTER_KEYS:
            continue
        close = difflib.get_close_matches(key, KNOWN_TASK_FRONTMATTER_KEYS, n=1, cutoff=0.75)
        if close:
            prefix = f"{label}: " if label else ""
            warnings.append(
                f"{prefix}frontmatter key '{key}' is not recognized -- did you mean "
                f"'{close[0]}'? (unrecognized keys are silently ignored, not applied)"
            )
    return warnings


def validate_external_dependencies(raw_entries: List[str], label: str = "") -> List[str]:
    """Return one warning string per malformed `external-dependencies:` entry.

    An External Dependency Reference is expected in `<spec-id>/<task-id>` shape
    (e.g. `098-recursive-organization-model/TASK-036`). An entry is malformed
    if it has no `/` separator, an empty spec-id segment, or an empty task-id
    segment. Malformed entries are only reported here -- the caller still
    keeps them in `external_deps` so a downstream unresolved-reference
    reporter can name the offending entry.
    """
    warnings: List[str] = []
    prefix = f"{label}: " if label else ""
    for entry in raw_entries:
        spec_id, sep, task_id = entry.partition("/")
        if not sep or not spec_id or not task_id:
            warnings.append(
                f"{prefix}external-dependencies entry '{entry}' is malformed -- "
                f"expected '<spec-id>/<task-id>'"
            )
    return warnings


def parse_frontmatter(text: str) -> Dict[str, Any]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}
    fm: Dict[str, Any] = {}
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        i += 1
        if not line or line.lstrip().startswith("#") or ":" not in line:
            continue
        # Skip indented lines (list items consumed below; nested maps unsupported)
        if line[0] in (" ", "\t"):
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            fm[key] = [x.strip().strip("\"'") for x in inner.split(",") if x.strip()]
        elif not val:
            # Multi-line block list: collect following `  - value` lines
            items: List[str] = []
            while i < len(lines):
                nxt = lines[i].rstrip()
                stripped = nxt.lstrip()
                if stripped.startswith("- "):
                    items.append(stripped[2:].strip().strip("\"'"))
                    i += 1
                elif not nxt:
                    i += 1
                    break
                else:
                    break
            if items:
                fm[key] = items
        else:
            fm[key] = val.strip("\"'")
    return fm


def _find_tasks_dir(p: Path) -> Path:
    """Locate `<p>/tasks/TASK-*.md`.

    Callers always pass a path that directly OWNS a `tasks/` dir: the spec
    root for a brainstorm-originated spec, or `$CHANGE_DIR` itself for
    change-spec (delta/bugfix) work -- pipeline-details.md's modify-pipeline
    step 4 is explicit: "SPEC_ROOT=$WT, pointed at $CHANGE_DIR". No caller in
    this codebase ever passes a bare spec-root for change-spec-only work, so
    a `changes/<name>/` auto-discovery fallback here was dead code -- and its
    glob checked one directory level too shallow relative to the real
    on-disk convention (`changes/<name>/tasks/TASK-*.md`, not
    `changes/<name>/TASK-*.md`) besides. See brief
    20260723-212300-loader-change-spec-fallback-glob-mismatch.
    """
    return p / "tasks"


_FILES_HEADING_RE = re.compile(r"^\*\*Files to (Create|Modify)\*\*", re.IGNORECASE)


def parse_files_sections(text: str) -> Tuple[List[str], List[str]]:
    """Extract (create_files, modify_files) from a TASK-*.md BODY's
    `**Files to Create**:` / `**Files to Modify**:` bulleted sections (written
    by the spec-to-tasks task template's "## Implementation Details" section --
    the combined `files:` frontmatter is itself extracted from these same two
    lists). A section that is absent, or present with no `- ` bullets (e.g.
    "**Files to Create**: None."), yields an empty list for that side.

    Only a `- `/`* ` bullet line adds an item (the leading backtick-wrapped
    path, or the text before " - " when unbacktick'd); a wrapped continuation
    line of the same bullet (no leading `- `) is ignored, not treated as the
    end of the section -- only a new `##`/`**Files to ...**` heading ends it.
    """
    create: List[str] = []
    modify: List[str] = []
    current: List[str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        heading = _FILES_HEADING_RE.match(line)
        if heading:
            current = create if heading.group(1).lower() == "create" else modify
            continue
        if line.startswith("##"):
            current = None
            continue
        if current is None:
            continue
        if line.startswith("- ") or line.startswith("* "):
            item = line[2:].strip()
            m = re.match(r"`([^`]+)`", item)
            path = m.group(1) if m else item.split(" - ")[0].strip()
            if path:
                current.append(path)
    return create, modify


def load_spec(spec_folder: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Return (spec_id, tasks) from a docs/specs/[id]/ folder (brainstorm- or
    change-spec-originated)."""
    p = Path(spec_folder)
    tasks_dir = _find_tasks_dir(p)
    if not tasks_dir.is_dir():
        raise FileNotFoundError(f"no task files under {p} (looked in {tasks_dir})")

    tasks: List[Dict[str, Any]] = []
    for f in sorted(tasks_dir.glob("TASK-*.md")):
        if "--" in f.stem:  # skip TASK-XXX--review.md and aux files
            continue
        fm = parse_frontmatter(f.read_text())
        _fm_warnings = validate_frontmatter_keys(fm, label=f.name)
        _external_deps = fm.get("external-dependencies", [])
        _external_deps_warnings = validate_external_dependencies(_external_deps, label=f.name)
        _raw_timeout = fm.get("timeout")
        try:
            _timeout: Any = int(_raw_timeout) if _raw_timeout is not None else None
        except (ValueError, TypeError):
            _timeout = None
        # Non-positive timeouts are not valid overrides; normalize to None so
        # `task.get("timeout") or run_default` always falls back correctly.
        if isinstance(_timeout, int) and _timeout <= 0:
            _timeout = None
        # Advisory routing metadata (complexity/domain): unknown or absent values
        # normalize rather than error, since pre-spec task files never carry them.
        _complexity = fm.get("complexity", "standard")
        if _complexity not in _VALID_COMPLEXITY_TIERS:
            _complexity = "standard"
        tasks.append(
            {
                "id": fm.get("id", f.stem),
                "deps": fm.get("dependencies", fm.get("depends_on", [])),
                "external_deps": _external_deps,
                "external_deps_warnings": _external_deps_warnings,
                "files": fm.get("files", []),
                "kind": fm.get("kind", "impl"),
                "reqs": fm.get("reqs", []),
                "status": fm.get("status", "pending"),
                "timeout": _timeout,
                "title": fm.get("title", ""),
                # Opt-in review fast-path (#16): `review: skip|false|no` lets a task
                # bypass the review gate (see live._review_exempt).
                "review": fm.get("review", ""),
                "complexity": _complexity,
                "domain": fm.get("domain", ""),
                # Source markdown path -- lets callers (e.g. live.precheck()) read
                # the task BODY (not just frontmatter) when they need a signal
                # frontmatter doesn't carry, such as the "Files to Create" vs
                # "Files to Modify" section split.
                "path": str(f),
                # Typo-shaped unrecognized frontmatter keys, if any -- see
                # validate_frontmatter_keys(). Empty list when clean.
                "frontmatter_warnings": _fm_warnings,
            }
        )
    tasks.sort(key=lambda t: t["id"])
    return p.name, tasks


DEFAULT_SPEC_ROOT = "docs/specs"


def resolve_external_dependency(repo_root: Path, ref: str, spec_root: str = DEFAULT_SPEC_ROOT) -> dict:
    """Resolve one raw `<spec-id>/<task-id>` External Dependency Reference
    (from a task's `external_deps`, produced by `validate_external_dependencies`)
    against `repo_root`'s spec tree (`docs/specs/` by default -- pass
    `spec_root` to point at a different convention).

    Returns `{"ref", "resolved", "satisfied", "status", "reason"}` -- see
    contracts/cross-spec-resolution.md's "Cross-Spec Resolution Result" for
    the full field contract. Read-only: never writes, never shells out to
    git, never touches a path outside `repo_root` (a `spec_id`/`task_id`
    segment crafted with `../` is caught by the same `resolve()` +
    `relative_to()` guard `live.py`'s worker-file-candidate scan already
    uses, before any filesystem check runs on the escaped path).
    """

    def _unresolved(reason: str) -> Dict[str, Any]:
        return {"ref": ref, "resolved": False, "satisfied": False, "status": None, "reason": reason}

    spec_id, sep, task_id = ref.partition("/")
    if not sep or not spec_id or not task_id:
        return _unresolved("malformed")

    root = repo_root.resolve()

    spec_dir = (root / spec_root / spec_id).resolve()
    try:
        spec_dir.relative_to(root)
    except ValueError:
        return _unresolved("spec-id folder not found")
    if not spec_dir.is_dir():
        return _unresolved("spec-id folder not found")

    task_file = (spec_dir / "tasks" / f"{task_id}.md").resolve()
    try:
        task_file.relative_to(root)
    except ValueError:
        return _unresolved("task file not found")
    if not task_file.is_file():
        return _unresolved("task file not found")

    status = parse_frontmatter(task_file.read_text()).get("status")
    satisfied = status in ("done", "completed")
    return {"ref": ref, "resolved": True, "satisfied": satisfied, "status": status, "reason": None}


class DevkitSpecTaskSource:
    """`TaskSource` implementation backed by devkit's
    `docs/specs/[id]/tasks/TASK-*.md` frontmatter contract (`FIELD_SCHEMA` in
    `worktrail.taskformats.devkit.schema`). Wraps this module's existing,
    battle-tested module-level functions rather than reimplementing them --
    the class is purely an adapter shape, not new parsing logic.
    """

    def __init__(self, repo_root: Path, spec_root: str = DEFAULT_SPEC_ROOT):
        self.repo_root = Path(repo_root)
        self._spec_root = spec_root

    def load(self, spec_ref: str) -> Tuple[str, List[Dict[str, Any]]]:
        return load_spec(str(self.repo_root / self._spec_root / spec_ref))

    def mark_status(self, task_id: str, status: str, *, spec_ref: Optional[str] = None) -> bool:
        """Persist *status* for one task. Returns True if the file changed.

        ``completed`` takes the surgical path (`schema.set_status_completed`):
        it rewrites only the status line and ticks the body's checkboxes,
        leaving every other frontmatter field byte-identical. Any other status
        goes through the yaml round-trip. The split exists because the
        orchestrator's integrate stage writes ``completed`` onto a PR branch,
        where an incidental yaml reformat of unrelated fields would show up as
        review noise.
        """
        from worktrail.taskformats.devkit.schema import (
            read_task_file,
            set_status_completed,
            write_task_file,
        )

        if spec_ref is None:
            raise ValueError("DevkitSpecTaskSource.mark_status requires spec_ref")
        path = self.task_file_path(task_id, spec_ref)
        if status == "completed":
            return set_status_completed(path)
        frontmatter, error, body = read_task_file(path)
        if error or frontmatter is None:
            raise FileNotFoundError(f"cannot read {path}: {error}")
        if frontmatter.get("status") == status:
            return False
        frontmatter["status"] = status
        write_task_file(path, frontmatter, body)
        return True

    def resolve_external_dependency(self, dep_ref: str) -> Dict[str, Any]:
        return resolve_external_dependency(self.repo_root, dep_ref, spec_root=self._spec_root)

    def task_brief_ref(self, task_id: str, spec_ref: str) -> tuple:
        """The task's own file is the brief; no anchor needed."""
        return f"{self._spec_root}/{spec_ref}/tasks/{task_id}.md", ""

    def task_file_path(self, task_id: str, spec_ref: str) -> Path:
        return self.repo_root / self._spec_root / spec_ref / "tasks" / f"{task_id}.md"

    def spec_root(self, spec_ref: str) -> Path:
        return self.repo_root / self._spec_root / spec_ref

    def spec_root_prefix(self) -> str:
        return f"{self._spec_root}/"

    def file_sections(self, text: str) -> tuple:
        return parse_files_sections(text)
