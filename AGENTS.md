# worktrail

Spec-format-agnostic task orchestration, extracted from the `developer-kit` Claude Code plugin
marketplace fork. Pip-installable Python package with console-script entrypoints — callable from
any harness (Claude Code, Codex, plain CLI, CI), not bound to one plugin runtime.

## What this repo is

Four subsystems under `src/worktrail/`:

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
`.claude-plugin/plugin.json`), shipping three skills under `skills/`:

| Skill | Surface over |
|---|---|
| `worktrail-go` | `router/` — the route classifier, orientation dashboard, run records, policy |
| `worktrail-handoff` | `workqueue/` — capture/claim/complete briefs in `$WORK_QUEUE_DIR` |
| `worktrail-drain` | `drain/` — unattended queue draining |

Install with `/plugin marketplace add behindthedash/worktrail`, then `/plugin install worktrail`.
The plugin is a **thin surface**, not a second implementation: every command a SKILL.md issues is
a console script from this package's `[project.scripts]`, on `PATH` after `pip install worktrail`.
There is no script-path resolution, no `$CLAUDE_PLUGIN_ROOT` fallback, and no cross-plugin
lookup — those existed only because the skills used to live in a different repo from their engine.

**The package remains runtime-agnostic.** The plugin is one optional surface over it; the console
scripts stay callable from Codex, OpenCode, plain CLI, and CI without it.

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
pip install -e ".[dev]"
pytest
python3 -m worktrail.orchestrator.orchestrate check   # golden record/replay regression
```

## Git workflow

Never commit or develop directly on `main`. Branch off `main` into a sibling worktree
(`git worktree add ../worktrail-worktrees/<branch> -b <branch> main`), open a PR, merge only
after CI is green. Delete merged branches once their PR lands.

## Versioning

Real semver in `pyproject.toml` (unlike `developer-kit`'s intentionally version-less/SHA-tracked
plugins) — this package is consumed as a pinned dependency, so version bumps are how consumers
pick up changes deliberately.
