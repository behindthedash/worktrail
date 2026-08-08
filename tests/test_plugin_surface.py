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
HOOKS_JSON = REPO_ROOT / "hooks" / "hooks.json"

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


def test_stop_hook_is_registered_and_points_inside_worktrail():
    hooks = json.loads(HOOKS_JSON.read_text())
    stop_hooks = hooks["hooks"]["Stop"]
    command = stop_hooks[0]["hooks"][0]["command"]
    assert "suggest_next_step.py" in command
    assert "developer-kit" not in command
    assert (REPO_ROOT / "hooks" / "suggest_next_step.py").is_file()


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


def test_cross_skill_anchor_citations_resolve():
    """`../worktrail-go/references/subagent-prompts.md#anchor` links between skills
    must land on a heading that actually defines that anchor.

    This is the guard that did not exist while `sdd-workflow` lived in
    `developer-kit-specs`: it cited 24 anchors in a file owned by another repo, and
    a rename on either side was a silent runtime dead-end that no test could see.
    Now that both sides ship from here, a broken anchor fails the build.
    """
    defined: dict[Path, set[str]] = {}
    for doc in _skill_docs():
        defined[doc] = set(re.findall(r"\{#([a-z0-9-]+)\}", doc.read_text()))

    broken = []
    for doc in _skill_docs():
        for rel, anchor in re.findall(r"`((?:\.\./)[A-Za-z0-9_./-]+\.md)#([a-z0-9-]+)`", doc.read_text()):
            target = (doc.parent / rel).resolve()
            if not target.is_file():
                broken.append(f"{doc.relative_to(REPO_ROOT)} -> {rel} (file missing)")
            elif anchor not in defined.get(target, set()):
                broken.append(f"{doc.relative_to(REPO_ROOT)} -> {rel}#{anchor} (anchor undefined)")
    assert not broken, f"broken cross-skill anchor citations: {broken}"


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


def test_no_devkit_specs_dispatches_remain():
    """The authoring layer dispatches to OpenSpec's `/opsx:*` commands now.

    A skill naming `specs.change-spec` / `specs.spec-to-tasks` / `specs.spec-check` /
    `specs.sync` would send an agent into a plugin that is being uninstalled — a
    runtime dead-end nothing else in this suite can see, because the target lives
    (lived) in another repo entirely.
    """
    retired = ("specs.change-spec", "specs.spec-to-tasks", "specs.spec-check", "specs.sync")
    offenders = []
    for doc in _skill_docs():
        text = doc.read_text()
        for name in retired:
            if name in text:
                offenders.append(f"{doc.relative_to(REPO_ROOT)}: {name}")
    assert not offenders, f"dispatches to retired developer-kit-specs skills: {offenders}"


def test_go_dispatches_worktrail_executor_only():
    """The front door must resolve to the executor shipped by this plugin."""
    go_docs = [doc for doc in _skill_docs() if doc.is_relative_to(SKILLS_DIR / "worktrail-go")]
    offenders = []
    for doc in go_docs:
        for needle in ("specs-sdd-workflow", "developer-kit-specs:"):
            if needle in doc.read_text():
                offenders.append(f"{doc.relative_to(REPO_ROOT)}: {needle}")
    assert not offenders, f"worktrail-go retains retired cross-plugin dispatches: {offenders}"


def test_opsx_apply_is_never_dispatched():
    """worktrail replaces OpenSpec's own sequential executor. Dispatching
    `/opsx:apply` would run the change twice — once sequentially by OpenSpec, once
    fanned out by the orchestrator."""
    offenders = []
    for doc in _skill_docs():
        for i, line in enumerate(doc.read_text().splitlines(), 1):
            if "/opsx:apply" not in line:
                continue
            # A mention is fine only when the same line says not to use it.
            if "not" not in line.lower():
                offenders.append(f"{doc.relative_to(REPO_ROOT)}:{i}: {line.strip()[:60]}")
    assert not offenders, f"skills dispatch /opsx:apply: {offenders}"


