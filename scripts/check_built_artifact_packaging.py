#!/usr/bin/env python3
"""Verify built wheel/sdist metadata agrees with the checkout's declared packaging."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


class BuildError(RuntimeError):
    """Raised when the `python -m build` subprocess fails."""


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
