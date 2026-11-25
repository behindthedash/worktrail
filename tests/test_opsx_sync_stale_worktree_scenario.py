"""Exercise the bundled sync preflight against a genuinely stale checkout."""

from __future__ import annotations

import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_SURFACES = (
    REPO_ROOT / "commands" / "opsx" / "sync.md",
    REPO_ROOT / "skills" / "openspec-sync-specs" / "SKILL.md",
)
CAPABILITY = "sync-fixture"
REQUIREMENT_NAME = "Remote requirement already applied"
SPEC_PATH = Path("openspec/specs") / CAPABILITY / "spec.md"
DELTA_BLOCK = """### Requirement: Remote requirement already applied
The system SHALL preserve the requirement from the target base.

#### Scenario: Remote base already contains the change
- **WHEN** sync is dispatched from an old checkout
- **THEN** sync reports already synced without editing the canonical spec."""


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _requirement_block(spec: str, requirement_name: str) -> str:
    lines = spec.splitlines()
    heading = f"### Requirement: {requirement_name}"
    start = lines.index(heading)
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("### Requirement: ")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def _preflight_section(surface: Path) -> str:
    text = surface.read_text()
    _, section = text.split("4. **Preflight against the remote base", 1)
    section, _ = section.split("5. **For each delta spec", 1)
    return section


def _preflight_script(section: str) -> str:
    match = re.search(
        r"(?ms)^ {3}```bash\n(?P<script>.*?)^ {3}```\s*$",
        section,
    )
    assert match, "sync instructions must keep the preflight shell block"
    return textwrap.dedent(match.group("script"))


def _sync_checkout(tmp_path: Path) -> tuple[Path, Path, str]:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    sync = tmp_path / "sync"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(origin))
    _git(tmp_path, "init", "--initial-branch=main", str(seed))
    _git(seed, "config", "user.name", "Sync Scenario")
    _git(seed, "config", "user.email", "sync-scenario@example.invalid")

    local_spec = seed / SPEC_PATH
    local_spec.parent.mkdir(parents=True)
    stale_content = (
        f"# {CAPABILITY} Specification\n\n## Requirements\n\n"
        "### Requirement: Existing requirement\n"
        "The local checkout has not received the remote requirement.\n"
    )
    local_spec.write_text(stale_content)
    _git(seed, "add", str(SPEC_PATH))
    _git(seed, "commit", "-m", "Add old canonical spec")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-u", "origin", "main")
    _git(tmp_path, "clone", str(origin), str(sync))

    remote_content = (
        f"# {CAPABILITY} Specification\n\n## Requirements\n\n{DELTA_BLOCK}  \n"
    )
    local_spec.write_text(remote_content)
    _git(seed, "add", str(SPEC_PATH))
    _git(seed, "commit", "-m", "Apply requirement on remote base")
    _git(seed, "push", "origin", "main")

    delta = sync / "openspec/changes/sync-fixture/specs" / CAPABILITY / "spec.md"
    delta.parent.mkdir(parents=True)
    delta.write_text(f"## ADDED Requirements\n\n{DELTA_BLOCK}\n")
    return sync, sync / SPEC_PATH, stale_content


@pytest.mark.parametrize("surface", SYNC_SURFACES, ids=lambda path: path.name)
def test_stale_sync_checkout_finds_remote_convergence_without_edits(
    tmp_path: Path, surface: Path
):
    sync, canonical_spec, stale_content = _sync_checkout(tmp_path)
    section = _preflight_section(surface)
    script = _preflight_script(section)
    script += '\nprintf "%s\\n" "$BASE_BRANCH"\n'
    before_status = _git(sync, "status", "--porcelain")
    stale_remote_ref = _git(sync, "rev-parse", "origin/main").strip()

    environment = os.environ.copy()
    environment.pop("BASE_BRANCH", None)
    environment["BASE"] = "main"
    result = subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=sync,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    base_branch = result.stdout.splitlines()[-1]
    assert stale_remote_ref != _git(sync, "rev-parse", "origin/main").strip()

    show = re.search(
        r'`git show\s+"origin/\$\{BASE_BRANCH\}:([^`"]+)"`',
        section,
        re.DOTALL,
    )
    assert show, "sync instructions must read canonical specs from origin/<base>"
    remote_path = show.group(1).replace("<capability>", CAPABILITY)
    remote_spec = _git(sync, "show", f"origin/{base_branch}:{remote_path}")
    delta_spec = (
        sync / "openspec/changes/sync-fixture/specs" / CAPABILITY / "spec.md"
    ).read_text()

    remote_requirement = _requirement_block(remote_spec, REQUIREMENT_NAME)
    delta_requirement = _requirement_block(delta_spec, REQUIREMENT_NAME)
    assert remote_requirement.rstrip() == delta_requirement.rstrip()
    assert REQUIREMENT_NAME not in canonical_spec.read_text()
    assert _git(sync, "status", "--porcelain") == before_status
    assert canonical_spec.read_text() == stale_content
