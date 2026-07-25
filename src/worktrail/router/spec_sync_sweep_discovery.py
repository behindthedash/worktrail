#!/usr/bin/env python3
"""spec_sync_sweep_discovery.py — repo discovery for the recurring spec-sync drift sweep.

Reuses `policy_selfcheck.py::discover_repo_names()` (the existing, self-maintaining,
filesystem-based "every git repo under repos-root" convention) and adds one filter: the repo
must currently have a `docs/specs/` directory. No repo list is hardcoded or manually
maintained -- discovery reads the filesystem fresh on every call, so a repo added or removed
under repos-root between runs is reflected automatically with zero configuration change.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from .policy_selfcheck import discover_repo_names


def discover_repos_with_specs(repos_root: Path) -> List[Path]:
    """Every git repo under `repos_root` that currently has a `docs/specs/` directory."""
    repos_root = Path(repos_root)
    return [
        repos_root / name
        for name in discover_repo_names(repos_root)
        if (repos_root / name / "docs" / "specs").is_dir()
    ]
