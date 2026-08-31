from __future__ import annotations

import hashlib
import importlib.metadata
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts import check_built_artifact_packaging as packaging


def _minimal_repo(
    tmp_path: Path, *, version: str = "0.1.0", scripts: dict[str, str] | None = None
) -> Path:
    """A minimal synthetic, buildable package: one module, one console script."""
    if scripts is None:
        scripts = {"mypkg-run": "mypkg.main:run"}
    tmp_path.mkdir(parents=True, exist_ok=True)
    scripts_block = "".join(
        f'{name} = "{target}"\n' for name, target in scripts.items()
    )
    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\n"
        'requires = ["setuptools>=68"]\n'
        'build-backend = "setuptools.build_meta"\n\n'
        "[project]\n"
        'name = "mypkg"\n'
        f'version = "{version}"\n\n'
        "[project.scripts]\n"
        f"{scripts_block}",
        encoding="utf-8",
    )
    pkg_dir = tmp_path / "mypkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "main.py").write_text(
        "def run() -> None:\n    print('hello')\n"
        "def extra() -> None:\n    print('extra')\n",
        encoding="utf-8",
    )
    return tmp_path


def test_check_passes_for_a_matching_checkout(tmp_path: Path) -> None:
    repo = _minimal_repo(tmp_path)
    assert packaging.check(repo) == []


def test_check_fails_on_version_drift_naming_both_values(tmp_path: Path) -> None:
    built_repo = _minimal_repo(tmp_path / "built", version="0.1.0")
    out_dir = tmp_path / "dist"
    out_dir.mkdir()
    wheel, sdist = packaging.build_artifacts(built_repo, out_dir)

    declared_repo = _minimal_repo(tmp_path / "declared", version="0.2.0")

    errors = packaging.check(declared_repo, wheel=wheel, sdist=sdist)

    assert any("0.1.0" in e and "0.2.0" in e and "wheel" in e for e in errors)
    assert any("0.1.0" in e and "0.2.0" in e and "sdist" in e for e in errors)


def test_check_fails_on_missing_console_script_naming_it(tmp_path: Path) -> None:
    built_repo = _minimal_repo(
        tmp_path / "built", scripts={"mypkg-run": "mypkg.main:run"}
    )
    out_dir = tmp_path / "dist"
    out_dir.mkdir()
    wheel, sdist = packaging.build_artifacts(built_repo, out_dir)

    declared_repo = _minimal_repo(
        tmp_path / "declared",
        scripts={
            "mypkg-run": "mypkg.main:run",
            "mypkg-extra": "mypkg.main:extra",
        },
    )

    errors = packaging.check(declared_repo, wheel=wheel, sdist=sdist)

    assert any("missing" in e and "mypkg-extra" in e for e in errors)


def _malform_pyproject_for_build(repo: Path) -> None:
    """Append TOML syntax that breaks the real `build` backend's parser.

    `check_packaging_metadata`'s own `pyproject.toml` reader is a line-based
    scanner that only looks for string-valued `key = "value"` lines inside
    `[project]`/`[project.scripts]`, so it tolerates this garbage line (and
    `checkout_declared` still succeeds); a real TOML parser, as used by the
    `python -m build` subprocess, does not.
    """
    with (repo / "pyproject.toml").open("a", encoding="utf-8") as f:
        f.write("this is not valid toml syntax\n")


def test_check_raises_build_error_on_malformed_pyproject(tmp_path: Path) -> None:
    repo = _minimal_repo(tmp_path)
    _malform_pyproject_for_build(repo)

    with pytest.raises(packaging.BuildError) as excinfo:
        packaging.check(repo)

    message = str(excinfo.value)
    assert "python -m build failed" in message
    assert str(repo) in message
    assert "exit" in message


def test_main_reports_build_failure_without_crashing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _minimal_repo(tmp_path)
    _malform_pyproject_for_build(repo)

    exit_code = packaging.main(["--repo", str(repo)])

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert "BUILT ARTIFACT PACKAGING: FAIL" in stderr
    assert "python -m build failed" in stderr
    assert str(repo) in stderr
    assert "exit" in stderr


