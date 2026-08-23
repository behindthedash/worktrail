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

import yaml

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


_GLOB_CHARS = frozenset("*?[")


def _directory_values(entry: dict):
    """Yield the raw `directory`/`directories` value(s) declared on one
    `updates` entry, in declaration order.

    A dependabot.yml entry declares exactly one of the two keys; yielding
    `directory` as a single value keeps both shapes uniform for the caller.
    """
    if "directories" in entry:
        for directory in entry["directories"] or []:
            yield directory
    elif "directory" in entry:
        yield entry["directory"]


def iter_checkable_entries(updates):
    """Yield `(ecosystem, directory)` for every `updates` entry/directory
    pair that should be manifest-checked.

    Skips entries whose `package-ecosystem` is absent from
    `ECOSYSTEM_MANIFEST_GLOBS` (design D2), and skips any directory value
    containing a glob character (`*`, `?`, `[`) since Dependabot itself
    does not expand globs there and such a value cannot resolve to a single
    on-disk path (design D3). A `directories` entry is checked once per
    directory, independently of its siblings.
    """
    for entry in updates or []:
        ecosystem = entry.get("package-ecosystem")
        if ecosystem not in ECOSYSTEM_MANIFEST_GLOBS:
            continue
        for directory in _directory_values(entry):
            if not isinstance(directory, str) or any(c in directory for c in _GLOB_CHARS):
                continue
            yield ecosystem, directory


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

    if not config.is_file():
        print(f"nothing to check: no {config} in this repo")
        return 0

    try:
        data = yaml.safe_load(config.read_text())
    except yaml.YAMLError as exc:
        print(f"could not parse {config}: {exc}", file=sys.stderr)
        return 1

    updates = (data or {}).get("updates")
    if not updates:
        print(f"nothing to check: {config} declares no updates")
        return 0

    broken = []
    checked = 0
    for ecosystem, directory in iter_checkable_entries(updates):
        checked += 1
        resolved = _resolve_directory(repo, directory)
        if not _has_manifest(resolved, ECOSYSTEM_MANIFEST_GLOBS[ecosystem]):
            broken.append((ecosystem, directory))

    if broken:
        for ecosystem, directory in broken:
            print(
                f"no manifest for ecosystem={ecosystem!r} directory={directory!r}",
                file=sys.stderr,
            )
        return 1

    print(f"in sync: {checked} checkable updates entr{'y' if checked == 1 else 'ies'} have a manifest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

DEPENDABOT_MANIFEST_CHECK_REQUIREMENTS_TXT = '''\
pyyaml>=6.0
'''
