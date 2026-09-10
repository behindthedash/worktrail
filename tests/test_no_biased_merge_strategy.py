"""Structural guard against the biased-merge-strategy defect class.

A git merge run with a biased strategy option (`-X ours` / `-X theirs`,
`--strategy-option=ours`, or `-s ours` / `--strategy=theirs` as a top-level
strategy) auto-resolves EVERY content-level conflict in the merge in one
direction, not just the conflicts on the lines the caller had in mind. That
silently discards real content on the other side instead of failing loud.
It shipped once with no test catching it (PR #414: `integrate_one`'s
dependency-branch-gone fallback reconciled a stale reconstructed start point
against the live base with an unconditioned `-X ours` merge, reverting a
different group's already-landed `tasks.md` checkboxes) -- see
`docs/specs/research/integrate-one-dep-branch-gone-fallback-root-cause.md`
and `docs/specs/research/carry-squash-merged-dependencies-x-ours-risk.md`.

This test parses every `*.py` under `src/worktrail/` with `ast` and flags any
string literal that can reach git's argv in one of those shapes. Only
`ast.Constant` string nodes are inspected and the module/class/function
docstring position is skipped, so comments and docstrings that *explain* the
prohibition (integrate.py, live.py) keep passing. A bare `"ours"` /
`"theirs"` literal is not flagged: conflict-side handling uses those words
legitimately. A separated `"-X"` literal is flagged only when its next sibling
literal is `ours` / `theirs` -- `gh api ... -X PATCH` (the HTTP-method flag)
is a different, safe shape. No allowlist, no opt-out. A `SyntaxError` is
allowed to propagate so an unparseable file fails the test rather than being
silently skipped.
"""

from __future__ import annotations

import ast
from itertools import pairwise
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCAN_ROOT = REPO_ROOT / "src" / "worktrail"

_STRATEGY_FORMS = frozenset(
    f"{prefix}{side}"
    for prefix in ("-s ", "--strategy=", "--strategy ")
    for side in ("ours", "theirs")
)
_BIASED_SIDES = frozenset({"ours", "theirs"})


def _is_biased(literal: str) -> bool:
    if literal in ("-Xours", "-Xtheirs"):
        return True
    if literal.startswith("--strategy-option"):
        return True
    return literal in _STRATEGY_FORMS


def _docstring_ids(tree: ast.AST) -> set[int]:
    """ids of the Constant nodes sitting in a docstring position."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _siblings(node: ast.AST) -> list[list[ast.AST]]:
    """Ordered child sequences (list/tuple/set elts, call args) of a node."""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [list(node.elts)]
    if isinstance(node, ast.Call):
        return [list(node.args)]
    return []


def scan_file(path: Path, rel_to: Path) -> list[str]:
    """Return `<path>:<line>: <literal>` for every biased-merge literal in `path`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    skip = _docstring_ids(tree)
    rel = path.relative_to(rel_to)
    offenders: list[str] = []
    seen: set[int] = set()

    def report(node: ast.Constant) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        offenders.append(f"{rel}:{node.lineno}: {node.value!r}")

    for node in ast.walk(tree):
        # Separated form: "-X" immediately followed by "ours"/"theirs".
        for seq in _siblings(node):
            for cur, nxt in pairwise(seq):
                if (
                    isinstance(cur, ast.Constant)
                    and cur.value == "-X"
                    and isinstance(nxt, ast.Constant)
                    and nxt.value in _BIASED_SIDES
                ):
                    report(cur)
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
            and _is_biased(node.value)
        ):
            report(node)
    return sorted(offenders)


def scan_tree(root: Path, rel_to: Path) -> list[str]:
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        offenders.extend(scan_file(path, rel_to))
    return offenders


def _assert_clean(offenders: list[str]) -> None:
    assert not offenders, (
        "Found biased merge strategy option literal(s). `-X ours`/`-X theirs`, "
        "`--strategy-option`, and `-s ours`/`--strategy=theirs` auto-resolve "
        "EVERY conflict in the merge one way and silently discard the other "
        "side's content (the PR #414 defect class). Use an unbiased merge and "
        "fail loud on conflict instead:\n" + "\n".join(offenders)
    )


def test_src_tree_has_no_biased_merge_strategy():
    _assert_clean(scan_tree(SCAN_ROOT, REPO_ROOT))


def test_biased_option_calls_are_reported(tmp_path: Path):
    src = tmp_path / "biased.py"
    src.write_text(
        '"""Module docstring."""\n'
        "\n"
        "\n"
        "def f(_git, wt):\n"
        '    _git(wt, "merge", "-X", "ours", "origin/main")\n'
        '    _git(wt, "merge", "-Xtheirs", "origin/main")\n'
        '    _git(wt, "merge", "--strategy-option=ours", "origin/main")\n'
        '    _git(wt, "merge", "--strategy-option", "theirs", "origin/main")\n'
        '    _git(wt, "merge", "-s ours", "origin/main")\n'
        '    _git(wt, "merge", "--strategy=theirs", "origin/main")\n'
        '    _git(wt, "merge", "--strategy ours", "origin/main")\n'
        '    return ["git", "merge", "-X", "theirs"]\n',
        encoding="utf-8",
    )
    offenders = scan_file(src, tmp_path)
    lines = sorted(int(o.split(":")[1]) for o in offenders)
    assert lines == [5, 6, 7, 8, 9, 10, 11, 12], offenders
    assert "biased.py:5: '-X'" in offenders
    assert "biased.py:6: '-Xtheirs'" in offenders


def test_documenting_the_prohibition_is_not_flagged(tmp_path: Path):
    src = tmp_path / "clean.py"
    src.write_text(
        '"""Never merge with `-X ours` -- it discards live-base content."""\n'
        "\n"
        "\n"
        "def merge(_git, wt, ours, theirs):\n"
        '    """Deliberately NOT `-X ours`; use `--strategy-option` never."""\n'
        "    # An `-X theirs` / `-s ours` merge would silently drop content.\n"
        '    _git(wt, "show", f":2:{ours}")\n'
        '    side = "ours" if ours else "theirs"\n'
        '    _git("gh", "api", "repos/x/y/pulls/1", "-X", "PATCH")\n'
        "    return side\n",
        encoding="utf-8",
    )
    assert scan_file(src, tmp_path) == []


def test_syntax_error_is_not_swallowed(tmp_path: Path):
    src = tmp_path / "broken.py"
    src.write_text("def f(:\n", encoding="utf-8")
    with pytest.raises(SyntaxError):
        scan_file(src, tmp_path)
