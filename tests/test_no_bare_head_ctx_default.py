"""Structural guard against the worktree-relative-HEAD-sentinel defect class.

`dict.get(<key>, "HEAD")` silently defaults an unresolved base/ref value to the
literal string "HEAD" -- worktree-relative, so a value built in one working
tree (or with no working tree in scope at all) and later consumed inside a
DIFFERENT one (a prompt template rendered here, `.format()`-ed into an agent
prompt, then interpreted by a worker running inside its own task worktree)
silently resolves against the wrong tree instead of failing loud. This has
shipped twice with zero test coverage catching either occurrence before a
human noticed the symptom (PR #825: `_validate_retained_task_branch`'s
auto-merge repair; PR #837: `LiveSpawn`'s review `base_commit`). This test
scans the orchestrator/taskformats/dispatch surface for the same `.get(...,
"HEAD")` shape so a third recurrence fails CI instead of shipping silently.

Deliberately narrow: a direct `git ... HEAD` argument (e.g. `_git(wt,
"rev-parse", "HEAD")`) evaluates against whichever repo/worktree the call
site already names explicitly -- that is a different, safe shape. Only the
"unresolved value defaulting silently to HEAD" shape is flagged.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCAN_DIRS = [
    REPO_ROOT / "src" / "worktrail" / "orchestrator",
    REPO_ROOT / "src" / "worktrail" / "taskformats",
]

# `.get("some_commit_or_ref_key", "HEAD")` / `.get('key', 'HEAD')` -- a dict
# read that silently falls back to the worktree-relative sentinel instead of
# requiring the caller to have resolved it.
BARE_HEAD_DEFAULT_RE = re.compile(r"""\.get\(\s*['"][^'"]+['"]\s*,\s*['"]HEAD['"]\s*\)""")


def _iter_source_files():
    for scan_dir in SCAN_DIRS:
        yield from scan_dir.rglob("*.py")


def test_no_bare_head_literal_as_ctx_dict_default():
    """No `.get(<key>, "HEAD")` pattern anywhere under orchestrator/ or
    taskformats/ -- a value consumed by a prompt template (or any other
    cross-worktree hand-off) must be resolved by its producer, never
    defaulted silently to the worktree-relative "HEAD" sentinel."""
    offenders = []
    for path in _iter_source_files():
        text = path.read_text(encoding="utf-8")
        for match in BARE_HEAD_DEFAULT_RE.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_no}: {match.group(0)}")

    assert not offenders, (
        "Found dict.get(..., \"HEAD\") default(s) -- this is the "
        "worktree-relative-HEAD-sentinel defect class fixed in PR #825 and "
        "PR #837. Resolve the value against the canonical repo at the point "
        "it is produced instead of defaulting it here:\n" + "\n".join(offenders)
    )
