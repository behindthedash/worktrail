## Context

The drain's remediation table intentionally handles both legacy devkit specs
and OpenSpec changes. That format distinction already matters in the
stale-bookkeeping action: its finder copies `row["format"]`, defaulting a
format-less dashboard row to `devkit`, and the action uses the corresponding
native artifact operation. The sync-pending finder omitted that information
and its action unconditionally invokes `/opsx:sync`.

An OpenSpec sync is an agent-driven merge of a change's delta specs into the
canonical OpenSpec capability specs. A devkit sync instead reconciles the
merged implementation with `docs/specs/<id>/` and records a `spec-sync`
analysis source in `knowledge-graph.json`. The latter record is part of the
existing devkit `sync-pending` stage predicate, so an OpenSpec-only command
cannot make a devkit finding leave the stage.

## Goals / Non-Goals

**Goals:**

- Preserve the dashboard's format classification from discovery through the
  one-finding action.
- Run the native sync operation for each supported format in the isolated
  remediation worktree.
- Make a command that claims success but leaves the same finding pending
  visible as a remediation failure rather than a successful no-op.

**Non-Goals:**

- Changing how `dashboard` determines `sync-pending`.
- Rewriting the OpenSpec sync skill or defining a new common spec format.
- Changing branch naming, commit/push, shared PR landing, or the generic
  remediation table's failure-isolation behavior.

## Decisions

### 1. Use the dashboard row's format and preserve the devkit default

`find_sync_pending_specs()` will attach `format: row.get("format") or
"devkit"` to every finding. This mirrors the stale-bookkeeping finder and
keeps legacy dashboard rows, which do not emit a format key, on their correct
devkit path. The action consumes this field rather than rediscovering format
from the worktree, avoiding a second source of truth after the finder has
selected a row.

### 2. Keep `/opsx:sync` exclusive to OpenSpec; dispatch devkit spec-sync for devkit

`build_sync_command()` will receive the finding format and produce the
OpenSpec `/opsx:sync <change-id>` dispatch only for `openspec`. For `devkit`,
it will dispatch the developerkit spec-sync agent with the spec id and
worktree context necessary to reconcile `docs/specs/<id>` and write the
knowledge-graph `metadata.analysis_sources` record. Both remain agent-driven
file edits in the isolated worktree, so the existing commit, push, and shared
PR-landing sequence remains unchanged.

An OpenSpec-only command plus a devkit prompt workaround is rejected: it
would preserve a misleading operation name and still depend on a skill that
does not own devkit artifacts.

### 3. Re-check only a successful no-diff result

After an exit-zero sync finds no worktree changes, `_run_sync_pending()` will
re-scan/re-evaluate that finding's current stage. If it remains
`sync-pending`, the action raises a descriptive error. `sweep_remediations()`
already catches an action error, logs it under `resume-sync-pending`, and
continues, so no new failure-control path is needed.

The re-check avoids treating a legitimate race as an error: another process
may have landed the required sync after the finding was discovered. A
non-zero agent exit retains its existing result behavior; this decision is
limited to the deceptive exit-zero/no-diff case.

## Risks / Trade-offs

- A devkit agent can still make an incomplete or incorrect edit. The existing
  PR review and stage detection remain the safety mechanisms; this change
  only ensures it is asked to edit the right artifact class.
- A transient dashboard-read failure during the no-diff re-check must not be
  mistaken for proof that the sync succeeded. It should surface as the same
  per-finding remediation failure for a later sweep.

## Migration Plan

No data migration or configuration change. Existing OpenSpec findings retain
their command shape. A rollback is a code revert; already-landed sync PRs are
ordinary documentation commits.