def _wheel_without_member(wheel: Path, out_path: Path, *, suffix: str) -> Path:
    """Copy `wheel` to `out_path`, dropping the `.dist-info/` member ending in `suffix`."""
    with zipfile.ZipFile(wheel) as src, zipfile.ZipFile(out_path, "w") as dst:
        for info in src.infolist():
            if info.filename.endswith(f".dist-info/{suffix}"):
                continue
            dst.writestr(info, src.read(info.filename))
    return out_path


def _sdist_without_pkg_info(sdist: Path, out_path: Path) -> Path:
    """Copy `sdist` to `out_path`, dropping every `PKG-INFO` member."""
    with tarfile.open(sdist) as src, tarfile.open(out_path, "w:gz") as dst:
        for member in src.getmembers():
            if member.name.endswith("/PKG-INFO"):
                continue
            extracted = src.extractfile(member) if member.isfile() else None
            dst.addfile(member, extracted)
    return out_path


def test_check_raises_metadata_error_on_wheel_missing_metadata_file(
    tmp_path: Path,
) -> None:
    repo = _minimal_repo(tmp_path / "repo")
    out_dir = tmp_path / "dist"
    out_dir.mkdir()
    wheel, sdist = packaging.build_artifacts(repo, out_dir)
    broken_wheel = _wheel_without_member(
        wheel, tmp_path / "broken.whl", suffix="METADATA"
    )

    with pytest.raises(packaging.MetadataError) as excinfo:
        packaging.check(repo, wheel=broken_wheel, sdist=sdist)

    assert "METADATA" in str(excinfo.value)


def test_check_raises_metadata_error_on_wheel_missing_entry_points(
    tmp_path: Path,
) -> None:
    repo = _minimal_repo(tmp_path / "repo")
    out_dir = tmp_path / "dist"
    out_dir.mkdir()
    wheel, sdist = packaging.build_artifacts(repo, out_dir)
    broken_wheel = _wheel_without_member(
        wheel, tmp_path / "broken.whl", suffix="entry_points.txt"
    )

    with pytest.raises(packaging.MetadataError) as excinfo:
        packaging.check(repo, wheel=broken_wheel, sdist=sdist)

    assert "entry_points.txt" in str(excinfo.value)


def test_check_raises_metadata_error_on_sdist_missing_pkg_info(tmp_path: Path) -> None:
    repo = _minimal_repo(tmp_path / "repo")
    out_dir = tmp_path / "dist"
    out_dir.mkdir()
    wheel, sdist = packaging.build_artifacts(repo, out_dir)
    broken_sdist = _sdist_without_pkg_info(sdist, tmp_path / "broken.tar.gz")

    with pytest.raises(packaging.MetadataError) as excinfo:
        packaging.check(repo, wheel=wheel, sdist=broken_sdist)

    assert "PKG-INFO" in str(excinfo.value)


def test_main_reports_missing_metadata_file_without_crashing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _minimal_repo(tmp_path / "repo")
    out_dir = tmp_path / "dist"
    out_dir.mkdir()
    wheel, sdist = packaging.build_artifacts(repo, out_dir)
    broken_wheel = _wheel_without_member(
        wheel, tmp_path / "broken.whl", suffix="METADATA"
    )

    exit_code = packaging.main(
        ["--repo", str(repo), "--wheel", str(broken_wheel), "--sdist", str(sdist)]
    )

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert "BUILT ARTIFACT PACKAGING: FAIL" in stderr
    assert "METADATA" in stderr


def _snapshot_tree(root: Path) -> dict[str, str]:
    """Return `{relative_path: sha256_of_contents}` for every file under `root`."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_check_is_hermetic_given_prebuilt_artifacts(tmp_path: Path) -> None:
    """Running `check` with pre-built `--wheel`/`--sdist` paths never builds, so
    it must leave `repo` byte-for-byte identical (no new, removed, or modified
    files at all — not even the `*.egg-info` byproduct a build would leave),
    and must not alter the currently-installed `worktrail` distribution's
    metadata.
    """
    repo = _minimal_repo(tmp_path / "repo")
    out_dir = tmp_path / "dist"
    out_dir.mkdir()
    wheel, sdist = packaging.build_artifacts(repo, out_dir)

    before_tree = _snapshot_tree(repo)
    before_metadata = dict(importlib.metadata.metadata("worktrail"))

    errors = packaging.check(repo, wheel=wheel, sdist=sdist)

    assert errors == []
    after_tree = _snapshot_tree(repo)
    assert after_tree == before_tree
    assert dict(importlib.metadata.metadata("worktrail")) == before_metadata


def test_check_given_prebuilt_artifacts_never_invokes_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given `--wheel`/`--sdist` paths, `check` reads them directly and never
    invokes `python -m build` -- `resolve_artifacts` must take the
    both-paths-given branch, not fall through to `build_artifacts`.
    """
    repo = _minimal_repo(tmp_path / "repo")
    out_dir = tmp_path / "dist"
    out_dir.mkdir()
    wheel, sdist = packaging.build_artifacts(repo, out_dir)

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "build subprocess must not run when --wheel/--sdist are given"
        )

    monkeypatch.setattr(subprocess, "run", _fail_if_called)

    errors = packaging.check(repo, wheel=wheel, sdist=sdist)

    assert errors == []


