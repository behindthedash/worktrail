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
    - repo_inference
    - infer_repo
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
- **In-place frontmatter edits splice the whole key, continuation lines included.**
  `_set_fm_fields`, `_remove_fm_field`, and `_set_fm_list_field` all go through
  `_splice_fm_key`, which drops every indented/blank continuation line of the old value
  (`|-` block scalar body or block-sequence items) along with the `key:` line. Replacing only
  the `key:` line is how a block-scalar `focus` got corrupted live (2026-09-04): the old
  continuation lines survived and PyYAML silently folded them into the new plain scalar.
  Keys in `_LITERAL_FM_KEYS` (`focus`) are re-rendered via `serialize_frontmatter` as a `|-`
  literal block so a canonical brief stays `is_canonical_style` instead of downgrading to
  quotes. When the block parsed before the edit, `_check_fm_fields` re-parses the result and
  requires every field just set to read back as requested (block-scalar values compare
  `.strip()`ped), raising `ValueError` before anything hits disk; a block that was *already*
  unparsable is still edited surgically and left to the caller's post-write `validate_brief`.
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
- **A `work-directly` verdict is downgraded to `keep` unless its evidence names a command.**
  `queue_triage._REPRODUCTION_EVIDENCE_RE` gates the stamp: it accepts test runners and lint
  tools (`pytest`, `tests/`, `make lint`, `mypy`, ...), `gh` with a known read subcommand
  (`gh repo view`, `gh pr ...`), `git` with a known read/verify subcommand (`git log`,
  `git status`, `git diff`, ...), a flagged `grep`/`rg` invocation (`grep -rn foo`), or
  "reproduces via"/"confirmed via". Bare prose such as "git history", "gh workflow", "grep for
  it", or "I read/inspected the file" does not qualify, nor do the bare words "command"/"check"
  (live 2026-09-03, brief 20260903-111047: high-confidence evidence citing `gh repo view` and
  `grep -rn` was downgraded because the regex only knew test-runner and lint tools).
- **`keep` is no longer a no-op verdict.** `apply_verdicts()` routes `keep` to `_apply_keep()`,
  which appends an in-place `## Triage <run-date>` note stamping `verdict: keep` and
  `keep-count: <n+1>` ahead of the evidence text — `n` from `consecutive_keep_count()`, the
  trailing run of `keep` notes read off `triage_history()` — previewed as `status: planned` /
  `action: append-triage-note` without `--confirm`, executed with it. `triage_history()` parses
  every `## Triage <date>` section in a brief's body into a `TriageNote(date, verdict,
  keep_count)`; `is_recently_triaged()` is rebuilt on top of it and ignores `verdict:
  repo-inferred` notes (queue-time repo inference, not a triage outcome), so that note alone
  never blocks a later evaluation. The escalation matrix that reads this streak to force a
  repeatedly-kept brief to `needs-decision`/`propose-change` has not landed in this checkout yet
  (queue_triage's own tasks.md task 4.1(b) is still open as of 2026-09-03) — only the streak
  bookkeeping itself is live.
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
- **Repo inference never guesses among ambiguous candidates.** `repo_inference.infer_repo(focus,
  repos_root)` tries three rules in order — (a) an explicit `Repo:`/`repo:` token (basename
  match, so a path-shaped value works), (b) a known repo name as a whole word (word boundary
  excludes `-` and `_` so `datalena-worktrees` does not match `datalena`), (c) a path probe from
  `router.brief_probes.extract_probes()` that exists under exactly one known checkout. The
  *first rule that finds any candidate at all* decides: exactly one distinct repo resolves it,
  two or more returns `repo=None` with that rule's sorted `candidates` (a later rule is never
  consulted to break the tie), zero falls through. A "known repo" is a direct subdirectory of
  `repos_root` (default `~/projects`) with a `.git` entry — file or directory, so a worktree
  checkout qualifies. `create_handoff._infer_repo_from_focus` now delegates to this at
  creation time, falling back to its own older bare `<project>:` prefix match against a plain
  (non-`.git`) `~/projects/<name>` directory only when `infer_repo` matched no rule at all — a
  rule that matched but stayed ambiguous (`rule` set, `repo=None`) is a deliberate refusal to
  guess and the prefix fallback must never override it. The intake-triage evaluator's null-repo
  write-back is a separate, not-yet-landed consumer as of 2026-09-03 (queue_triage tasks.md
  group 4, tasks 4.1(c)–4.3) — `worktrail-go`'s Phase 2 gate still does not pass a
  `--triage-repos-root` flag, and a brief with no `repo:` frontmatter reaching evaluation is
  still evaluated in the repo-less `__none__` group and comes back `needs-decision` when the
  target cannot be told from the brief.

## Critical files
- `workqueue/work_queue.py` — the single implementation every consumer shares; do not reimplement
  claim/done/release logic at a new call site. Its frontmatter editors (`_set_fm_fields`,
  `_remove_fm_field`, `_set_fm_list_field`) share `_splice_fm_key` / `_fm_field_lines` /
  `_check_fm_fields`; add new frontmatter mutations on top of those, not with fresh line-matching
- `workqueue/queue_triage.py` — intake-triage verdict apply actions (stale-close, duplicate-of,
  fold-into-change, propose-change, keep); the only caller that closes briefs with `triaged=True`
- `workqueue/create_handoff.py` (via `worktrail-handoff`) — brief creation entrypoint; delegates
  repo inference to `repo_inference.infer_repo()` with a prefix-match fallback
- `workqueue/repo_inference.py` — `InferenceResult(repo, rule, candidates)` + `infer_repo()`; the
  deterministic focus-text → repo resolver for briefs with no `repo:` frontmatter

## Critical Rules
- Never write directly into `queue/`/`picked/` with plain file I/O from a new call site — always
  go through `work_queue.py`'s functions so the write-verification and atomic-rename guarantees
  hold.
- Never bypass the Route-C gate with `--triaged` for an ordinary completion — it is for triage
  closures only; a brief whose work was actually done still needs `--planning-only` or
  `--implementation-complete`.
- Never replace a frontmatter `key:` line by itself — a block-scalar or list value has
  continuation lines that must go with it; use `_splice_fm_key`.

---
**Last Updated:** 2026-09-05
