---
name: base
description: Core conventions, tech stack, and project structure for worktrail
triggers:
  alwaysActivate: true
---

You are working in **worktrail**, a spec-format-agnostic task orchestration package.

## Tech Stack
Python 3.10+ | one runtime dependency: `pyyaml` | pytest for tests (`dev` extra) | setuptools `src/` layout packaging | no linter/formatter is configured — CI's "Lint, Test & Build" job runs pytest + a golden regression check + build, not an actual lint step

## Commands
- `./scripts/dev-install.sh` — `pip install -e ".[dev]"`; refuses to run from a linked worktree (must be the canonical checkout, e.g. `~/projects/worktrail`)
- `pytest` / `pytest -q` — full test suite (`testpaths = ["tests", "hooks"]`)
- `python3 -m worktrail.orchestrator.orchestrate check` — golden record/replay regression for the orchestrator; run alongside pytest, not a substitute for it
- `python3 -m build` — sdist/wheel build (mirrors CI's own build step)

## Critical Conventions
- **Never `pip install -e` from a task worktree.** The editable install records the checkout's absolute path; deleting a merged worktree (the standard teardown-after-merge step) then breaks every `worktrail-*` console script with `ModuleNotFoundError: No module named 'worktrail'` until someone manually reinstalls from the canonical checkout. `dev-install.sh` enforces this by refusing to run from a worktree.
- **~70 console scripts, one per `[project.scripts]` entry in `pyproject.toml`.** A skill, doc, or code path that names a `worktrail-*` command must match a real entry point exactly — `tests/test_plugin_surface.py` enforces this in CI.
- **Every PR that changes `src/worktrail/**` must also bump `pyproject.toml`'s `version`** (real semver, unlike sibling plugin repos' version-less/SHA-tracked model), unless the PR carries the `go:no-version-bump` label for an intentionally deferred, later batch bump. `CI: Version Bump Check` enforces this.
- **Never commit or develop directly on `main`.** Branch off `main` into a sibling worktree (`git worktree add ../worktrail-worktrees/<branch> -b <branch> main`); merge only after CI is green; delete the branch once its PR lands.

## Structure
- `src/worktrail/conductor/` — compiles a spec/change into a schedulable RunPlan (see the `worktrail` skill)
- `src/worktrail/orchestrator/` — parallel git-worktree fan-out execution, live agent spawning, branch integration + PR creation (see the `worktrail` skill)
- `src/worktrail/taskformats/` — the `TaskSource` adapter interface plus `devkit`, `openspec`, and `speckit` implementations (see the `worktrail` skill)
- `src/worktrail/addons/` — opt-in post-task tooling, e.g. the `aspens` skill-doc sync add-on (see the `worktrail` skill)
- `src/worktrail/workqueue/` — the `$WORK_QUEUE_DIR` handoff-brief claim/done/release lifecycle (see the `workqueue` skill)
- `src/worktrail/router/` — the deterministic route classifier, resume dashboard, policy loader, and run records (see the `router` skill)
- `src/worktrail/drain/` — unattended queue-draining loop (see the `drain` skill)
- `src/worktrail/shared/` — cross-cutting helpers (`homedir.py`, `brief_frontmatter.py`)
- `tests/` — mirrors the `src/worktrail/` layout (see the `tests` skill)
- `skills/`, `commands/` — this repo's own Claude Code plugin marketplace surface (see AGENTS.md "Claude Code plugin surface")

---
**Last Updated:** 2026-08-16
