"""The Claude Code plugin surface must stay in lockstep with the package.

The skills under `skills/` document this package's own console scripts. When they
lived in `developer-kit` they drifted from the code they described — the installed
plugin cache lagged the source fork by whole features, and the SKILL.md files
carried a `find`-based resolution fallback specifically to cope with that. Shipping
them from this repo removes the distance; these tests keep it removed.
"""

from __future__ import annotations

import importlib.metadata as md
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = REPO_ROOT / ".claude-plugin" / "marketplace.json"

COMMAND_RE = re.compile(r"\bworktrail-[a-z0-9-]+")

# The shell form that actually resolves a plugin path. Prose may legitimately
# name the variable to state that it is no longer used, so match the expansion,
# not the bare word.
PLUGIN_PATH_PATTERNS = (
    "${CLAUDE_PLUGIN_ROOT",
    "scripts/work_queue.py",
    "scripts/classify.py",
    "find \"$HOME/.claude/plugins\"",
)


def _console_scripts() -> set[str]:
    return {ep.name for ep in md.distribution("worktrail").entry_points}


def _skill_docs() -> list[Path]:
    return sorted(SKILLS_DIR.rglob("*.md"))


def test_every_referenced_command_is_a_real_console_script():
    """A SKILL.md naming a command that no longer exists sends the agent down a
    dead path at runtime; nothing else in the suite would catch it."""
    # Skill names share the worktrail- prefix but are not console scripts.
    known = _console_scripts() | {d.name for d in SKILLS_DIR.iterdir() if d.is_dir()}
    unresolved: dict[str, list[str]] = {}
    for doc in _skill_docs():
        for name in COMMAND_RE.findall(doc.read_text()):
            if name not in known:
                unresolved.setdefault(name, []).append(str(doc.relative_to(REPO_ROOT)))
    assert not unresolved, f"skill docs reference non-existent console scripts: {unresolved}"


def test_no_plugin_path_resolution_leaked_in():
    """Console scripts are on PATH. Any reappearance of the old
    `$CLAUDE_PLUGIN_ROOT` / `find ~/.claude/plugins` resolution chain means a doc
    was copied back from the pre-extraction source."""
    offenders = []
    for doc in _skill_docs():
        text = doc.read_text()
        for pattern in PLUGIN_PATH_PATTERNS:
            if pattern in text:
                offenders.append(f"{doc.relative_to(REPO_ROOT)}: {pattern}")
    assert not offenders, f"stale plugin-path resolution found: {offenders}"


def test_plugin_manifest_lists_exactly_the_skills_on_disk():
    declared = {Path(p).name for p in json.loads(PLUGIN_JSON.read_text())["skills"]}
    on_disk = {d.name for d in SKILLS_DIR.iterdir() if d.is_dir()}
    assert declared == on_disk, (
        "Claude Code does not auto-discover skills from disk — plugin.json's "
        f"skills array is hand-maintained. declared={sorted(declared)} "
        f"on_disk={sorted(on_disk)}"
    )


def test_every_skill_has_a_frontmatter_name_and_description():
    for skill_dir in sorted(d for d in SKILLS_DIR.iterdir() if d.is_dir()):
        skill_md = skill_dir / "SKILL.md"
        assert skill_md.is_file(), f"{skill_dir.name} has no SKILL.md"
        text = skill_md.read_text()
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        assert m, f"{skill_dir.name}/SKILL.md has no YAML frontmatter"
        fm = m.group(1)
        name = re.search(r"^name:\s*(\S+)", fm, re.M)
        assert name, f"{skill_dir.name}/SKILL.md frontmatter has no name:"
        assert re.fullmatch(r"[a-z0-9-]+", name.group(1)), (
            f"{skill_dir.name}: skill name {name.group(1)!r} must be kebab-case — a dot "
            "silently drops the description and makes the skill untriggerable"
        )
        assert name.group(1) == skill_dir.name, (
            f"{skill_dir.name}: frontmatter name {name.group(1)!r} must match its directory"
        )
        assert re.search(r"^description:\s*\S", fm, re.M), (
            f"{skill_dir.name}/SKILL.md frontmatter has no description:"
        )


@pytest.mark.parametrize("manifest", [PLUGIN_JSON, MARKETPLACE_JSON])
def test_manifests_are_valid_json(manifest: Path):
    json.loads(manifest.read_text())


def test_referenced_reference_files_exist():
    """`references/foo.md` cross-links are load-bearing: the SKILL.md tells the
    agent to open them mid-procedure."""
    missing = []
    for doc in _skill_docs():
        skill_root = doc.parent if doc.parent.name != "references" else doc.parent.parent
        for ref in re.findall(r"`references/([a-z0-9-]+\.md)`", doc.read_text()):
            if not (skill_root / "references" / ref).is_file():
                missing.append(f"{doc.relative_to(REPO_ROOT)} -> references/{ref}")
    assert not missing, f"dangling reference links: {missing}"


def test_worktrail_doc_links_from_skills_resolve():
    """Skills cite this repo's own docs (e.g. the GO design records under
    `docs/design/history/`) that deliberately live outside the skill bundle —
    they are history, not procedure, and shipping them as skill context would
    put ~480 lines of non-procedural text in front of the agent. The citations
    still have to point at something.

    Scoped to `docs/design/` on purpose: skill docs also cite paths in the
    *target* repo an agent operates on (`docs/specs/**` — the devkit spec
    convention), which by definition do not exist here.
    """
    missing = []
    for doc in _skill_docs():
        for path in re.findall(r"`(docs/design/[A-Za-z0-9_/-]+\.md)`", doc.read_text()):
            if not (REPO_ROOT / path).is_file():
                missing.append(f"{doc.relative_to(REPO_ROOT)} -> {path}")
    assert not missing, f"skill docs cite missing worktrail docs: {missing}"
