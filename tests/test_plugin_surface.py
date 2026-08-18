"""The Claude Code plugin surface must stay in lockstep with the package.

The skills under `skills/` document this package's own console scripts. When they
lived in `developer-kit` they drifted from the code they described — the installed
plugin cache lagged the source fork by whole features, and the SKILL.md files
carried a `find`-based resolution fallback specifically to cope with that. Shipping
them from this repo removes the distance; these tests keep it removed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.check_packaging_metadata import checkout_console_scripts

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
    # Source validation must be checkout-relative. The active editable install
    # can legitimately point at the canonical checkout while this test runs in
    # a task worktree containing a newly declared command.
    return checkout_console_scripts(REPO_ROOT)


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


def test_handoff_dispatch_includes_explicit_executor_route():
    """The SDD executor's direct-invocation guard requires route:X even for handoffs."""
    go_skill = SKILLS_DIR / "worktrail-go" / "SKILL.md"
    text = go_skill.read_text()
    assert 'Skill("worktrail-sdd-workflow", args="handoff:<id> route:<X> by:<dispatch-id>")' in text
    assert 'Skill("worktrail-sdd-workflow", args="handoff:<id>")' not in text


def test_adapter_dispatch_seeds_the_parent_run_record():
    """The adapter must send the resolved seed, not the raw handoff args.

    A raw ``handoff:<id> route:<X>`` child takes handoff-seed mode and starts a
    second run record.  Pin the front-door procedure to the same seeded-dispatch
    contract exercised end-to-end by ``test_internal_dispatch_lifecycle.py``.
    """
    text = (SKILLS_DIR / "worktrail-go" / "SKILL.md").read_text()
    start = text.index("**Adapter dispatch (`dispatch_mode: adapter`).**")
    end = text.index("**Provider-capacity gate:**", start)
    adapter = text[start:end]

    assert "worktrail-go-seed" in adapter
    assert '--run "$RUN"' in adapter
    assert '--brief "$BRIEF_PATH"' in adapter
    assert '--args "$SEED"' in adapter


def test_executor_guard_distinguishes_adapter_entry_from_direct_invocation():
    text = (SKILLS_DIR / "worktrail-sdd-workflow" / "SKILL.md").read_text()
    assert "[WORKTRAIL INTERNAL DISPATCH]" in text
    assert "Do not redirect to `/go`" in text
    assert "IF no `route:X` positional arg is present" in text
    assert "sdd-workflow is an internal executor. Use /go for all engineering work." in text


