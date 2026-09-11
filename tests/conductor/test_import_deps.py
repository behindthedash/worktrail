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


def test_src_takes_precedence_over_repo_root(repo: Path) -> None:
    _write(repo, "src/pkg/a.py", "import chosen\n")
    _write(repo, "src/chosen.py", "")
    _write(repo, "chosen.py", "")
    edges, _ = import_dep_edges(
        [
            _task("1.1", "src/chosen.py"),
            _task("1.2", "chosen.py"),
            _task("2.1", "src/pkg/a.py"),
        ],
        repo,
    )
    assert edges == {"2.1": ["1.1"]}


def test_module_file_takes_precedence_over_package_init(repo: Path) -> None:
    _write(repo, "src/pkg/a.py", "import choice\n")
    _write(repo, "src/choice.py", "")
    _write(repo, "src/choice/__init__.py", "")
    edges, _ = import_dep_edges(
        [
            _task("1.1", "src/choice.py"),
            _task("1.2", "src/choice/__init__.py"),
            _task("2.1", "src/pkg/a.py"),
        ],
        repo,
    )
    assert edges == {"2.1": ["1.1"]}


def test_repo_root_used_when_absent_under_src(repo: Path) -> None:
    _write(repo, "src/pkg/a.py", "import chosen\n")
    _write(repo, "chosen.py", "")
    edges, _ = import_dep_edges(
        [_task("1.1", "chosen.py"), _task("2.1", "src/pkg/a.py")], repo
    )
    assert edges == {"2.1": ["1.1"]}


def test_declared_file_outside_repo_is_ignored(repo: Path, tmp_path: Path) -> None:
    (tmp_path / "outside.py").write_text("from pkg.b import thing\n")
    _write(repo, "src/pkg/b.py", "")
    edges, warnings = import_dep_edges(
        [
            _task("owner", "src/pkg/b.py"),
            _task("importer", "../outside.py", str(tmp_path / "outside.py")),
        ],
        repo,
    )
    assert edges == {}
    assert warnings == []


# --- TypeScript / JavaScript -------------------------------------------------


def _ts_edges(
    repo: Path, importer_src: str, owner_rel: str, importer_rel: str = "src/a.ts"
):
    _write(repo, importer_rel, importer_src)
    if not (repo / owner_rel).exists():
        _write(repo, owner_rel, "export const x = 1;\n")
    return import_dep_edges([_task("1.1", owner_rel), _task("2.1", importer_rel)], repo)


@pytest.mark.parametrize(
    "src",
    [
        'import { x } from "./b";\n',
        "import x from './b';\n",
        'import "./b";\n',
        'export { x } from "./b";\n',
        'export * from "./b";\n',
        'const m = await import("./b");\n',
        'const m = require("./b");\n',
        'import type { T } from "./b";\n',
    ],
)
def test_ts_specifier_forms(repo: Path, src: str) -> None:
    edges, warnings = _ts_edges(repo, src, "src/b.ts")
    assert edges == {"2.1": ["1.1"]}
    assert warnings == []


def test_ts_specifier_as_written(repo: Path) -> None:
    edges, _ = _ts_edges(repo, 'import "./b.ts";\n', "src/b.ts")
    assert edges == {"2.1": ["1.1"]}


def test_ts_as_written_wins_over_extension_append(repo: Path) -> None:
    _write(repo, "src/b", "")
    _write(repo, "src/b.ts", "")
    _write(repo, "src/a.ts", 'import "./b";\n')
    edges, _ = import_dep_edges(
        [_task("1.1", "src/b"), _task("1.2", "src/b.ts"), _task("2.1", "src/a.ts")],
        repo,
    )
    assert edges == {"2.1": ["1.1"]}


def test_ts_extension_append_order(repo: Path) -> None:
    _write(repo, "src/b.tsx", "")
    _write(repo, "src/b.js", "")
    _write(repo, "src/a.ts", 'import "./b";\n')
    edges, _ = import_dep_edges(
        [_task("1.1", "src/b.tsx"), _task("1.2", "src/b.js"), _task("2.1", "src/a.ts")],
        repo,
    )
    assert edges == {"2.1": ["1.1"]}


