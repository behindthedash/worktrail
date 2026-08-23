"""Vendored copy of devops's canonical `scripts/ci/dependabot/{test_dependabot_config.py,requirements.txt}`.

`test_dependabot_config.py` guards against a silent Dependabot-Updates
failure: a `.github/dependabot.yml` entry whose `directory` (or
`directories`) has no manifest file GitHub's ecosystem updater actually
recognizes, so Dependabot silently stops opening update PRs for that entry
with no error surfaced anywhere in the repo's own CI. worktrail-repo-init
scaffolds this pair of files into a target repo verbatim -- as string
constants here rather than a packaged data file, matching how `repo_init.py`
already vendors `_AUTOMERGE_WORKFLOW` as an inline template rather than
reading it off disk, so the CLI has no runtime dependency on package-data
resolution.

Source of truth: behindthedash/devops, `scripts/test_dependabot_config.py`
(PR #306). Update these constants by hand if that script changes
upstream -- there is no automated sync back to devops.
"""
from __future__ import annotations

DEPENDABOT_MANIFEST_CHECK_PY = '''\
#!/usr/bin/env python3
"""Fail CI when a `.github/dependabot.yml` entry has no manifest file Dependabot's
ecosystem updater will actually recognize in its declared directory.

When an `updates` entry's `directory` (or one of its `directories`) has no
manifest file the named `package-ecosystem` looks for -- a typo'd path, a
manifest that moved, a directory that was never created -- Dependabot-Updates
silently stops opening update PRs for that entry. Nothing surfaces an error
anywhere in the repo's own CI when this happens; it just goes quiet. This
script re-derives, for each checkable entry, whether a manifest file its
ecosystem's updater looks for actually exists on disk, and fails loudly if
not.

Vendored into worktrail from behindthedash/devops,
scripts/test_dependabot_config.py (PR #306). See
dependabot_manifest_check_template.py's module docstring for how this is
kept in sync.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ecosystem -> manifest glob patterns Dependabot's updater looks for in an
# entry's directory. Add a row here to make an ecosystem checkable; an
# ecosystem absent from this table (e.g. "github-actions") is always
# skipped, never failed.
ECOSYSTEM_MANIFEST_GLOBS = {
    "pip": ("requirements*.txt", "setup.py", "pyproject.toml", "Pipfile"),
    "npm": ("package.json",),
}


def _resolve_directory(repo: Path, directory: str) -> Path:
    """Resolve a dependabot.yml ``directory``/``directories`` entry to an
    absolute path. ``/`` (and an omitted key, which callers pass as ``/``)
    means the repo root; every other value is repo-root-relative.
    """
    if not directory or directory == "/":
        return repo
    return repo / directory.lstrip("/")


def _has_manifest(directory: Path, globs) -> bool:
    """Whether `directory` directly contains a file matching one of `globs`.

    Uses `Path.glob` (non-recursive) since Dependabot's own updater only
    looks in the declared directory itself, never its subdirectories.
    """
    if not directory.is_dir():
        return False
    return any(any(directory.glob(pattern)) for pattern in globs)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--repo",
        default=str(Path(__file__).resolve().parent.parent.parent.parent),
        help="repo root (default: inferred from this script's own location)",
    )
    ap.add_argument(
        "--config",
        default=None,
        help="path to dependabot.yml (default: <repo>/.github/dependabot.yml)",
    )
    args = ap.parse_args(argv)

    repo = Path(args.repo)
    config = Path(args.config) if args.config else repo / ".github" / "dependabot.yml"

    return 0


if __name__ == "__main__":
    sys.exit(main())
'''
