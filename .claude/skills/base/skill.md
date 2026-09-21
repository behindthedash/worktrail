---
name: base
description: Core conventions, tech stack, and project structure for worktrail
triggers:
  alwaysActivate: true
---

You are working in **worktrail**, a spec-format-agnostic task orchestration package.

## Tech Stack
Python 3.14+ | one runtime dependency: `pyyaml` | pytest for tests (`dev` extra) | setuptools `src/` layout packaging | ruff is pinned exactly (`ruff==<version>` in `pyproject.toml`'s `dev` extra) — CI's "Lint, Test & Build" job runs ruff lint + ruff format check + a shebang/exec-bit check + pytest + a golden regression check + build

## Commands
- `./scripts/dev-install.sh` — `pip install -e ".[dev]"`; refuses to run from a linked worktree (must be the canonical checkout, e.g. `~/projects/worktrail`)
- `pytest` / `pytest -q` — full test suite (`testpaths = ["tests", "hooks", "scripts/ci"]`)
- `python3 -m worktrail.orchestrator.orchestrate check` — golden record/replay regression for the orchestrator; run alongside pytest, not a substitute for it
- `python3 scripts/ci/ruff_pinned.py check .` / `... format --check .` — lint/format through the pinned-ruff wrapper; this is what CI and `.worktrail/policy.yaml`'s `pre_commit_cmd`/`integrate_smoke_cmd` invoke
- `python3 scripts/ci/check_shebang_exec_bits.py` — EXE001/EXE002 read from git's index modes
- `python3 -m build` — sdist/wheel build (mirrors CI's own build step)

## Critical Conventions
- **Never `pip install -e` from a task worktree.** The editable install records the checkout's absolute path; deleting a merged worktree (the standard teardown-after-merge step) then breaks every `worktrail-*` console script with `ModuleNotFoundError: No module named 'worktrail'` until someone manually reinstalls from the canonical checkout. `dev-install.sh` enforces this by refusing to run from a worktree.
- **Lint through the two `scripts/ci/` wrappers, never a bare `ruff`** — both exist because a local PASS was not evidence about CI. `ruff_pinned.py` runs the exact pin from `pyproject.toml` (PATH when it already matches, else `uvx ruff@<pin>`) and refuses anything else, because a global `ruff` installed once for every repo drifts from the pin (0.16.5 against a 0.16.7 pin on 2026-09-19) and ruff's behavior changes between patch releases. `check_shebang_exec_bits.py` enforces EXE001/EXE002 from git's index modes, because ruff disables those two rules on Windows and WSL (its own docs) while GitHub's Linux runners enforce them — PR #1279 passed lint locally and failed CI on both matrix legs over exactly that, and pinning the version does not help since the rules are off by platform.
- **~70 console scripts, one per `[project.scripts]` entry in `pyproject.toml`.** A skill, doc, or code path that names a `worktrail-*` command must match a real entry point exactly — `tests/test_plugin_surface.py` enforces this in CI.
- **Version bumps are batched, standalone commits, not per-PR** (real semver, unlike sibling plugin repos' version-less/SHA-tracked model). Ordinary feature/fix PRs need no label or bump. `CI: Release Metadata Check` validates only PRs that actually change `pyproject.toml`'s version: valid semver, increased over base, and `.codex-plugin/plugin.json` in sync.
- **Never commit or develop directly on `main`.** Branch off `main` into a sibling worktree (`git worktree add ../worktrail-worktrees/<branch> -b <branch> main`); merge only after CI is green; delete the branch once its PR lands.
- **Long-running launches (`worktrail-live full-real`, headless `worktrail-skill-dispatch`) go through `worktrail-detach launch`, never the Bash tool's `run_in_background`** — the harness reaps its own tracked background tasks on this host (see the `detach` skill).

## Structure
- `src/worktrail/conductor/` — compiles a spec/change into a schedulable RunPlan (see the `worktrail` skill)
- `src/worktrail/orchestrator/` — parallel git-worktree fan-out execution, live agent spawning, branch integration + PR creation (see the `worktrail` skill)
- `src/worktrail/taskformats/` — the `TaskSource` adapter interface plus `devkit`, `openspec`, and `speckit` implementations (see the `worktrail` skill)
- `src/worktrail/addons/` — opt-in post-task tooling, e.g. the `aspens` skill-doc sync add-on (see the `worktrail` skill)
- `src/worktrail/workqueue/` — the `$WORK_QUEUE_DIR` handoff-brief claim/done/release lifecycle (see the `workqueue` skill)
- `src/worktrail/router/` — the deterministic route classifier, resume dashboard, policy loader, and run records (see the `router` skill)
- `src/worktrail/drain/` — unattended queue-draining loop (see the `drain` skill)
- `src/worktrail/onboarding/` — `worktrail-repo-init`: scaffolds/migrates a repo onto the repo-standards doctrine (see the `onboarding` skill)
- `src/worktrail/runtime/` — provider-neutral runtime primitives: routing-catalog/target selection (`routing_source.py`, `selection.py`) and the `worktrail-detach` supervised-launch primitive (`detach.py`, see the `detach` skill)
- `src/worktrail/shared/` — cross-cutting helpers (`homedir.py`, `brief_frontmatter.py`)
- `scripts/ci/` — CI gate scripts callable locally, each with its `test_*.py` beside it
- `tests/` — mirrors the `src/worktrail/` layout (see the `tests` skill)
- `skills/`, `commands/` — this repo's own Claude Code plugin marketplace surface (see AGENTS.md "Claude Code plugin surface")

---
**Last Updated:** 2026-09-20
