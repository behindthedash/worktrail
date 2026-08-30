#!/usr/bin/env python3
"""Verify checkout, installed, and Codex-plugin packaging metadata agree."""

from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import re
import sys
from pathlib import Path


def _project_section(pyproject: Path, section: str) -> dict[str, str]:
    """Read the simple string-valued project tables Worktrail uses.

    This intentionally avoids a TOML dependency so the post-install check also
    works on Worktrail's minimum Python 3.10 before optional dev dependencies
    are imported.
    """
    values: dict[str, str] = {}
    active = False
    heading = f"[{section}]"
    for raw_line in pyproject.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            active = line == heading
            continue
        if not active or not line or line.startswith("#"):
            continue
        match = re.fullmatch(r'([A-Za-z0-9_-]+)\s*=\s*"([^"]*)"(?:\s*#.*)?', line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def checkout_console_scripts(repo: Path) -> set[str]:
    scripts = set(_project_section(repo / "pyproject.toml", "project.scripts"))
    if not scripts:
        raise ValueError("pyproject.toml has no [project.scripts] entries")
    return scripts


def checkout_version(repo: Path) -> str:
    version = _project_section(repo / "pyproject.toml", "project").get("version")
    if not version:
        raise ValueError("pyproject.toml has no string-valued [project] version")
    return version


def installed_distribution() -> tuple[str, set[str]]:
    distribution = md.distribution("worktrail")
    return distribution.version, {
        ep.name for ep in distribution.entry_points if ep.group == "console_scripts"
    }


def check(repo: Path) -> list[str]:
    repo = repo.resolve()
    declared = checkout_console_scripts(repo)
    installed_version, installed = installed_distribution()
    errors: list[str] = []
    if declared != installed:
        errors.append(
            "installed worktrail console-script metadata is stale: "
            f"missing={sorted(declared - installed)} extra={sorted(installed - declared)}"
        )

    package_version = checkout_version(repo)
    if installed_version != package_version:
        errors.append(
            "installed worktrail version differs from pyproject.toml: "
            f"installed={installed_version!r} package={package_version!r}"
        )
    plugin_manifest = json.loads(
        (repo / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    plugin_version = plugin_manifest.get("version")
    if plugin_version != package_version:
        errors.append(
            "Codex plugin manifest version differs from pyproject.toml: "
            f"plugin={plugin_version!r} package={package_version!r}"
        )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        errors = check(args.repo)
    except (OSError, ValueError, md.PackageNotFoundError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
    if errors:
        for error in errors:
            print(f"PACKAGING METADATA: FAIL — {error}", file=sys.stderr)
        return 1
    print(
        "PACKAGING METADATA: PASS — checkout, installed scripts, and Codex plugin version agree"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