def test_opencode_bridge_exposes_worktrail_commands_without_devkit_paths():
    bridge = REPO_ROOT / ".opencode" / "plugins" / "worktrail.js"
    text = bridge.read_text()
    for command in ("worktrail.go", "worktrail.handoff", "worktrail.spec-create"):
        assert command in text
    assert "drain" in text.split('input.command["worktrail.go"]', 1)[1].split('input.command["worktrail.handoff"]', 1)[0]
    assert "developer-kit" not in text
    assert "developer_kit" not in text


def test_opencode_bridge_exposes_session_end_parity():
    bridge = REPO_ROOT / ".opencode" / "plugins" / "worktrail.js"
    text = bridge.read_text()
    assert '"session.idle"' in text
    assert '"session.compacted"' in text
    assert "worktrail-handoff" in text


def test_spec_worktree_setup_does_not_number_openspec_change_ids():
    """`openspec new change` rejects any name not starting with a letter
    (verified against OpenSpec 1.6.0: "Error: Change name must start with a
    letter"). The devkit-only NNN-prefix convention must not reach the
    openspec SPEC_ID, or every openspec-format `new`/Route-C dispatch fails.
    Reproduced directly 2026-08-06 (brief 20260806-201014)."""
    doc = SKILLS_DIR / "worktrail-go" / "references" / "subagent-prompts.md"
    text = doc.read_text()
    heading = "### Spec worktree setup {#spec-worktree-setup}"
    start = text.index(heading)
    block_start = text.index("```bash", start)
    block_end = text.index("```", block_start + len("```bash"))
    block = text[block_start:block_end]

    assert '"$FORMAT" = "devkit"' in block, (
        "spec-worktree-setup must branch the id convention on $FORMAT; "
        "an unconditional NNN-prefixed SPEC_ID breaks every openspec-format dispatch"
    )
    devkit_branch, _, non_devkit_branch = block.partition('else')
    assert 'SPEC_ID="$NNN-$slug"' in devkit_branch
    assert "NNN=" not in non_devkit_branch and "$NNN-$slug" not in non_devkit_branch, (
        "the non-devkit (openspec) branch must not carry the numeric NNN prefix "
        "into SPEC_ID -- `openspec new change` rejects a name that doesn't start "
        "with a letter"
    )


def test_orchestrator_invocation_branches_spec_ref_on_detected_format():
    """`worktrail-live full-real --spec` must resolve to the tree that actually
    owns $SPEC_ID's tasks. `taskformats.resolve.detect_format()` decides format
    purely from the path it is handed (docs/specs/** vs openspec/changes/**), so
    a hardcoded `docs/specs/$SPEC_ID` here always resolves as devkit regardless
    of the target repo's real format -- reproduced directly: running this exact
    invocation against an OpenSpec change raised FileNotFoundError ('no task
    files under .../docs/specs/<id>/tasks') because it silently tried to load
    the change as devkit. Brief 20260807-194634."""
    doc = SKILLS_DIR / "worktrail-go" / "references" / "subagent-prompts.md"
    text = doc.read_text()
    heading = "## Orchestrator invocation {#orchestrator}"
    start = text.index(heading)
    block_start = text.index("```bash", start)
    block_end = text.index("```", block_start + len("```bash"))
    block = text[block_start:block_end]

    assert '$SPEC_ROOT/openspec/changes/$SPEC_ID"' in block
    if_start = block.index('if [ -d "$SPEC_ROOT/openspec/changes/$SPEC_ID" ]; then')
    fi_end = block.index("\nfi\n", if_start) + len("\nfi\n")
    format_switch = block[if_start:fi_end]
    after_switch = block[fi_end:]

    openspec_branch, _, devkit_branch = format_switch.partition("else")
    assert 'SPEC_REF="openspec/changes/$SPEC_ID"' in openspec_branch
    assert 'SPEC_REF="docs/specs/$SPEC_ID"' in devkit_branch
    assert "worktrail-live full-real" in after_switch
    assert '--spec "$SPEC_REF"' in after_switch, (
        "the full-real invocation must pass the format-resolved $SPEC_REF, not a "
        "literal docs/specs/$SPEC_ID -- otherwise every OpenSpec-format repo's "
        "Route C-continuing-inline-D and Route D dispatch loads the wrong adapter"
    )
    assert "--spec docs/specs/$SPEC_ID" not in block
