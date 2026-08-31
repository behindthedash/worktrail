#!/usr/bin/env python3
"""Verify built wheel/sdist metadata agrees with the checkout's declared packaging."""

from __future__ import annotations

import configparser
import email.parser
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


class BuildError(RuntimeError):
    """Raised when the `python -m build` subprocess fails."""


class MetadataError(RuntimeError):
    """Raised when a built/given artifact is missing expected metadata."""


def build_artifacts(repo: Path, out_dir: Path) -> tuple[Path, Path]:
    """Build a wheel and sdist for `repo` into `out_dir` via `python -m build`.

    Returns the (wheel_path, sdist_path) pair. Raises `BuildError` if the
    build subprocess exits non-zero.
    """
    result = subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(out_dir), str(repo)],
        capture_output=True,
        text=True,
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
    caller-supplied artifact with one built here.
    """
    if wheel is not None and sdist is not None:
        return wheel, sdist, None
    if wheel is not None or sdist is not None:
        raise ValueError(
            "--wheel and --sdist must both be given, or neither: "
            f"wheel={wheel!r} sdist={sdist!r}"
        )
    tmp = tempfile.TemporaryDirectory()
    built_wheel, built_sdist = build_artifacts(repo, Path(tmp.name))
    return built_wheel, built_sdist, tmp


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
    `entry_points.txt` itself is missing from the wheel.
    """
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()

        metadata_name = _find_dist_info_member(names, "METADATA")
        with zf.open(metadata_name) as f:
            metadata = email.parser.BytesParser().parse(f)
        version = metadata["Version"]

        entry_points_name = _find_dist_info_member(names, "entry_points.txt")
        with zf.open(entry_points_name) as f:
            entry_points_text = f.read().decode("utf-8")

    parser = configparser.ConfigParser()
    parser.read_string(entry_points_text)
    if parser.has_section("console_scripts"):
        console_scripts = frozenset(parser.options("console_scripts"))
    else:
        console_scripts = frozenset()

    return version, console_scripts