def test_check_is_hermetic(tmp_path: Path) -> None:
    """Running `check` with no pre-built artifacts builds a wheel/sdist via
    `python -m build`. `build_artifacts` builds from a disposable copy of
    `repo` (not `repo` itself), so `repo` must come out byte-for-byte
    identical: no new, removed, or modified file at all -- not even the
    `<pkg>.egg-info/` metadata-generation byproduct a build run in-place
    would leave. It must also leave the currently-installed `worktrail`
    distribution's metadata untouched, since the build happens against an
    unrelated synthetic repo.
    """
    repo = _minimal_repo(tmp_path)
    before_tree = _snapshot_tree(repo)
    before_metadata = dict(importlib.metadata.metadata("worktrail"))

    errors = packaging.check(repo)

    assert errors == []
    after_tree = _snapshot_tree(repo)
    assert after_tree == before_tree
    assert dict(importlib.metadata.metadata("worktrail")) == before_metadata


def _real_worktrail_repo(tmp_path: Path) -> Path:
    """Copy this checkout's own packaging inputs (`pyproject.toml`, `README.md`,
    `LICENSE`, `src/`) into a disposable `tmp_path`, so `packaging.check()` can
    build a real `worktrail` sdist/wheel without writing into this worktree.
    """
    checkout_root = Path(__file__).resolve().parents[1]
    dest = tmp_path / "worktrail_copy"
    shutil.copytree(checkout_root / "src", dest / "src")
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(checkout_root / name, dest / name)
    return dest


def test_check_does_not_affect_installed_worktrail_metadata(tmp_path: Path) -> None:
    """Checking a repo that is actually `worktrail` (a disposable copy of this
    checkout, not the checkout itself) must not shift what
    `importlib.metadata.metadata("worktrail")` resolves to for the running
    interpreter, whose `worktrail` is installed editable from a different
    path entirely. Unlike checking the unrelated synthetic `mypkg` fixture in
    `test_check_is_hermetic`, building this repo's own name/version/entry
    points is the case where a leak into the installed distribution's
    metadata could plausibly occur.

    `repo` here is itself a disposable copy (see `_real_worktrail_repo`), and
    `build_artifacts` copies it again before building, so it must also come
    out byte-for-byte identical -- same bar as `test_check_is_hermetic`, for
    a real, `src/`-layout, dependency-bearing package instead of the flat,
    dependency-free `mypkg` fixture.
    """
    repo = _real_worktrail_repo(tmp_path)
    before_tree = _snapshot_tree(repo)
    before_metadata = dict(importlib.metadata.metadata("worktrail"))

    errors = packaging.check(repo)

    assert errors == []
    after_tree = _snapshot_tree(repo)
    assert after_tree == before_tree
    assert dict(importlib.metadata.metadata("worktrail")) == before_metadata


def test_check_fails_on_extra_console_script_naming_it(tmp_path: Path) -> None:
    built_repo = _minimal_repo(
        tmp_path / "built",
        scripts={
            "mypkg-run": "mypkg.main:run",
            "mypkg-extra": "mypkg.main:extra",
        },
    )
    out_dir = tmp_path / "dist"
    out_dir.mkdir()
    wheel, sdist = packaging.build_artifacts(built_repo, out_dir)

    declared_repo = _minimal_repo(
        tmp_path / "declared", scripts={"mypkg-run": "mypkg.main:run"}
    )

    errors = packaging.check(declared_repo, wheel=wheel, sdist=sdist)

    assert any("undeclared" in e and "mypkg-extra" in e for e in errors)
