"""Tests for the dependency freshness precondition."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from worktrail.workqueue.dependency_freshness import (
    check_dependency_freshness,
    format_freshness_block,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def _write_lock(
    root: Path, deps: dict[str, str], dev: dict[str, str] | None = None
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    packages: dict = {"": {"dependencies": deps, "devDependencies": dev or {}}}
    for name, version in {**deps, **(dev or {})}.items():
        packages[f"node_modules/{name}"] = {"version": version}
    lock = root / "package-lock.json"
    lock.write_text(json.dumps({"lockfileVersion": 3, "packages": packages}))
    return lock


def _install(root: Path, name: str, version: str) -> None:
    pkg = root / "node_modules" / name
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"name": name, "version": version}))


def _track(repo: Path, *paths: Path) -> None:
    subprocess.run(["git", "add", "-f", *[str(p) for p in paths]], cwd=repo, check=True)


def test_fresh_root(repo: Path) -> None:
    lock = _write_lock(repo, {"left-pad": "1.3.0"}, {"vitest": "4.1.11"})
    _install(repo, "left-pad", "1.3.0")
    _install(repo, "vitest", "4.1.11")
    _track(repo, lock)

    results = check_dependency_freshness(repo)

    assert len(results) == 1
    entry = results[0]
    assert entry["app_dir"] == "."
    assert entry["lockfile"] == "package-lock.json"
    assert entry["status"] == "fresh"
    assert entry["mismatches"] == []


def test_stale_version(repo: Path) -> None:
    lock = _write_lock(repo, {}, {"vitest": "5.0.0"})
    _install(repo, "vitest", "4.1.11")
    _track(repo, lock)

    [entry] = check_dependency_freshness(repo)

    assert entry["status"] == "stale"
    assert entry["mismatches"] == [
        {"name": "vitest", "locked": "5.0.0", "installed": "4.1.11"}
    ]
    assert "vitest" in entry["detail"]


def test_missing_dependency(repo: Path) -> None:
    lock = _write_lock(repo, {"left-pad": "1.3.0"})
    _track(repo, lock)

    [entry] = check_dependency_freshness(repo)

    assert entry["status"] == "stale"
    assert entry["mismatches"] == [
        {"name": "left-pad", "locked": "1.3.0", "installed": "missing"}
    ]


def test_lockfile_without_packages_is_unknown(repo: Path) -> None:
    lock = repo / "package-lock.json"
    lock.write_text(json.dumps({"lockfileVersion": 1, "dependencies": {}}))
    _track(repo, lock)

    [entry] = check_dependency_freshness(repo)

    assert entry["status"] == "unknown"
    assert entry["mismatches"] == []
    assert "packages" in entry["detail"]


def test_unparseable_lockfile_is_unknown(repo: Path) -> None:
    lock = repo / "package-lock.json"
    lock.write_text("{not json")
    _track(repo, lock)

    [entry] = check_dependency_freshness(repo)

    assert entry["status"] == "unknown"


def test_nested_app_root(repo: Path) -> None:
    app = repo / "app"
    lock = _write_lock(app, {"left-pad": "1.3.0"})
    _install(app, "left-pad", "1.3.0")
    _track(repo, lock)

    [entry] = check_dependency_freshness(repo)

    assert entry["app_dir"] == "app"
    assert entry["lockfile"] == "app/package-lock.json"
    assert entry["status"] == "fresh"


def test_untracked_lockfile_is_ignored(repo: Path) -> None:
    _write_lock(repo, {"left-pad": "1.3.0"})
    assert check_dependency_freshness(repo) == []


def test_no_lockfile(repo: Path) -> None:
    assert check_dependency_freshness(repo) == []


def test_check_is_read_only(repo: Path) -> None:
    lock = _write_lock(repo, {"left-pad": "1.3.0"})
    _track(repo, lock)
    before = sorted(p for p in repo.rglob("*") if ".git" not in p.parts)

    check_dependency_freshness(repo)

    after = sorted(p for p in repo.rglob("*") if ".git" not in p.parts)
    assert before == after
    assert (
        subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        == "A  package-lock.json"
    )


def test_format_block_empty() -> None:
    assert "no npm package roots found" in format_freshness_block([])


def test_format_block_renders_roots() -> None:
    results = [
        {
            "app_dir": ".",
            "lockfile": "package-lock.json",
            "status": "fresh",
            "mismatches": [],
            "detail": ".: 1 pinned package(s) match node_modules",
        },
        {
            "app_dir": "app",
            "lockfile": "app/package-lock.json",
            "status": "stale",
            "mismatches": [
                {"name": "vitest", "locked": "5.0.0", "installed": "4.1.11"}
            ],
            "detail": "app: 1 mismatch(es): vitest 5.0.0 -> 4.1.11",
        },
    ]

    block = format_freshness_block(results)

    assert "- . (package-lock.json): fresh" in block
    assert "- app (app/package-lock.json): stale" in block
    assert "vitest: locked 5.0.0, installed 4.1.11" in block
