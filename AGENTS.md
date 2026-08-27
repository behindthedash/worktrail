# worktrail

Spec-format-agnostic task orchestration, extracted from the `developer-kit` Claude Code plugin
marketplace fork. Pip-installable Python package with console-script entrypoints — callable from
any harness (Claude Code, Codex, plain CLI, CI), not bound to one plugin runtime.

## What this repo is

Five subsystems under `src/worktrail/`:

- **`conductor/`** — the one context that reads a whole change. `compile.py` turns a spec/change
  into a **RunPlan** (per-task file scope + dependency edges), content-addressed and cached under
  `<repo>-worktrees/runplans/`, so a re-run or resume costs nothing. Formats that already declare
  file scope (devkit frontmatter) are seeded without a model; formats that do not (OpenSpec's
  `tasks.md`) get one inference pass. `runplan.py` owns the rule for applying a plan safely: an
  edge may only be dropped if both endpoints carry file scope, because `runnable_frontier` reads
  an empty file set as "collides with nothing".
- **`orchestrator/`** — parallel git-worktree fan-out task execution: dependency-aware task DAG
  planning (`coordinator.py`), worktree lifecycle (`worktree.py`), cold-start worker dispatch
  (`dispatch.py`), live headless-agent spawning with run-lock/journal/resilience (`live.py`,
  `spawnlib.py`), branch integration + PR creation (`integrate.py`), post-PR verify + cleanup
  (`verify.py`).
- **`taskformats/`** — the `TaskSource` adapter interface (`base.py`) plus `devkit/`, the
  reference implementation backed by the original `docs/specs/[id]/tasks/TASK-*.md` frontmatter
  contract (`FIELD_SCHEMA`, ported from `task_lifecycle.py`), and `openspec/`, backed by an
  OpenSpec change's single `tasks.md` checklist. Both plug in without `orchestrator/` knowing
  which is in use.
- **`workqueue/`** — the work-queue handoff system (`work_queue.py` claim/done/release, brief
  scoring, upstream-dependency watch). Storage root is `$WORK_QUEUE_DIR` (default `~/work-queue`),
  resolved at runtime — never inside this repo or any consuming repo.
- **`router/`** — the deterministic route classifier and resume dashboard that scan a target
  repo's spec tree and route free-text requests to the right workflow.
- **`drain/`** — unattended queue-draining: repeatedly spawns fresh-context headless one-shots
  against the router until the queue empties or a stop condition fires.

## Claude Code plugin surface

This repo is also a Claude Code plugin marketplace (`.claude-plugin/marketplace.json` +
`.claude-plugin/plugin.json`), shipping five user-facing skills under `skills/`:

| Skill | Surface over |
|---|---|
| `worktrail-help` | command reference for the host-specific Worktrail front door |
| `worktrail-handoff` | `workqueue/` — capture/claim/complete briefs in `$WORK_QUEUE_DIR` |
| `worktrail-go` | `router/` — the route classifier, orientation dashboard, run records, policy, and queue draining |
| `worktrail-sdd-workflow` | the route executor `worktrail-go` dispatches to (routes A–J) — internal, never called directly |
| `worktrail-repo-init` | `onboarding/repo_init.py` — bootstrap/migrate a repo onto the repo-standards doctrine (AGENTS.md split, branch model, rulesets, OpenSpec scaffold, auto-merge workflow, `.worktrail/policy.yaml`) |

It also bundles OpenSpec's own Claude Code integration (`commands/opsx/*.md` +
`skills/openspec-{propose,explore,update-change,sync-specs,archive-change}/`,
sourced from `openspec init --tools claude` — see https://github.com/Fission-AI/OpenSpec),
so `/opsx:propose` etc. are available to any session that installs this plugin, not only one
launched with the target repo as cwd (project-scoped `.claude/commands/` are never loaded via
`--add-dir`, and a workspace-rooted `/go` session is not launched from inside the target repo —
Fission-AI ships no plugin/marketplace of its own to depend on instead, so this is a deliberate
fork-and-adapt, not a second implementation). `/opsx:apply` and its backing `openspec-apply-change`
skill are deliberately **not** bundled: worktrail's own orchestrator replaces it, and shipping it
would let it be invoked directly and run a change twice (`test_opsx_apply_is_never_dispatched`
guards the skill *text* against dispatching it; not bundling it at all closes the same gap for
direct invocation). The bundled skill text is lightly edited from OpenSpec's generated output to
redirect its own "next step" suggestions to worktrail's pipeline instead of `/opsx:apply`, and to
fix a dangling `/opsx:continue` reference (that command does not exist in OpenSpec 1.6.0's
Claude Code integration; redirected to `/opsx:propose` against the same change name instead).
This repo's own specs use the OpenSpec format (`openspec/`, `openspec init`'d — see
`openspec/config.yaml`); `docs/specs/001-task-ac-verification-gate/` predates this and stays
devkit-format, since existing specs are always read by their on-disk format, never migrated.

