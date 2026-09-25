"""Precheck for acceptance criteria that name an absent update target.

A change's task text routinely says "Update the `<needle>` entry in
`<path>`". When `<path>` exists in the base tree but has never contained
`<needle>`, the criterion is unsatisfiable as written: the worker either
invents the entry (turning an "update" into an unreviewed addition) or
reports the task blocked. This module extracts exactly that claim from task
text and reports it before compile writes a plan.

The extraction is deliberately narrow (see
`openspec/changes/compile-precheck-ac-named-target-existence`): a single
sentence must carry an update verb whose direct object is a backticked entity
needle (not itself an existing file path), and a distinct backticked
repo-relative path that resolves to a file under `repo`. Other backticked
tokens do not count as needles. Unbackticked prose,
additive phrasing ("Add a ... entry to ..."), and a backticked path that does
not exist all report nothing. An absent path in particular is *not* a finding here: a task that
creates the file it names is the normal case, and this gate has no way to
tell that apart from a typo -- the file-scope and requirement-coverage gates
own that ground.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# The nine verbs the change specifies, matched as whole words. Suffix-open
# matching (`update\w*`) would let `fixed`, `fixture`, `correctly` and
# `removal` license a sentence that makes no update claim at all.
_UPDATE_VERBS = (
    "update",
    "modify",
    "amend",
    "extend",
    "replace",
    "rename",
    "remove",
    "fix",
    "correct",
)
_BACKTICKED_RE = re.compile(r"`([^`]+)`")
_UPDATE_OBJECT_RE = re.compile(
    r"\b(?:"
    + "|".join(_UPDATE_VERBS)
    + r")\b\s+(?:(?:only|the|a|an|existing|current|named|specific|same|old|new|stale|correct|matching|missing|corresponding)\s+)*`([^`]+)`",
    re.IGNORECASE,
)
# A sentence may end behind a closing bracket or quote (`... exhaustion.)`).
# Without those, two sentences are scanned as one and a verb in the first can
# license a token pair in the second.
_SENTENCE_SPLIT_RE = re.compile(r"[.!?][)\]\"'”’]*(?=\s)")


def _task_block_text(task: Mapping[str, Any], spec_dir: Path, repo: Path) -> str:
    """The task's title plus any prose continuation lines beneath it.

    An OpenSpec `tasks.md` item wraps its acceptance criteria across the lines
    following the `- [ ] <id>` line, and only the first of those lines becomes
    the task's `title`. Without the continuation block the precheck would only
    ever see a task's opening clause. The block is located with the canonical
    `openspec/schema.py` parser -- a private second parser here would drift
    from the one the orchestrator actually runs on.
    """
    from worktrail.taskformats.openspec import schema

    parts = [str(task.get("title") or "")]
    rel = str(task.get("path") or "")
    task_id = str(task.get("id") or "")
    source = repo / rel if rel else spec_dir / "tasks.md"
    if not source.is_file():
        source = spec_dir / "tasks.md"
    if task_id and source.is_file():
        text = source.read_text(encoding="utf-8")
        parsed = schema.parse_tasks_md(text)
        found = parsed.by_id(task_id)
        if found is not None:
            lines = text.splitlines()
            for follow in lines[found.line_no + 1 :]:
                if not follow.strip():
                    break
                if schema.GROUP_RE.match(follow) or schema.TASK_RE.match(follow):
                    break
                # `files:`/`depends:`/`review:` are metadata, not prose.
                if (
                    schema.FILES_RE.match(follow)
                    or schema.DEPENDS_RE.match(follow)
                    or schema.REVIEW_RE.match(follow)
                ):
                    continue
                parts.append(follow.strip())
    return " ".join(p for p in parts if p)


def _resolve_existing(token: str, repo: Path) -> Path | None:
    """The file `token` names under `repo`, or None if it is not one."""
    token = token.strip()
    if not token or token.startswith("/") or ".." in token:
        return None
    candidate = repo / token
    try:
        return candidate if candidate.is_file() else None
    except OSError:
        return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None


def find_missing_ac_targets(spec_dir: str | Path, repo: str | Path) -> list[str]:
    """Acceptance criteria naming something absent from the file they update.

    `spec_dir` is the change directory; `repo` is the repository root the
    backticked paths are relative to. Returns `[]` when the directory holds no
    loadable tasks or no task makes an unambiguous update claim.
    """
    spec_dir = Path(spec_dir)
    repo = Path(repo)

    from worktrail.taskformats import resolve

    _, tasks = resolve.load_spec(str(spec_dir))

    findings: list[str] = []
    for task in tasks:
        task_id = str(task.get("id") or "")
        for sentence in _SENTENCE_SPLIT_RE.split(
            _task_block_text(task, spec_dir, repo)
        ):
            tokens = list(_BACKTICKED_RE.finditer(sentence))
            if len(tokens) < 2:
                continue
            objects = list(_UPDATE_OBJECT_RE.finditer(sentence))
            if not objects:
                continue

            for obj in objects:
                needle = obj.group(1).strip()
                if not needle or _resolve_existing(needle, repo) is not None:
                    continue
                paths = [
                    (match.group(1), _resolve_existing(match.group(1), repo))
                    for match in tokens
                    if match.group(1).strip() != needle
                ]
                paths = [(token, path) for token, path in paths if path is not None]
                if len(paths) != 1:
                    continue
                token, path = paths[0]
                text = _read_text(path)
                if text is None or needle in text:
                    continue
                finding = (
                    f"{task_id}: `{token}` does not contain `{needle}`"
                    if task_id
                    else f"`{token}` does not contain `{needle}`"
                )
                if finding not in findings:
                    findings.append(finding)

    return findings
