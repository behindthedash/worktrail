"""Tests for import-based dependency inference between tasks' declared files."""

from __future__ import annotations

from pathlib import Path

import pytest

from worktrail.conductor.import_deps import import_dep_edges


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "src" / "pkg" / "sub").mkdir(parents=True)
    (r / "src" / "pkg" / "__init__.py").write_text("")
    (r / "src" / "pkg" / "sub" / "__init__.py").write_text("")
    return r


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _task(tid: str, *files: str, deps: list[str] | None = None, kind: str = "") -> dict:
    return {"id": tid, "files": list(files), "deps": deps or [], "kind": kind}


def test_absolute_import_under_src(repo: Path) -> None:
    _write(repo, "src/pkg/a.py", "from pkg.b import thing\n")
    _write(repo, "src/pkg/b.py", "thing = 1\n")
    edges, warnings = import_dep_edges(
        [_task("1.1", "src/pkg/b.py"), _task("2.1", "src/pkg/a.py")], repo
    )
    assert edges == {"2.1": ["1.1"]}
    assert warnings == []


def test_absolute_import_at_repo_root(repo: Path) -> None:
    _write(repo, "tools/a.py", "import tools.b\n")
    _write(repo, "tools/b.py", "")
    edges, _ = import_dep_edges(
        [_task("1.1", "tools/b.py"), _task("1.2", "tools/a.py")], repo
    )
    assert edges == {"1.2": ["1.1"]}


def test_relative_import(repo: Path) -> None:
    _write(repo, "src/pkg/sub/a.py", "from ..b import thing\nfrom . import c\n")
    _write(repo, "src/pkg/b.py", "thing = 1\n")
    _write(repo, "src/pkg/sub/c.py", "")
    edges, warnings = import_dep_edges(
        [
            _task("1.1", "src/pkg/b.py"),
            _task("1.2", "src/pkg/sub/c.py"),
            _task("2.1", "src/pkg/sub/a.py"),
        ],
        repo,
    )
    assert edges == {"2.1": ["1.1", "1.2"]}
    assert warnings == []


def test_third_party_import_yields_nothing(repo: Path) -> None:
    _write(repo, "src/pkg/a.py", "import json\nimport yaml\nfrom os import path\n")
    _write(repo, "src/pkg/b.py", "")
    edges, warnings = import_dep_edges(
        [_task("1.1", "src/pkg/b.py"), _task("2.1", "src/pkg/a.py")], repo
    )
    assert edges == {}
    assert warnings == []


def test_same_stem_module_in_another_package_is_not_matched(repo: Path) -> None:
    _write(repo, "src/pkg/a.py", "from other.util import x\n")
    _write(repo, "src/pkg/util.py", "")
    _write(repo, "src/other/__init__.py", "")
    _write(repo, "src/other/util.py", "")
    edges, _ = import_dep_edges(
        [_task("1.1", "src/pkg/util.py"), _task("2.1", "src/pkg/a.py")], repo
    )
    assert edges == {}


def test_same_task_import_is_not_an_edge(repo: Path) -> None:
    _write(repo, "src/pkg/a.py", "from pkg.b import thing\n")
    _write(repo, "src/pkg/b.py", "thing = 1\n")
    edges, warnings = import_dep_edges(
        [_task("1.1", "src/pkg/a.py", "src/pkg/b.py")], repo
    )
    assert edges == {}
    assert warnings == []


def test_backward_edge_from_later_to_earlier_task(repo: Path) -> None:
    _write(repo, "src/pkg/a.py", "from pkg.b import thing\n")
    _write(repo, "src/pkg/b.py", "thing = 1\n")
    edges, warnings = import_dep_edges(
        [_task("1.1", "src/pkg/b.py"), _task("2.1", "src/pkg/a.py")], repo
    )
    assert edges == {"2.1": ["1.1"]}
    assert warnings == []


def test_earlier_task_may_depend_on_later_task(repo: Path) -> None:
    _write(repo, "src/pkg/a.py", "from pkg.b import thing\n")
    _write(repo, "src/pkg/b.py", "thing = 1\n")
    edges, warnings = import_dep_edges(
        [_task("1.1", "src/pkg/a.py"), _task("1.2", "src/pkg/b.py")], repo
    )
    assert edges == {"1.1": ["1.2"]}
    assert warnings == []


def test_mutual_import_keeps_one_edge_and_warns(repo: Path) -> None:
    _write(repo, "src/pkg/a.py", "from pkg.b import thing\n")
    _write(repo, "src/pkg/b.py", "from pkg.a import other\n")
    edges, warnings = import_dep_edges(
        [_task("1.1", "src/pkg/a.py"), _task("1.2", "src/pkg/b.py")], repo
    )
    assert edges == {"1.1": ["1.2"]}
    assert len(warnings) == 1
    assert "1.2" in warnings[0] and "1.1" in warnings[0]


def test_baseline_dep_blocks_reverse_edge(repo: Path) -> None:
    _write(repo, "src/pkg/a.py", "from pkg.b import thing\n")
    _write(repo, "src/pkg/b.py", "")
    edges, warnings = import_dep_edges(
        [_task("1.1", "src/pkg/a.py"), _task("1.2", "src/pkg/b.py", deps=["1.1"])], repo
    )
    assert edges == {}
    assert len(warnings) == 1


def test_existing_dep_is_not_duplicated(repo: Path) -> None:
    _write(repo, "src/pkg/a.py", "from pkg.b import thing\n")
    _write(repo, "src/pkg/b.py", "")
    edges, warnings = import_dep_edges(
        [_task("1.1", "src/pkg/b.py"), _task("1.2", "src/pkg/a.py", deps=["1.1"])], repo
    )
    assert edges == {}
    assert warnings == []


def test_missing_file_is_skipped(repo: Path) -> None:
    _write(repo, "src/pkg/b.py", "")
    edges, warnings = import_dep_edges(
        [_task("1.1", "src/pkg/b.py"), _task("2.1", "src/pkg/nope.py")], repo
    )
    assert edges == {}
    assert warnings == []


def test_non_py_file_is_skipped(repo: Path) -> None:
    _write(repo, "src/pkg/b.py", "")
    _write(repo, "docs/a.md", "from pkg.b import thing\n")
    edges, _ = import_dep_edges(
        [_task("1.1", "src/pkg/b.py"), _task("2.1", "docs/a.md")], repo
    )
    assert edges == {}


def test_syntax_error_file_is_skipped(repo: Path) -> None:
    _write(repo, "src/pkg/a.py", "from pkg.b import (\n")
    _write(repo, "src/pkg/b.py", "")
    edges, warnings = import_dep_edges(
        [_task("1.1", "src/pkg/b.py"), _task("2.1", "src/pkg/a.py")], repo
    )
    assert edges == {}
    assert warnings == []


def test_undecodable_file_is_skipped(repo: Path) -> None:
    (repo / "src" / "pkg" / "a.py").write_bytes(b"\xff\xfe from pkg.b import x\n")
    _write(repo, "src/pkg/b.py", "")
    edges, warnings = import_dep_edges(
        [_task("1.1", "src/pkg/b.py"), _task("2.1", "src/pkg/a.py")], repo
    )
    assert edges == {}
    assert warnings == []


def test_tail_task_files_are_not_parsed(repo: Path) -> None:
    _write(repo, "src/pkg/a.py", "from pkg.b import thing\n")
    _write(repo, "src/pkg/b.py", "")
    edges, _ = import_dep_edges(
        [_task("1.1", "src/pkg/b.py"), _task("3.1", "src/pkg/a.py", kind="e2e")], repo
    )
    assert edges == {}
