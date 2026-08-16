---
name: workqueue
description: Handoff-brief queue lifecycle — atomic claim/done/release and write verification for src/worktrail/workqueue
triggers:
  files:
    - src/worktrail/workqueue/**
  keywords:
    - work_queue
    - WORK_QUEUE_DIR
    - claim
    - handoff brief
    - picked/
    - queue/
---

You are working on **worktrail's work-queue handoff system**: the atomic claim/done/release
lifecycle for briefs under `$WORK_QUEUE_DIR`.

## Domain purpose
Every consumer that needs to hand off deferred work — the `handoff` skill's Consume workflow, the
`go` front door, and sdd-workflow's handoff-seed mode — goes through `work_queue.py` so the
move-a-brief mechanism never diverges between callers.

## Business rules / invariants
- **Two folders only, no separate `done/`.** `$WORK_QUEUE_DIR/queue/` holds waiting briefs;
  `picked/` holds claimed briefs, in-flight AND completed, distinguished purely by the
  frontmatter `status:` field (`picked` -> `done`).
- **A POSIX rename is the claim arbiter.** `claim` moves `queue/<file> -> picked/<file>`; exactly
  one racing agent wins the atomic rename, the loser gets an "already claimed" signal and
  re-lists. This is what makes claiming safe without a lock server.
- **Every mutation that leaves a file on disk re-reads and validates its own write**
  (`brief_frontmatter.validate_brief`: a `---`-fenced block that parses as YAML to a mapping with
  non-empty `id`/`status` fields) before reporting success. A validation failure restores the
  file's pre-mutation content (and, for `claim`/`release`, undoes the queue/picked move) and
  returns `write-verification-failed` instead of a false-positive success — never trust "the
  write call returned" as proof the write is good.
- **Exit codes are part of the contract for `claim`/`claim-batch`**: 0 ok, 2 none, 3 ambiguous,
  4 already-claimed, 5 io-error, 6 write-verification-failed (keyed off the primary brief for
  `claim-batch`).
- **Route-C briefs require `--planning-only` or `--implementation-complete` on `done`** — a bare
  `done` is rejected for that route so the completion type is always explicit.
- **`claim-batch` claims a primary plus related companions per-brief atomically** — a partial
  failure on one companion does not silently leave the primary claimed with an inconsistent
  batch; check the per-brief result, not just the overall exit code.

## Critical files
- `workqueue/work_queue.py` — the single implementation every consumer shares; do not reimplement
  claim/done/release logic at a new call site
- `workqueue/create_handoff.py` (via `worktrail-handoff`) — brief creation entrypoint

## Critical Rules
- Never write directly into `queue/`/`picked/` with plain file I/O from a new call site — always
  go through `work_queue.py`'s functions so the write-verification and atomic-rename guarantees
  hold.

---
**Last Updated:** 2026-08-16
