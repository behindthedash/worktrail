# Contributing to worktrail

Start with [README.md](README.md) for what this is and [AGENTS.md](AGENTS.md) for architecture,
origin, and the full development workflow. This file is the short version of the rules every
change is expected to follow — the same rules the repo's CI enforces.

## Development setup

```bash
./scripts/dev-install.sh   # pip install -e ".[dev]" — run from the canonical checkout only
pytest                     # full suite
python3 -m worktrail.orchestrator.orchestrate check   # golden record/replay regression
```

`dev-install.sh` refuses to run from a linked worktree on purpose: the editable install records
an absolute path, and installing from a worktree that later gets deleted silently breaks every
`worktrail-*` console script.

## Workflow

- **Never commit on `main`.** Branch off `main` into a sibling worktree
  (`git worktree add ../worktrail-worktrees/<branch> -b <branch> main`), open a PR, merge only
  after CI is green, and delete the branch and worktree once it lands.
- **Features and behavior changes need an OpenSpec change** under `openspec/changes/<id>/`
  (proposal, spec delta, tasks) with a committed `.compile-ok` marker — run
  `worktrail-compile openspec/changes/<id>` after editing the change; the `Scope check` CI job
  fails without it. The requirement-coverage gate needs each requirement name cited on a single
  unwrapped line in `tasks.md`. Archive the change (`openspec archive <id> -y`) in a follow-up
  PR after the feature merges.
- **Tests are not optional.** New behavior gets tests beside the existing ones under `tests/`;
  the suite must pass in full, and `orchestrate check` must stay golden. Tests must never touch
  operator state — `tests/conftest.py` isolates `WORKTRAIL_HOME` and `WORK_QUEUE_DIR` per test,
  and new machine-wide state needs the same treatment.
- **Version bumps are batched, not per-PR.** Feature/fix PRs need no label or bump; a standalone
  `chore: bump Worktrail to X.Y.Z` PR bumps `pyproject.toml` and `.codex-plugin/plugin.json`
  together once a batch lands. `CI: Version Bump Check` only validates PRs that actually
  change `pyproject.toml`'s version.
- **Skill docs are load-bearing.** Every `worktrail-*` command named in a `skills/**` doc must
  be a real `[project.scripts]` entry point, and cross-references between skills resolve by
  `{#anchor}` — `tests/test_plugin_surface.py` fails the build on drift.

## Reporting issues

Security reports go through [SECURITY.md](SECURITY.md). For everything else, open a GitHub
issue with the failing command, expected vs actual behavior, and the relevant run record or
drain transcript when one exists.
