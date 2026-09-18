## Why

`worktrail-audit-delivery` (`src/worktrail/router/audit_delivery.py`) only ever moves a
not-an-ancestor task out of `confirmed_dropped` when it can prove *positive* content presence on
the base branch: byte/line match (`content_delivered_via_rewrite`), identifier survival
(`identifiers_survive_elsewhere`), or a never-shipped path policy (`never_shipped_by_policy`).
The 2026-09-17 exhaustive re-verification of the fleet-wide audit
(`docs/specs/research/fleet-wide-retroactive-delivery-audit.md`, PR #1221) verified all 146
`confirmed_dropped` candidates by hand and found 0 genuine drops. Two false-positive categories
account for a large share of the noise and cannot be recognised by any positive-presence check:

1. **Deliberate deletion** — the task's own commit *removed* a file (an out-of-scope test
   flagged in review, a superseded module). The path's absence on base is the intended end
   state, but `_file_content_on_base` returns `False` for a path base does not have, so the
   task is reported dropped.
2. **Systemic restructuring events** — a repo-wide reorganisation (datalena's Alembic
   migration-baseline squash in PR #2902, its `api/app/models.py` → `api/app/models/` package
   split, worktrail's PR #739 refactor retiring 4 shipped files) orphans old paths at scale
   while the behaviour persists. 8/51 datalena, 4/9 gracefully-giving-back, and a 19-item
   worktrail cluster were exactly this.

Every re-run of the audit re-surfaces these as `confirmed_dropped`, so the tool's actionable
signal is buried. (Work-queue brief `20260917-173019-worktrail-audit-delivery-automated-false`.)

## What Changes

- **Deletion-aware per-file check.** A path the task's commit deleted counts as delivered
  when it is absent on base. A task whose shippable diff is pure deletion (no added lines)
  is bucketed as `content_delivered_via_deletion`; a mixed add+delete task continues through
  `content_delivered_via_rewrite`, which now accepts the deleted paths.
- **Opt-in restructured-path filter.** A new repeatable `--restructured-path GLOB` CLI option
  (default: none). A task whose every remaining shippable file either matches a glob *and* is
  absent on base, or is otherwise content-verified, is bucketed as
  `superseded_by_restructure`. With no globs passed, behaviour is unchanged.
- Both new buckets appear in the JSON result, the per-repo summary line, and the final totals
  line. They never contribute to the non-zero exit code; only `confirmed_dropped` does.
- The false-negative bias is preserved: a deleted path that still exists on base, or a
  glob-matched path that still exists on base with different content, stays on the existing
  path to `confirmed_dropped`.

## Capabilities

### New Capabilities
- `audit-delivery-false-positive-filters`: deliberate-deletion and restructured-path
  classification in the retroactive delivery audit.

### Modified Capabilities

## Impact

- `src/worktrail/router/audit_delivery.py` (per-file check, two new bucket helpers,
  `audit_repo` classification order, CLI option and summary output).
- `tests/router/test_audit_delivery.py` (real-git regression tests for both buckets and the
  unchanged-default path).
- `docs/specs/research/fleet-wide-retroactive-delivery-audit.md` (note that the two
  categories are now automated and how to invoke the restructure filter).
- JSON output gains two keys; consumers reading only `confirmed_dropped` are unaffected.
