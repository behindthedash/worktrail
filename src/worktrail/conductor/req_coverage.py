"""Requirement-coverage gate for OpenSpec-format changes.

Design `openspec-req-ac-coverage-gate/design.md` D1/D3/D4: an OpenSpec change
declares requirements by name (`### Requirement: <Name>`) under `## ADDED
Requirements` / `## MODIFIED Requirements` in its `specs/**/spec.md` delta
files. Unlike devkit's numbered `REQ-001` identifiers with per-task
`reqs:`/`ac-mapping:` frontmatter arrays, OpenSpec's `tasks.md` carries no
structured field linking a task to a requirement, so coverage here is a
name-presence text match (D1) rather than an exact lookup.

The gate is non-retroactive (D3): a requirement only counts if it is newly
declared relative to the same capability's already-merged
`openspec/specs/<capability-path>/spec.md`, comparing by exact name. A
capability with no existing main spec (brand-new) has every declared
requirement counted as newly declared.
"""

from __future__ import annotations

import re
from pathlib import Path

_SECTION_HEADER_RE = re.compile(r"^## (.+?)\s*$", re.MULTILINE)
_REQUIREMENT_HEADER_RE = re.compile(r"^### Requirement:\s*(.+?)\s*$", re.MULTILINE)
_WHITESPACE_RE = re.compile(r"\s+")

_DECLARING_SECTIONS = ("ADDED Requirements", "MODIFIED Requirements")


def _normalize_whitespace(text: str) -> str:
    """Collapse every run of whitespace (newlines included) to a single space.

    Coverage is a name-present text match (D1); a requirement's title that gets
    word-wrapped across a line break in `tasks.md` (long titles, editor reflow)
    must still match its declared form, so the comparison depends only on the
    wording, not on incidental text-wrapping.
    """
    return _WHITESPACE_RE.sub(" ", text)


def _iter_sections(spec_text: str):
    """Yield `(section_title, section_body)` for each `## ...` block."""
    headers = list(_SECTION_HEADER_RE.finditer(spec_text))
    for i, m in enumerate(headers):
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(spec_text)
        yield m.group(1).strip(), spec_text[start:end]


def declared_requirement_names(delta_spec_text: str) -> list[str]:
    """Requirement names a delta spec declares, per D1.

    Only `## ADDED Requirements` / `## MODIFIED Requirements` count. A name
    that appears solely under `## REMOVED Requirements` claims no new task
    coverage and is excluded.
    """
    names: list[str] = []
    for title, body in _iter_sections(delta_spec_text):
        if title not in _DECLARING_SECTIONS:
            continue
        names.extend(m.group(1).strip() for m in _REQUIREMENT_HEADER_RE.finditer(body))
    return names


def existing_requirement_names(main_spec_text: str) -> set[str]:
    """Requirement names already present in a merged `openspec/specs/**/spec.md`.

    The main-spec schema has no ADDED/MODIFIED/REMOVED sectioning -- just a
    flat `## Requirements` section -- so every `### Requirement:` header in
    the file is a currently-declared name. This is the D3 ratchet baseline.
    """
    return {m.group(1).strip() for m in _REQUIREMENT_HEADER_RE.finditer(main_spec_text)}


def is_openspec_change_dir(spec_dir: str | Path) -> bool:
    """D4's format guard: True only for an `openspec/changes/<id>/` layout.

    A devkit-format `docs/specs/<id>/` directory has a single `spec.md` file
    and a `tasks/` directory of per-task files -- neither `tasks.md` (a file)
    nor `specs/` (a directory) in the OpenSpec layout -- so it reads as False
    here and the gate is a no-op for it.
    """
    spec_dir = Path(spec_dir)
    return (spec_dir / "tasks.md").is_file() or (spec_dir / "specs").is_dir()


def find_uncovered_requirements(spec_dir: str | Path, repo: str | Path) -> list[str]:
    """Newly-declared requirements this change's `tasks.md` never mentions.

    `spec_dir` is the OpenSpec change directory (`openspec/changes/<id>/`);
    `repo` is the repository root, used to resolve each declared requirement's
    capability against the already-merged `openspec/specs/<capability-path>/
    spec.md` for the D3 ratchet. Returns `[]` for a devkit-format `spec_dir`
    (D4) or a change directory declaring no requirements at all.
    """
    spec_dir = Path(spec_dir)
    if not is_openspec_change_dir(spec_dir):
        return []

    tasks_md = spec_dir / "tasks.md"
    tasks_text_lower = (
        _normalize_whitespace(tasks_md.read_text(encoding="utf-8")).lower()
        if tasks_md.is_file()
        else ""
    )

    repo = Path(repo)
    specs_dir = spec_dir / "specs"
    if not specs_dir.is_dir():
        return []

    uncovered: list[str] = []
    for delta_spec in sorted(specs_dir.rglob("spec.md")):
        declared = declared_requirement_names(delta_spec.read_text(encoding="utf-8"))
        if not declared:
            continue

        capability_path = delta_spec.parent.relative_to(specs_dir)
        main_spec = repo / "openspec" / "specs" / capability_path / "spec.md"
        existing_names = (
            existing_requirement_names(main_spec.read_text(encoding="utf-8"))
            if main_spec.is_file()
            else set()
        )

        for name in declared:
            if name in existing_names:
                continue
            if (
                _normalize_whitespace(name).lower() not in tasks_text_lower
                and name not in uncovered
            ):
                uncovered.append(name)

    return uncovered
