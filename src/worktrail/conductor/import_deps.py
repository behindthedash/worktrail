"""Infer plan edges from Python and TypeScript/JavaScript imports between
tasks' declared files.

A task whose on-disk `.py` (or `.ts`/`.tsx`/`.js`/... ) file imports a module
another task declares in its `files:` depends on that task, whether or not the
author said so. The change that motivated this (go-20260910-085218) had
`dashboard.py` importing `smoke_flake_selfcheck` with disjoint `files:` on both
tasks, so the plan ran them in parallel and the importer's worktree never saw
the module.

Python imports are read with `ast`, including dynamic loading via
`importlib.util.spec_from_file_location(name, path)` (path as a repo-relative string literal,
an inline `Path(__file__)<.resolve()><.parent>* / "file.py"` expression, or a variable
assigned from that same expression shape) and `importlib.import_module("dotted.name")`.
Any other path expression is left unresolved rather than guessed at. TypeScript/JavaScript
files are scanned
with a regex for the string-literal specifier of `import ... from`, side-effect
`import`, `export ... from`, dynamic `import(...)`, and `require(...)`; only
`./` and `../` specifiers are resolved (against the importing file's directory,
trying the path as written, each supported extension, the `.js`-family suffix
rewritten to its `.ts`-family source, then an `index` file). Bare package
specifiers (`react`, `@scope/pkg`) and `tsconfig.json` path aliases (`@/lib/x`)
are dropped without resolution, so an aliased import is silently unordered.

Only imports already on disk can be seen here; a module a task is about to
create is invisible, which is what the `depends:` continuation line is for.
Inference is additive and never fails a compile: unreadable or unparseable
files are skipped, and an edge that would close a cycle is dropped with a
warning naming both tasks.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from worktrail.conductor import runplan
from worktrail.orchestrator.coordinator import TAIL_KINDS

# Absolute imports are tried under `src/` first, then the repo root.
_ABS_ROOTS = ("src", "")


def _module_candidates(base: Path, parts: Sequence[str]) -> list[Path]:
    """Paths a dotted module could live at, in precedence order: `a/b.py`, then
    `a/b/__init__.py`."""
    if not parts:
        return [base / "__init__.py"]
    stem = base.joinpath(*parts)
    return [stem.with_suffix(".py"), stem / "__init__.py"]


def _resolve(bases: Sequence[Path], parts: Sequence[str]) -> Path | None:
    """First candidate under `bases` (in order) that exists on disk, else None."""
    for base in bases:
        for cand in _module_candidates(base, parts):
            if cand.is_file():
                return cand
    return None


def _under_repo(path: Path, repo: Path) -> Path | None:
    """`path` relative to `repo` if it lies inside it, else None."""
    try:
        return path.resolve().relative_to(repo.resolve())
    except ValueError:
        return None


def _imported_paths(tree: ast.AST, file: Path, repo: Path) -> list[Path]:
    """Repo-relative paths the imports in `tree` resolve to, in source order.

    Each import resolves to at most one path per dotted name: absolute names are
    tried under `src/` then the repo root, and at each root the module file is
    preferred over the package `__init__.py`. Names that resolve nowhere on disk
    are dropped.
    """
    abs_bases = [repo / root for root in _ABS_ROOTS]
    out: list[Path] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if (hit := _resolve(abs_bases, alias.name.split("."))) is not None:
                    out.append(hit)
        elif isinstance(node, ast.ImportFrom):
            parts = node.module.split(".") if node.module else []
            if node.level:
                base = file.parent
                for _ in range(node.level - 1):
                    base = base.parent
                bases = [base]
            else:
                bases = abs_bases
            if (hit := _resolve(bases, parts)) is not None:
                out.append(hit)
            # `from pkg import mod` -- the imported name may be a submodule.
            for alias in node.names:
                if alias.name == "*":
                    continue
                if (hit := _resolve(bases, [*parts, alias.name])) is not None:
                    out.append(hit)
    return [rel for p in out if (rel := _under_repo(p, repo)) is not None]


# TypeScript/JavaScript resolution: extensions tried when a relative specifier
# has none, and the emitted-name -> source-name rewrite for the ESM convention.
_JS_EXTS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".d.ts")
_JS_TO_TS = {".js": ".ts", ".jsx": ".tsx", ".mjs": ".mts", ".cjs": ".cts"}
_JS_SUFFIXES = frozenset(_JS_EXTS) - {".d.ts"}

_JS_SPECIFIER = re.compile(
    r"""(?:
        \b(?:import|export)\b[^'"`;]*?\bfrom\s*  # import x from / export * from
      | \bimport\s*                              # side-effect import "x"
      | \bimport\s*\(\s*                         # dynamic import("x")
      | \brequire\s*\(\s*                        # require("x")
    )(['"])([^'"\n]+)\1""",
    re.VERBOSE,
)


def _js_candidates(stem: Path) -> list[Path]:
    """Paths a relative specifier could resolve to, in precedence order."""
    out = [stem]
    out.extend(stem.with_name(stem.name + ext) for ext in _JS_EXTS)
    for js, ts in _JS_TO_TS.items():
        if stem.name.endswith(js):
            out.append(stem.with_name(stem.name[: -len(js)] + ts))
    out.extend(stem / f"index{ext}" for ext in _JS_EXTS)
    return out


def _js_imported_paths(text: str, file: Path, repo: Path) -> list[Path]:
    """Repo-relative paths the relative specifiers in `text` resolve to, in
    source order. Bare and alias specifiers are dropped."""
    out: list[Path] = []
    for m in _JS_SPECIFIER.finditer(text):
        spec = m.group(2)
        if not spec.startswith(("./", "../")):
            continue
        for cand in _js_candidates(file.parent / spec):
            if cand.is_file():
                out.append(cand)
                break
    return [rel for p in out if (rel := _under_repo(p, repo)) is not None]


def _path_from_file_hops(node: ast.AST) -> int | None:
    """Number of `.parent` hops on top of `Path(__file__)` (an optional leading `.resolve()`
    doesn't count as a hop), or None if `node` isn't that shape."""
    hops = 0
    cur = node
    while isinstance(cur, ast.Attribute) and cur.attr == "parent":
        hops += 1
        cur = cur.value
    if (
        isinstance(cur, ast.Call)
        and isinstance(cur.func, ast.Attribute)
        and cur.func.attr == "resolve"
    ):
        cur = cur.func.value
    if (
        isinstance(cur, ast.Call)
        and isinstance(cur.func, ast.Name)
        and cur.func.id == "Path"
        and len(cur.args) == 1
        and isinstance(cur.args[0], ast.Name)
        and cur.args[0].id == "__file__"
    ):
        return hops
    return None


def _dynamic_path_literal(node: ast.AST) -> tuple[int, str] | None:
    """`(hops, filename)` for `Path(__file__)<.resolve()><.parent>* / "filename"`, else None."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        hops = _path_from_file_hops(node.left)
        if (
            hops is not None
            and isinstance(node.right, ast.Constant)
            and isinstance(node.right.value, str)
        ):
            return hops, node.right.value
    return None


def _assignment_dynamic_paths(tree: ast.AST) -> dict[str, tuple[int, str]]:
    """Name -> `(hops, filename)` for `NAME = Path(__file__)<.resolve()><.parent>* / "x"`."""
    out: dict[str, tuple[int, str]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and (resolved := _dynamic_path_literal(node.value)) is not None
        ):
            out[node.targets[0].id] = resolved
    return out


def _dynamic_imported_paths(tree: ast.AST, file: Path, repo: Path) -> list[Path]:
    """Repo-relative paths from `importlib.util.spec_from_file_location(...)` and
    `importlib.import_module(...)` calls, in source order.

    `spec_from_file_location`'s path argument is resolved when it is a string literal, an
    inline `Path(__file__)<.resolve()><.parent>* / "x"` expression, or a Name bound to that
    expression by an earlier assignment; any other expression is left unresolved.
    """
    abs_bases = [repo / root for root in _ABS_ROOTS]
    name_paths = _assignment_dynamic_paths(tree)
    out: list[Path] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr == "spec_from_file_location" and len(node.args) >= 2:
            arg = node.args[1]
            resolved = _dynamic_path_literal(arg)
            if resolved is None and isinstance(arg, ast.Name):
                resolved = name_paths.get(arg.id)
            if resolved is not None:
                hops, filename = resolved
                if hops >= 1 and hops <= len(file.parents):
                    out.append(file.parents[hops - 1] / filename)
            elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                out.append(repo / arg.value)
        elif node.func.attr == "import_module" and node.args:
            arg = node.args[0]
            if (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and (hit := _resolve(abs_bases, arg.value.split("."))) is not None
            ):
                out.append(hit)
    return [rel for p in out if (rel := _under_repo(p, repo)) is not None]


def _py_imported_paths(text: str, file: Path, repo: Path) -> list[Path]:
    tree = ast.parse(text, filename=str(file))
    return _imported_paths(tree, file, repo) + _dynamic_imported_paths(tree, file, repo)


_SCANNERS = {
    ".py": _py_imported_paths,
    **{ext: _js_imported_paths for ext in _JS_SUFFIXES},
}


def _reachable(start: str, target: str, deps: Mapping[str, Sequence[str]]) -> bool:
    """True if following `deps` edges from `start` arrives at `target`."""
    seen = {start}
    stack = [start]
    while stack:
        cur = stack.pop()
        if cur == target:
            return True
        for nxt in deps.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


def import_dep_edges(
    tasks: Sequence[Mapping[str, Any]], repo: Path
) -> tuple[dict[str, list[str]], list[str]]:
    """`{importer id: [owner ids]}` inferred from on-disk imports, plus warnings.

    For each non-tail task, every declared file present under `repo` (a path
    escaping it is ignored) whose suffix has a scanner is read and its imports
    resolved to paths (Python: relative against the file's package, absolute
    under `src/` then the repo root; TypeScript/JavaScript: `./` and `../`
    specifiers against the file's directory); a path another task
    declares yields an importer -> owner edge. An edge is added only if the
    owner cannot already reach the importer through the edges present so far
    (baseline `deps` plus edges added earlier in authored order); otherwise it
    is skipped with a warning, so inference never introduces a cycle.
    """
    repo = Path(repo)
    owners: dict[str, list[str]] = {}
    for t in tasks:
        tid = str(t.get("id"))
        for f in runplan._norm_str_list(t.get("files")):
            owners.setdefault(Path(f).as_posix(), []).append(tid)

    deps: dict[str, list[str]] = {
        str(t.get("id")): list(runplan._norm_str_list(t.get("deps"))) for t in tasks
    }
    edges: dict[str, list[str]] = {}
    warnings: list[str] = []
    for t in tasks:
        if str(t.get("kind") or "") in TAIL_KINDS:
            continue
        tid = str(t.get("id"))
        for f in runplan._norm_str_list(t.get("files")):
            scanner = _SCANNERS.get(Path(f).suffix)
            if scanner is None:
                continue
            path = repo / f
            if _under_repo(path, repo) is None or not path.is_file():
                continue
            try:
                targets = scanner(path.read_text(encoding="utf-8"), path, repo)
            except (SyntaxError, ValueError, UnicodeDecodeError, OSError):
                continue
            for target in targets:
                for owner in owners.get(target.as_posix(), ()):
                    if owner == tid or owner in deps.get(tid, ()):
                        continue
                    if _reachable(owner, tid, deps):
                        warnings.append(
                            f"{tid}: import of {target.as_posix()} implies a dependency "
                            f"on {owner}, skipped because {owner} already depends on {tid}"
                        )
                        continue
                    deps.setdefault(tid, []).append(owner)
                    edges.setdefault(tid, []).append(owner)
    return edges, warnings