@pytest.mark.parametrize(
    "js, ts", [(".js", ".ts"), (".jsx", ".tsx"), (".mjs", ".mts"), (".cjs", ".cts")]
)
def test_js_suffix_rewritten_to_ts_source(repo: Path, js: str, ts: str) -> None:
    edges, _ = _ts_edges(repo, f'import {{ x }} from "./b{js}";\n', f"src/b{ts}")
    assert edges == {"2.1": ["1.1"]}


def test_js_as_written_wins_over_ts_rewrite(repo: Path) -> None:
    _write(repo, "src/b.js", "")
    _write(repo, "src/b.ts", "")
    _write(repo, "src/a.ts", 'import "./b.js";\n')
    edges, _ = import_dep_edges(
        [_task("1.1", "src/b.js"), _task("1.2", "src/b.ts"), _task("2.1", "src/a.ts")],
        repo,
    )
    assert edges == {"2.1": ["1.1"]}


def test_ts_directory_index(repo: Path) -> None:
    edges, _ = _ts_edges(repo, 'import { x } from "./lib";\n', "src/lib/index.ts")
    assert edges == {"2.1": ["1.1"]}


def test_ts_parent_relative_specifier(repo: Path) -> None:
    edges, _ = _ts_edges(
        repo, 'import "../b";\n', "src/b.ts", importer_rel="src/sub/a.ts"
    )
    assert edges == {"2.1": ["1.1"]}


@pytest.mark.parametrize(
    "spec", ["react", "@scope/pkg", "lodash/fp", "@/lib/b", "b", "src/b"]
)
def test_bare_and_alias_specifiers_are_ignored(repo: Path, spec: str) -> None:
    _write(repo, "src/lib/b.ts", "")
    _write(repo, "src/b.ts", "")
    _write(repo, "src/a.ts", f'import {{ x }} from "{spec}";\n')
    edges, warnings = import_dep_edges(
        [
            _task("1.1", "src/b.ts"),
            _task("1.2", "src/lib/b.ts"),
            _task("2.1", "src/a.ts"),
        ],
        repo,
    )
    assert edges == {}
    assert warnings == []


def test_ts_specifier_escaping_repo_is_ignored(repo: Path, tmp_path: Path) -> None:
    (tmp_path / "outside.ts").write_text("")
    _write(repo, "a.ts", 'import "../outside";\n')
    edges, warnings = import_dep_edges(
        [_task("1.1", "../outside.ts"), _task("2.1", "a.ts")], repo
    )
    assert edges == {}
    assert warnings == []


def test_ts_mutual_import_keeps_one_edge_and_warns(repo: Path) -> None:
    _write(repo, "src/a.ts", 'import { b } from "./b";\n')
    _write(repo, "src/b.ts", 'import { a } from "./a";\n')
    edges, warnings = import_dep_edges(
        [_task("1.1", "src/a.ts"), _task("1.2", "src/b.ts")], repo
    )
    assert edges == {"1.1": ["1.2"]}
    assert len(warnings) == 1
    assert "1.2" in warnings[0] and "1.1" in warnings[0]


def test_js_importer_is_scanned(repo: Path) -> None:
    edges, _ = _ts_edges(
        repo, 'const b = require("./b");\n', "src/b.js", importer_rel="src/a.js"
    )
    assert edges == {"2.1": ["1.1"]}


def test_md_file_mentioning_ts_import_is_skipped(repo: Path) -> None:
    _write(repo, "src/b.ts", "")
    _write(repo, "docs/a.md", 'import { x } from "./b";\nimport "../src/b";\n')
    edges, warnings = import_dep_edges(
        [_task("1.1", "src/b.ts"), _task("2.1", "docs/a.md")], repo
    )
    assert edges == {}
    assert warnings == []
