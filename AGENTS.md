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
  contract (`FIELD_SCHEMA`, ported from `task_lifecycle.py`). Additional `TaskSource`
  implementations (e.g. an OpenSpec-backed one) are meant to plug in here without touching
  `orchestrator/`.
- **`workqueue/`** — the work-queue handoff system (`work_queue.py` claim/done/release, brief
  scoring, upstream-dependency watch). Storage root is `$WORK_QUEUE_DIR` (default `~/work-queue`),
  resolved at runtime — never inside this repo or any consuming repo.
- **`router/`** — the deterministic route classifier and resume dashboard that scan a target
  repo's spec tree and route free-text requests to the right workflow.
- **`drain/`** — unattended queue-draining: repeatedly spawns fresh-context headless one-shots
  against the router until the queue empties or a stop condition fires.

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