def test_go_dispatch_mode_table_pins_invocation_context_constants():
    """Phase 7 of worktrail-go/SKILL.md branches on the `dispatch_mode` values
    `invocation_context.resolve()` emits. A table row naming a mode the resolver
    never produces — or a missing row for one it does — sends the agent down a
    dead dispatch path at runtime, the same drift class as a SKILL.md naming a
    retired console script. The table must list exactly the resolver's
    `DISPATCH_MODES`, in the decision tree's order."""
    from worktrail.router import invocation_context

    text = (SKILLS_DIR / "worktrail-go" / "SKILL.md").read_text()
    header = "| `dispatch_mode` | action |"
    assert header in text, "worktrail-go/SKILL.md lost its dispatch_mode table"

    lines = text[text.index(header):].splitlines()
    assert re.fullmatch(r"\|[-\s|]+\|", lines[1].strip()), (
        f"expected a markdown separator row after the table header, got: {lines[1]!r}"
    )
    modes = []
    for line in lines[2:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break
        cell = re.match(r"\|\s*`([a-z-]+)`\s*\|", stripped)
        assert cell, f"unparseable dispatch_mode row: {stripped!r}"
        modes.append(cell.group(1))

    assert tuple(modes) == invocation_context.DISPATCH_MODES, (
        "worktrail-go/SKILL.md's dispatch_mode table has drifted from "
        f"invocation_context.DISPATCH_MODES: table={modes} "
        f"resolver={list(invocation_context.DISPATCH_MODES)}"
    )


def test_active_run_resume_stays_in_session_never_spawns_nested_worker():
    """An interactive parent resuming its own already-active run (no final_status +
    existing worktree, Route E) must hand execution back to the active parent and continue
    in-session — it must NOT spawn a nested worker that polls the same run/worktree/provider
    (Datalena run go-20260811-132806). Headless drain one-shots are never an active-run
    resume and keep spawning. This pins the prose guard both in the go front door's Phase 7
    dispatch and in the subagent-prompts in-session contract."""
    go_skill = (SKILLS_DIR / "worktrail-go" / "SKILL.md").read_text()
    subagent = (SKILLS_DIR / "worktrail-go" / "references" / "subagent-prompts.md").read_text()

    # Both docs must state the active-run resume stays in-session / hands back to the parent,
    # and both must name the run go-20260811-132806 that first surfaced the self-poll loop.
    for doc_name, text in (("worktrail-go/SKILL.md", go_skill),
                           ("worktrail-go/references/subagent-prompts.md", subagent)):
        assert "already active" in text, f"{doc_name} lacks the active-run-resume guard"
        assert "go-20260811-132806" in text, (
            f"{doc_name} no longer cites the run that first surfaced the self-poll loop")
        assert "final_status" in text and "worktree" in text, (
            f"{doc_name} must define what makes a run 'already active' "
            "(no final_status + existing worktree)")

    # The go front door must not send an active-run resume through the Native Skill adapter.
    assert "do not use the adapter for an active-run resume" in go_skill.lower()

    # Headless drain is explicitly preserved: it is never an active-run resume.
    for doc_name, text in (("worktrail-go/SKILL.md", go_skill),
                           ("worktrail-go/references/subagent-prompts.md", subagent)):
        assert "drain" in text, f"{doc_name} must keep stating headless drain still spawns"


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


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_opencode_bridge_registers_openspec_propose_deterministically():
    """worktrail-skill-dispatch always sends `--skill openspec-propose`, which
    skill_dispatch.py's `_prompt` turns into `/openspec-propose <args>` for opencode.
    Without a matching `input.command["openspec-propose"]` entry, that command name
    resolves only if the model happens to glob-search for and read
    skills/openspec-propose/SKILL.md itself — verified live 2026-08-09 (PR #264
    follow-up) to work only by luck, not by design. The bridge must register the
    command and embed the skill's real procedure so resolution never depends on
    model self-discovery."""
    bridge = REPO_ROOT / ".opencode" / "plugins" / "worktrail.js"
    text = bridge.read_text()
    assert '"openspec-propose"' in text
    assert "readFileSync" in text, "must read the skill body rather than pointing at a path"

    script = f"""
    import({json.dumps(bridge.as_uri())}).then(async (m) => {{
      const plugin = await m.Worktrail({{ directory: {json.dumps(str(REPO_ROOT))} }})
      const input = {{}}
      await plugin.config(input)
      const cmd = input.command["openspec-propose"]
      if (!cmd) {{ console.error("MISSING"); process.exit(1) }}
      process.stdout.write(cmd.template)
    }})
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    template = result.stdout
    # The template must carry the real propose procedure, not a bare pointer.
    assert "openspec new change" in template
    assert "openspec instructions" in template
    assert "$ARGUMENTS" in template


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


def test_modify_pipeline_runs_scope_check_before_uncommitted_output_guard():
    """The `modify` pipeline's pre-launch uncommitted-output guard only ever
    catches files present -- and uncommitted -- at the moment it runs. Before
    this fix, `worktrail-compile` (which writes `.compile-ok`) only ran inside
    `#orchestrator`, strictly *after* this guard already passed, so the marker
    was always missing from the committed change and `CI: Scope check`
    (`worktrail-check-compile-markers`) failed deterministically on every
    modify-pipeline group PR. Reproduced directly on worktrail PR #260 (run
    go-20260809-021940): the marker had to be hand-committed onto the group
    branch as a `ci_repair` intervention. The fix is ordering: a `worktrail-
    compile` scope-check step must run *before* the guard so the guard itself
    catches the freshly-written, uncommitted marker."""
    doc = SKILLS_DIR / "worktrail-sdd-workflow" / "references" / "pipeline-details.md"
    text = doc.read_text()
    heading = "## `modify` pipeline {#modify-pipeline}"
    section = text[text.index(heading):]

    compile_call_pos = section.index('worktrail-compile "$CHANGE_DIR"')
    guard_heading_pos = section.index("**Pre-launch uncommitted-output guard (mandatory)**")
    guard_block_pos = section.index('CHG_DIFF=$(git -C "$WT" status --porcelain -- openspec/')

    assert compile_call_pos < guard_heading_pos < guard_block_pos, (
        "worktrail-compile must run before the pre-launch uncommitted-output "
        "guard in the modify pipeline -- otherwise .compile-ok is always "
        "written after the guard's commit checkpoint and every group PR fails "
        "CI: Scope check"
    )


def _h2_sections(text: str) -> dict[str, str]:
    """Split a markdown body into h2-delimited sections, keyed by heading line.

    YAML frontmatter is stripped first so an `allowed-tools:` mention of a tool
    doesn't count as a call site. Text before the first `## ` heading lands in a
    `"(preamble)"` pseudo-section.
    """
    if text.startswith("---"):
        end = text.index("\n---", 3)
        text = text[end + len("\n---"):]
    sections: dict[str, str] = {}
    current = "(preamble)"
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            sections[current] = "\n".join(lines)
            current, lines = line, []
        else:
            lines.append(line)
    sections[current] = "\n".join(lines)
    return sections


def test_route_execution_ask_sites_carry_auto_mode_fallbacks():
    """PR #290 gave go's three Phase 5.5 asks an $AUTO_MODE=true fallback
    (AskUserQuestion is not a registered tool in the headless one-shots
    `worktrail-go drain` spawns), but sdd-workflow's route execution still
    assumed an interactive parent one layer deeper — the "Interactive routes
    stay in-session" doctrine dispatched Route C/I in-session on the premise a
    human could answer mid-flow asks. Brief 20260810-114713. This pins:

    1. the doctrine no longer asserts an interactive parent is always present;
    2. a shared route-execution fallback contract exists and prescribes PR
       #290's pattern (blocked_product_decision, brief left claimed in picked/);
    3. structurally, every h2 section of the route-execution surface that
       mentions AskUserQuestion also documents an AUTO_MODE branch — so a
       future edit can't reintroduce an unconditional ask without tripping here;
    4. the prose ask sites (routes.md §A/§C, the implement-pipeline spec pick,
       Route C closeout) carry their documented defaults.
    """
    subagent = (SKILLS_DIR / "worktrail-go" / "references" / "subagent-prompts.md").read_text()
    routes = (SKILLS_DIR / "worktrail-go" / "references" / "routes.md").read_text()
    automode = (SKILLS_DIR / "worktrail-go" / "references" / "auto-mode.md").read_text()
    pipeline_doc = SKILLS_DIR / "worktrail-sdd-workflow" / "references" / "pipeline-details.md"
    sdd_skill_doc = SKILLS_DIR / "worktrail-sdd-workflow" / "SKILL.md"

    # 1. Doctrine: the unconditional interactive-parent premise is gone.
    assert "Routes that need `AskUserQuestion` mid-flow (C, I)" not in subagent, (
        "the 'Interactive routes stay in-session' doctrine reverted to asserting "
        "an interactive parent is always present")
    assert "In-session does not mean interactive" in subagent

    # 2. Shared contract, consistent with PR #290's Phase 5.5 pattern.
    assert "{#auto-mode-ask-fallbacks}" in subagent
    fallbacks = _h2_sections(subagent)[
        "## Auto-mode ask fallbacks (route execution) {#auto-mode-ask-fallbacks}"]
    assert "blocked_product_decision" in fallbacks
    assert "picked/" in fallbacks, "the contract must leave the brief claimed, like PR #290"
    assert "work_queue.py done" in fallbacks, (
        "the contract must forbid closing the brief on an unattended block")

    # 3. Structural invariant over the route-execution surface.
    surface = {
        "worktrail-go/references/subagent-prompts.md": subagent,
        "worktrail-sdd-workflow/references/pipeline-details.md": pipeline_doc.read_text(),
        "worktrail-sdd-workflow/SKILL.md": sdd_skill_doc.read_text(),
    }
    offenders = []
    for name, text in surface.items():
        for heading, body in _h2_sections(text).items():
            if "AskUserQuestion" in body and "AUTO_MODE" not in body:
                offenders.append(f"{name} :: {heading}")
    assert not offenders, (
        "route-execution sections mention AskUserQuestion with no AUTO_MODE "
        f"fallback documented in the same section: {offenders}")

    # 4. Prose ask sites (no AskUserQuestion literal) carry their defaults.
    route_a = routes[routes.index("## Route A"):routes.index("## Route B")]
    assert "$AUTO_MODE=true" in route_a and "investigation_complete" in route_a
    route_c = routes[routes.index("## Route C"):routes.index("## Route D")]
    assert "$AUTO_MODE=true" in route_c and "planning-only default" in route_c
    implement = _h2_sections(surface["worktrail-sdd-workflow/references/pipeline-details.md"])[
        "## `implement` pipeline {#implement-pipeline}"]
    assert "$AUTO_MODE=true" in implement and "blocked_product_decision" in implement
    assert "planning-only default" in surface["worktrail-sdd-workflow/SKILL.md"]

    # auto-mode.md indexes the route-execution fallbacks alongside Phase 5.5's.
    assert "auto-mode-ask-fallbacks" in automode


def test_phase55_auto_mode_blocks_actually_file_a_decision():
    """Handoff 20260813-104029: `brief-staleness-check.md`'s (and its two
    siblings') `$AUTO_MODE=true` code block went straight from `run-record
    start` to `run-record finish --status blocked_product_decision`, even
    though the prose immediately below the block says to file a decision via
    `decision-queue.md#file-a-decision` and release the brief *before* that
    `finish`. An agent that pattern-matches the shown snippet (as the run
    that produced brief `20260813-103458`'s stranding did -- its recorded
    `merge_result` is a near-verbatim copy of this block's placeholder text)
    skips the decision-queue step entirely and leaves the brief stuck in
    `picked/` instead of released `awaiting-decision`, defeating
    `human-decision-queue`'s own "punishes decision-less blocks" contract.
    Each auto-mode code block must call `worktrail-decision ask` (with
    `--release`) before `run-record finish`, not just say so in prose.
    """
    guard_dir = SKILLS_DIR / "worktrail-go" / "references"
    targets = [
        guard_dir / "brief-staleness-check.md",
        guard_dir / "spec-collision-check.md",
        guard_dir / "related-brief-collision-check.md",
    ]
    block_re = re.compile(r"```bash\n(.*?)```", re.DOTALL)

    for path in targets:
        text = path.read_text()
        auto_blocks = [
            b for b in block_re.findall(text) if "blocked_product_decision" in b
        ]
        assert auto_blocks, f"{path.name}: no auto-mode blocked_product_decision code block found"
        for block in auto_blocks:
            assert "worktrail-decision ask" in block, (
                f"{path.name}: auto-mode block finishes blocked_product_decision without "
                "calling `worktrail-decision ask` first -- the brief will be stranded in "
                "picked/ instead of released awaiting-decision")
            assert "--release" in block, (
                f"{path.name}: `worktrail-decision ask` call is missing --release")
            assert block.index("worktrail-decision ask") < block.index("run-record finish"), (
                f"{path.name}: `worktrail-decision ask` must run before `run-record finish`")
