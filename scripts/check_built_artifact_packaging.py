#!/usr/bin/env python3
"""Verify built wheel/sdist metadata agrees with the checkout's declared packaging."""

from __future__ import annotations

import argparse
import configparser
import email.parser
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.check_packaging_metadata import checkout_console_scripts, checkout_version


class BuildError(RuntimeError):
    """Raised when the `python -m build` subprocess fails."""


class MetadataError(RuntimeError):
    """Raised when a built/given artifact is missing expected metadata."""


def checkout_declared(repo: Path) -> tuple[str, frozenset[str]]:
    """Return `repo`'s declared (version, console_scripts) via `check_packaging_metadata`.

    Reuses `checkout_version`/`checkout_console_scripts` for the checkout's
    declared values instead of re-parsing `pyproject.toml` here.
    """
    return checkout_version(repo), frozenset(checkout_console_scripts(repo))


_BUILD_COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    ".venv",
    "__pycache__",
    "*.egg-info",
    "build",
    "dist",
    "node_modules",
    "*.pyc",
)


def build_artifacts(repo: Path, out_dir: Path) -> tuple[Path, Path]:
    """Build a wheel and sdist for `repo` into `out_dir` via `python -m build`.

    Copies `repo` into a disposable temporary directory first and builds from
    that copy: `python -m build`'s setuptools backend runs its metadata-
    generation step with the source tree as cwd and deposits a top-level
    `<pkg>.egg-info/` directory there as a byproduct, so building in `repo`
    itself would leave that behind. Building from a throwaway copy instead
    means `repo` is never touched at all -- `out_dir` remains the only
    externally visible output. Returns the (wheel_path, sdist_path) pair.
    Raises `BuildError` if the build subprocess exits non-zero.
    """
    with tempfile.TemporaryDirectory() as copy_dir:
        source_copy = Path(copy_dir) / "src"
        shutil.copytree(repo, source_copy, ignore=_BUILD_COPY_IGNORE)
        result = subprocess.run(
            [sys.executable, "-m", "build", "--outdir", str(out_dir), str(source_copy)],
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != 0:
        raise BuildError(
            f"python -m build failed for {repo} (exit {result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )
    wheels = sorted(out_dir.glob("*.whl"))
    sdists = sorted(out_dir.glob("*.tar.gz"))
    if not wheels:
        raise BuildError(f"python -m build produced no wheel in {out_dir}")
    if not sdists:
        raise BuildError(f"python -m build produced no sdist in {out_dir}")
    return wheels[-1], sdists[-1]


def resolve_artifacts(
    repo: Path, wheel: Path | None, sdist: Path | None
) -> tuple[Path, Path, tempfile.TemporaryDirectory | None]:
    """Resolve the wheel/sdist paths to check.

    If both `wheel` and `sdist` are given, they are used as-is and no build
    runs. If neither is given, a wheel and sdist are built into a fresh
    `tempfile.TemporaryDirectory()`, whose handle is returned so the caller
    can keep it alive for the duration of the check and clean it up after.
    Supplying only one of the two paths is rejected, since it would mix a
    caller-supplied artifact with one built here. If the build itself fails,
    the temporary directory is cleaned up here before the error propagates,
    since the caller never receives a handle to clean it up with.
    """
    if wheel is not None and sdist is not None:
        return wheel, sdist, None
    if wheel is not None or sdist is not None:
        raise ValueError(
            "--wheel and --sdist must both be given, or neither: "
            f"wheel={wheel!r} sdist={sdist!r}"
        )
    tmp = tempfile.TemporaryDirectory()
    try:
        built_wheel, built_sdist = build_artifacts(repo, Path(tmp.name))
    except BaseException:
        tmp.cleanup()
        raise
    return built_wheel, built_sdist, tmp


def compare_versions(
    declared: str, wheel_version: str, sdist_version: str
) -> list[str]:
    """Compare the wheel's and sdist's extracted version against `declared`.

    Returns one error string per artifact whose extracted version does not
    exactly match `declared` (exact string equality), naming the artifact and
    both values. An empty list means both artifacts agree with `declared`.
    """
    errors: list[str] = []
    if wheel_version != declared:
        errors.append(
            f"wheel version {wheel_version!r} does not match declared version {declared!r}"
        )
    if sdist_version != declared:
        errors.append(
            f"sdist version {sdist_version!r} does not match declared version {declared!r}"
        )
    return errors


def compare_console_scripts(
    declared: frozenset[str], wheel_scripts: frozenset[str]
) -> list[str]:
    """Compare the wheel's extracted `console_scripts` names against `declared`.

    Returns one error string per direction of disagreement (missing names
    declared but absent from the wheel, and/or extra names present in the
    wheel but not declared), naming the differing script(s). An empty list
    means the wheel's console-script names exactly match `declared`.
    """
    errors: list[str] = []
    missing = declared - wheel_scripts
    extra = wheel_scripts - declared
    if missing:
        errors.append(f"wheel is missing declared console_scripts: {sorted(missing)}")
    if extra:
        errors.append(f"wheel has undeclared console_scripts: {sorted(extra)}")
    return errors


def _find_dist_info_member(names: list[str], filename: str) -> str:
    """Return the wheel zip member for `filename` inside its `.dist-info/` dir.

    Raises `MetadataError` if the wheel has zero or more than one such
    member, since either means the wheel's metadata cannot be trusted.
    """
    matches = [name for name in names if name.endswith(f".dist-info/{filename}")]
    if not matches:
        raise MetadataError(f"wheel is missing {filename} in its .dist-info directory")
    if len(matches) > 1:
        raise MetadataError(f"wheel has multiple {filename} entries: {matches}")
    return matches[0]


def extract_wheel_metadata(wheel: Path) -> tuple[str, frozenset[str]]:
    """Extract the version and console-script names from `wheel`.

    Reads `METADATA` and `entry_points.txt` out of the wheel's
    `.dist-info/` directory via `zipfile`. `METADATA`'s `Version` header is
    parsed with `email.parser`; `entry_points.txt`'s `[console_scripts]`
    section is parsed with `configparser`, and a missing section yields an
    empty set rather than an error. Raises `MetadataError` if `METADATA` or
    `entry_points.txt` itself is missing from the wheel, if the wheel cannot
    be opened as a zip archive at all, or if `METADATA` lacks a `Version`
    header.
    """
    try:
        zf = zipfile.ZipFile(wheel)
    except (FileNotFoundError, zipfile.BadZipFile) as exc:
        raise MetadataError(f"wheel {wheel} could not be opened: {exc}") from exc

    with zf:
        names = zf.namelist()

        metadata_name = _find_dist_info_member(names, "METADATA")
        with zf.open(metadata_name) as f:
            metadata = email.parser.BytesParser().parse(f)
        version = metadata["Version"]
        if version is None:
            raise MetadataError(f"wheel {wheel} METADATA is missing a Version header")

        entry_points_name = _find_dist_info_member(names, "entry_points.txt")
        with zf.open(entry_points_name) as f:
            entry_points_text = f.read().decode("utf-8")

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read_string(entry_points_text)
    if parser.has_section("console_scripts"):
        console_scripts = frozenset(parser.options("console_scripts"))
    else:
        console_scripts = frozenset()

    return version, console_scripts


def extract_sdist_version(sdist: Path) -> str:
    """Extract the version from `sdist`'s `PKG-INFO`.

    Reads `PKG-INFO` out of the sdist tarball via `tarfile` and parses its
    `Version` header the same way as the wheel's `METADATA`. A standard
    setuptools sdist contains both the archive-root `PKG-INFO`
    (`<name>-<version>/PKG-INFO`) and an embedded egg-info copy
    (`<name>-<version>/**/*.egg-info/PKG-INFO`); only the root member,
    identified as the shallowest match, is authoritative. Raises
    `MetadataError` if the sdist has no `PKG-INFO` member, or more than one
    at the shallowest depth, since either means the sdist's metadata cannot
    be trusted. Also raises `MetadataError` if the sdist cannot be opened as
    a tar archive at all, or if `PKG-INFO` lacks a `Version` header.
    """
    try:
        tf = tarfile.open(sdist)  # noqa: SIM115 -- opened outside `with` to catch open() errors separately
    except (FileNotFoundError, tarfile.TarError) as exc:
        raise MetadataError(f"sdist {sdist} could not be opened: {exc}") from exc

    with tf:
        matches = [name for name in tf.getnames() if name.endswith("/PKG-INFO")]
        if not matches:
            raise MetadataError("sdist is missing PKG-INFO")
        min_depth = min(name.count("/") for name in matches)
        root_matches = [name for name in matches if name.count("/") == min_depth]
        if len(root_matches) > 1:
            raise MetadataError(
                f"sdist has multiple root PKG-INFO entries: {root_matches}"
            )
        member = tf.extractfile(root_matches[0])
        if member is None:
            raise MetadataError("sdist is missing PKG-INFO")
        with member:
            metadata = email.parser.BytesParser().parse(member)

    version = metadata["Version"]
    if version is None:
        raise MetadataError(f"sdist {sdist} PKG-INFO is missing a Version header")
    return version


def check(
    repo: Path, wheel: Path | None = None, sdist: Path | None = None
) -> list[str]:
    """Compare `repo`'s declared version against its built wheel and sdist.

    Resolves the wheel/sdist to check via `resolve_artifacts` (building both
    if neither is given), extracts each artifact's version and the wheel's
    console-script names, and returns the combined per-artifact mismatch
    diagnostics from `compare_versions` and `compare_console_scripts` against
    the checkout's declared version and `[project.scripts]`.
    """
    repo = repo.resolve()
    declared_version, declared_scripts = checkout_declared(repo)
    wheel_path, sdist_path, tmp = resolve_artifacts(repo, wheel, sdist)
    try:
        wheel_version, wheel_scripts = extract_wheel_metadata(wheel_path)
        sdist_version = extract_sdist_version(sdist_path)
        return compare_versions(
            declared_version, wheel_version, sdist_version
        ) + compare_console_scripts(declared_scripts, wheel_scripts)
    finally:
        if tmp is not None:
            tmp.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--wheel", type=Path, default=None)
    parser.add_argument("--sdist", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        errors = check(args.repo, args.wheel, args.sdist)
    except (BuildError, MetadataError, ValueError, OSError) as exc:
        errors = [str(exc)]
    if errors:
        for error in errors:
            print(f"BUILT ARTIFACT PACKAGING: FAIL — {error}", file=sys.stderr)
        return 1
    print(
        "BUILT ARTIFACT PACKAGING: PASS — wheel and sdist versions and "
        "console_scripts match the checkout"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
