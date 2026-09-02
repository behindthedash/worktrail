## Why

`is_canonical_style()` (`src/worktrail/shared/brief_frontmatter.py`) only runs as a post-write
self-check inside this repo's own two writers, `create_handoff.py` and `consolidate_cluster.py`
— each raises and unlinks its own output if the frontmatter it just wrote isn't canonical. That
guarantees nothing about the other ~1400 briefs already on disk, and nothing about a brief written
by a tool this repo does not own: an external Codex/OpenCode-side writer, or a future internal
writer that forgets to route through `serialize_frontmatter`. No CI workflow or scheduled job
references corpus-wide validation today (confirmed: no `queue`/`corpus` hits in `.github/workflows`),
so a non-canonical file entering the live `$WORK_QUEUE_DIR` corpus is invisible until something
downstream trips on it.

`$WORK_QUEUE_DIR` is runtime state on the operator's machine, never inside this repo or any
consuming repo, so GitHub Actions cannot reach it — the same reason `worktrail-dist-tag-watch`
(the existing precedent for a queue-corpus-scanning tool) ships as a bare console script with no
CI wiring, invoked externally on whatever cadence the operator sets up. This change follows that
precedent: it adds a scan tool, not a GitHub Actions workflow.

## What Changes

- Add a new read-only scan over `queue/` and `picked/` under `$WORK_QUEUE_DIR` that evaluates
  every brief's frontmatter block against `is_canonical_style()` and reports each file that
  doesn't match, distinguishing a style mismatch (parses fine, wrong scalar style/quoting) from a
  malformed block (missing fence or unparseable YAML) since the two need different remediation.
  This scan reports; it never rewrites, moves, or otherwise touches a file (that stays
  `backfill_focus_style.py`'s job for the one-time historical cleanup, and each writer's own
  post-write self-check for new writes).
- Expose it as a new console script (`worktrail-check-corpus-canonical-style`) with `--json` output
  and a nonzero exit code when any finding exists, so it can be wired into an external cron or CI
  job by whoever operates a given `$WORK_QUEUE_DIR` — mirroring `worktrail-dist-tag-watch`, which
  ships the same way with no workflow file in this repo.

## Capabilities

### New Capabilities

- `work-queue-corpus-canonical-style-scan`: Defines the read-only corpus-wide scan for
  non-canonical work-queue frontmatter, its style-mismatch/malformed classification, and its
  scriptable JSON/exit-code contract.

### Modified Capabilities

None.

## Impact

- Adds `src/worktrail/workqueue/check_corpus_style.py` and a `worktrail-check-corpus-canonical-style`
  entry in `pyproject.toml`'s `[project.scripts]`.
- Adds `tests/workqueue/test_check_corpus_style.py`.
- Reuses `is_canonical_style()`, `split_frontmatter()`/`_find_frontmatter_block()`, and
  `validate_brief_path()` from `src/worktrail/shared/brief_frontmatter.py` without changing them.
- Does not touch `create_handoff.py`, `consolidate_cluster.py`, `backfill_focus_style.py`, or any
  stored brief. Does not add a GitHub Actions workflow, since `$WORK_QUEUE_DIR` is not repo state.
