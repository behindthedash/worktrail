"""Precheck for acceptance criteria that name an absent update target.

A change's task text routinely says "Update the `<needle>` entry in
`<path>`". When `<path>` exists in the base tree but has never contained
`<needle>`, the criterion is unsatisfiable as written: the worker either
invents the entry (turning an "update" into an unreviewed addition) or
reports the task blocked. This module extracts exactly that claim from task
text and reports it before compile writes a plan.

The extraction is deliberately narrow (see
`openspec/changes/compile-precheck-ac-named-target-existence`): a sentence
fires only when it carries an update-verb, a backticked repo-relative path
that resolves to a file that exists under `repo`, and at least one other
backticked token, named *before* the path, to look for inside it. The
ordering is what keeps "extend `<path>` with a case covering `<token>`" out:
a token named after the file is the new content being written into it, and is
correctly absent from the base tree. Unbackticked prose, additive
phrasing ("Add a ... entry to ..."), and a backticked path that does not
exist all report nothing. An absent path in particular is *not* a finding
here: a task that creates the file it names is the normal case, and this
gate has no way to tell that apart from a typo -- the file-scope and
requirement-coverage gates own that ground.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

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
_UPDATE_VERB_RE = re.compile(
    r"\b(?:" + "|".join(_UPDATE_VERBS) + r")\w*\b", re.IGNORECASE
)
_BACKTICKED_RE = re.compile(r"`([^`]+)`")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?](?=\s)")
_TASK_LINE_RE = re.compile(r"^\s*- \[[ xX]\]\s*(\S+)\s")


def _task_block_text(task: Mapping[str, Any], spec_dir: Path, repo: Path) -> str:
    """The task's full prose: its declared fields plus any continuation lines.

    An OpenSpec `tasks.md` item wraps its acceptance criteria across the lines
    following the `- [ ] <id>` line, and only the first of those lines becomes
    the task's `title`. Without the continuation block the precheck would only
    ever see a task's opening clause.
    """
    parts = [
        str(task.get(field) or "") for field in ("title", "text", "body", "description")
    ]
    rel = str(task.get("path") or "")
    task_id = str(task.get("id") or "")
    source = repo / rel if rel else spec_dir / "tasks.md"
    if not source.is_file():
        source = spec_dir / "tasks.md"
    if task_id and source.is_file():
        lines = source.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            m = _TASK_LINE_RE.match(line)
            if not m or m.group(1) != task_id:
                continue
            for follow in lines[i + 1 :]:
                if not follow.strip() or _TASK_LINE_RE.match(follow):
                    break
                parts.append(follow.strip())
            break
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
    except (OSError, UnicodeDecodeError):
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
            spans = [(m.start(), m.group(1)) for m in _BACKTICKED_RE.finditer(sentence)]
            if len(spans) < 2:
                continue
            if not _UPDATE_VERB_RE.search(_BACKTICKED_RE.sub(" ", sentence)):
                continue

            resolved = [(at, t, _resolve_existing(t, repo)) for at, t in spans]
            for at, token, path in resolved:
                if path is None:
                    continue
                text = _read_text(path)
                if text is None:
                    continue
                # Only a token named *before* the file is a claim about what
                # that file already holds ("update the `X` entry in `f`").
                # A token after it is the new content being written into it
                # ("extend `f` with a case covering `X`") -- an addition, and
                # correctly absent from the base tree.
                needles = [t for nat, t, p in resolved if p is None and nat < at]
                for needle in needles:
                    finding = (
                        f"{task_id}: `{token}` does not contain `{needle}`"
                        if task_id
                        else f"`{token}` does not contain `{needle}`"
                    )
                    if needle.strip() not in text and finding not in findings:
                        findings.append(finding)

    return findings
