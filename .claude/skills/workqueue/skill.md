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
    - queue_triage
    - duplicate-of
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
  `done` is rejected for that route so the completion type is always explicit. The exception is
  a **triage closure** (`--triaged`, `--triaged-to`, or `--duplicate-of` — what
  `queue_triage.py`'s stale-close / fold-into-change / propose-change / duplicate-of apply
  actions pass): the brief closes because its work is stale, now lives in an OpenSpec change, or
  is tracked by another brief, so it is neither "planned" nor "implemented" and the gate does not
  apply (live 2026-09-02: the first unattended intake-triage pass rolled back 9 of 27 verdicts on it).
- **`--duplicate-of BRIEF-ID` stamps `duplicate-of:` frontmatter and waives the
  consolidation-evidence gate** — a consolidated batch closed as a duplicate has its sub-items
  carried by the surviving brief, not shipped by this one; `done()` appends a
  `duplicate-of: sub-item(s) ... are carried by <id>` line to the closure note instead of
  returning `unverified_consolidation_closure`.
- **`claim-batch` claims a primary plus related companions per-brief atomically** — a partial
  failure on one companion does not silently leave the primary claimed with an inconsistent
  batch; check the per-brief result, not just the overall exit code.
- **A failed triage apply is a true no-op.** `queue_triage._apply_close` does `claim()` then
  `done(..., triaged=True, duplicate_of=...)`; if `done()` still refuses (ownership mismatch, an
  unbacked re-verification claim, ...) it `release()`s the brief back to `queue/` rather than
  leaving it stranded in `picked/` under a `queue-triage` claim nobody will release.
- **Worktree-PR closures (fold-into-change / propose-change) run `worktrail-compile` on the
  change before committing** so the `.compile-ok` marker matches the edited `tasks.md` — CI's
  Scope check (`check_compile_markers.py`) refuses a change PR without one (live 2026-09-02:
  worktrail #897/#898 both failed it). A compile failure returns `status="error"` before any
  push or `gh pr create`, and the brief is untouched. Bounded by `_COMPILE_TIMEOUT_S` (900s)
  since an OpenSpec change may need one model inference pass.
- **Push goes to `git config remote.pushDefault` when set, else `origin`.** `_push_target()`
  returns the remote plus its GitHub `owner/repo` slug so `gh pr create -R <slug>` targets the
  fork's repo; with no `pushDefault` it pushes `origin` and lets `gh` infer the base repo as
  before (live 2026-09-02: an unattended propose-change against `aspens` pushed to upstream
  `aspenkit/aspens` and was denied because the fork remote was never consulted).

## Critical files
- `workqueue/work_queue.py` — the single implementation every consumer shares; do not reimplement
  claim/done/release logic at a new call site
- `workqueue/queue_triage.py` — intake-triage verdict apply actions (stale-close, duplicate-of,
  fold-into-change, propose-change); the only caller that closes briefs with `triaged=True`
- `workqueue/create_handoff.py` (via `worktrail-handoff`) — brief creation entrypoint

## Critical Rules
- Never write directly into `queue/`/`picked/` with plain file I/O from a new call site — always
  go through `work_queue.py`'s functions so the write-verification and atomic-rename guarantees
  hold.
- Never bypass the Route-C gate with `--triaged` for an ordinary completion — it is for triage
  closures only; a brief whose work was actually done still needs `--planning-only` or
  `--implementation-complete`.

---
**Last Updated:** 2026-09-02
