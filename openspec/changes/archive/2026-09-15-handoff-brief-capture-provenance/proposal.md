## Why

Work-queue briefs record when they were captured (`created`), but not who or what captured them.
A brief can come from several places:
- an agent running the `worktrail-handoff` skill;
- the backlog seeder (`worktrail-seed-backlog`);
- cluster consolidation (`consolidate_cluster.py`);
- external callers of the `worktrail-handoff` CLI, such as detectors and hooks.

On disk, all of these briefs look the same. Triage and operators can't tell a human-intent
capture from a machine-generated one without reading the body. They also can't trace a noisy
source back to its origin.

The existing `seeded-from:` key does not solve this. It is an execution-provenance signal that
`work_queue.py` reads to classify a brief as `execution` vs `intake`, and it is set only by the
seeder and by triage `work-directly`. It is not a general capture-source stamp.

Evidence from brief `20260910-040952-handoff-brief-capture-provenance-stamp`:
- `create_handoff.py:397` stamps `created` and no capture-source field.
- Searching `src/worktrail/workqueue/` for `captured-by`/`provenance` finds only decision-envelope
  provenance in `decisions.py`.
- No PR or OpenSpec change covers this.

## What Changes

- **New brief frontmatter key `captured-by`.** It is a short source identifier: a lowercase
  kebab-case source name with an optional `:<detail>` suffix, for example `worktrail-handoff`,
  `seed-backlog`, `consolidate-cluster`, or `detector:stale-branch`.
- **`create_handoff()`** gains `captured_by: str | None = None`:
  - it validates the value's shape and raises `ValueError` on a malformed value;
  - it stamps the key right after `created`;
  - when the value is omitted, it stamps `unknown`, so every newly minted brief carries the key.
- **`worktrail-handoff` CLI** gains `--captured-by`, defaulting to `worktrail-handoff`.
- **In-repo minting callers stamp their own source:**
  - `seed_backlog.py` passes `captured_by="seed-backlog"`;
  - `consolidate_cluster.py` stamps `captured-by: consolidate-cluster` on the brief it writes
    directly.
- **`worktrail-handoff` skill doc** tells capturing agents and external callers (hooks, detectors)
  to pass `--captured-by` with their own source name.
- `seeded-from` semantics are unchanged. Existing briefs are not backfilled: a missing
  `captured-by` is read as unknown and never fails validation.

## Capabilities

### New Capabilities

- `handoff-brief-capture-provenance`: every brief minted by Worktrail records its capture source
  in a validated `captured-by` frontmatter key.

### Modified Capabilities

_None._

## Impact

- `src/worktrail/workqueue/create_handoff.py` and `tests/workqueue/test_create_handoff.py`.
- `src/worktrail/workqueue/seed_backlog.py` and `tests/workqueue/test_seed_backlog.py`.
- `src/worktrail/router/consolidate_cluster.py` and `tests/router/test_consolidate_cluster.py`.
- `skills/worktrail-handoff/SKILL.md`.
- External callers outside this repo (Stop-hook and detector integrations) keep working unchanged
  and are stamped `worktrail-handoff` until they pass `--captured-by`.
- Brief readers, `validate_brief`, and the `execution`/`intake` classification are unaffected.