Install with `/plugin marketplace add behindthedash/worktrail`, then `/plugin install worktrail`.
The plugin is a **thin surface**, not a second implementation: every command a SKILL.md issues is
a console script from this package's `[project.scripts]`, on `PATH` after `pip install worktrail`.
There is no script-path resolution, no `$CLAUDE_PLUGIN_ROOT` fallback, and no cross-plugin
lookup — those existed only because the skills used to live in a different repo from their engine.

**The package remains runtime-agnostic.** The plugin is one optional surface over it; the console
scripts stay callable from Codex, OpenCode, plain CLI, and CI without it.

`worktrail-sdd-workflow` and `worktrail-go` cite each other's `{#anchors}` across skill
directories. Those citations used to span two repos (the executor lived in
`developer-kit-specs` and cited 24 anchors in a file this repo owns), where a rename on either
side was a silent runtime dead-end no test could see. They are intra-repo links now, and
`test_cross_skill_anchor_citations_resolve` fails the build on a broken one.

The skill bundle carries **procedure only**. The GO v1/v2 design records are history and live
at `docs/design/history/` — a skill's `references/` are loaded as agent context, so non-procedural
archaeology does not belong there.

`tests/test_plugin_surface.py` enforces the lockstep: every `worktrail-*` command a skill doc
names must be a real entry point, `plugin.json`'s hand-maintained `skills` array must match the
directories on disk, frontmatter `name:` must be kebab-case and match its directory (a dot
silently drops the description and makes a skill untriggerable), `references/*.md` cross-links
must resolve, and the old plugin-path resolution patterns must not reappear.

## Origin and why it's separate

Extracted from `developer-kit` (behindthedash/developer-kit,
`plugins/developer-kit-specs/skills/specs-parallel-orchestrator`,
`plugins/developer-kit-specs/hooks/task_lifecycle.py`, and
`plugins/developer-kit-project-management/skills/{devkit-pm-go,devkit-pm-handoff,devkit-pm-drain}`)
because this code has no dependency on any one spec/template format or plugin runtime — it only
needs a source of task definitions with dependencies and a completion signal. `developer-kit`
consumes this package as a pinned dependency; its SKILL.md-driven scripts are thin shims that
call into the installed `worktrail` package so existing documented invocations keep working
unchanged.

## Development

```bash
./scripts/dev-install.sh   # pip install -e ".[dev]", refuses to run from a worktree
pytest
python3 -m worktrail.orchestrator.orchestrate check   # golden record/replay regression
```

Always install editable from the canonical checkout (`~/projects/worktrail`), never from a
task worktree — `pip install -e` records this checkout's absolute path, and deleting a
worktree (the standard teardown-after-merge step) then silently breaks every `worktrail-*`
console script with `ModuleNotFoundError: No module named 'worktrail'` until someone
manually diagnoses it (this broke the `/go` front door once, 2026-08-05).
`scripts/dev-install.sh` refuses to run from a linked worktree so this can't recur silently.

## Git workflow

Never commit or develop directly on `main`. Branch off `main` into a sibling worktree
(`git worktree add ../worktrail-worktrees/<branch> -b <branch> main`), open a PR, merge only
after CI is green. Delete merged branches once their PR lands. Never `pip install -e` from
that worktree (see Development above) — the editable install must always point at this
canonical checkout.

Merges to `main` propagate to this machine's local install surfaces (pip editable checkout,
Claude Code plugin cache, Codex plugin cache) automatically: a GitHub webhook relays the
merge event through pullhook to a local bridge sweep (`pullhook-bridge.py` in
`behindthedash/devops`, `*/2` cron) that runs `worktrail-plugin-refresh.sh` — typically
within ~2 minutes, with the pre-existing `*/5` refresh cron as fallback. A running Claude
Code session still needs a restart to pick up refreshed skill text.

## Versioning

Real semver in `pyproject.toml` (unlike `developer-kit`'s intentionally version-less/SHA-tracked
plugins) — this package is consumed as a pinned dependency, so version bumps are how consumers
pick up changes deliberately. Bump both `pyproject.toml` and `.codex-plugin/plugin.json` together
in a standalone `chore: bump Worktrail to X.Y.Z` commit (see `0b62a12`, `#75`) — not bundled into
a feature/fix PR.

`CI: Release Metadata Check` (`.github/workflows/release_metadata_check.yml`) enforces this, but
only for PRs that declare release intent by actually changing `pyproject.toml`'s `version` line —
an ordinary feature/fix PR passes unconditionally, no label required. A PR that does bump the
version is validated: the new value must be valid semver, greater than the base branch's version,
and `.codex-plugin/plugin.json`'s version must match it.
