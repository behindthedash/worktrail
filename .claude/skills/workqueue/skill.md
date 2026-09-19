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
    - score_candidates
    - batch_candidates
    - precheck_duplicate
    - _focus_overlap
    - MIN_FOCUS_TOKENS
    - BATCH_MIN
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
- **`_worktree_pr_close()` (fold-into-change / propose-change) also claims first, before any
  git/worktree/`land_pr` work, and `release()`s on any failure prior to a PR URL.** Previously the
  claim happened only after a PR was opened, leaving a window where two concurrent triage runs
  could each evaluate and apply the same brief through their own worktree/PR pipeline before
  either saw the other's work, producing two separate merged OpenSpec proposal PRs for the same
  feature (the duplicate-PR incident this guards against). Now a second concurrent run sees
  `already-claimed` and returns an `error` status immediately — no fetch, no worktree, no
  `land_pr` — instead of racing through to its own PR. A failure after claiming but before a PR
  URL exists still `release()`s the brief back to `queue/` (retryable, not byte-identical to the
  pre-claim state since claim/release stamp housekeeping frontmatter fields on the round trip,
  but same `status: queued` and no `triaged-to`); once a PR URL exists, closing proceeds as
  before via `done(..., triaged_to=pr_url)`.
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
  push or `gh pr create`, and the brief is released back to `queue/` per the claim-first guard
  above. Bounded by `_COMPILE_TIMEOUT_S` (900s) since an OpenSpec change may need one model
  inference pass.
- **The fold-into-change task declares an explicit `files:` scope line derived from its
  evidence.** `_fold_task_file_scope(worktree_dir, evidence)` takes every path probe from
  `router.brief_probes.extract_probes()` (a `:120-140` line-number suffix stripped) that exists
  as a file in the worktree, then for each `src/` path appends the first matching existing
  `tests/**/test_<stem>*.py` — the same glob compile's scope check uses. `worktrail-compile`
  seeds scope from an indented `files:` line when present and otherwise infers it, and its scope
  check refuses a task touching a `src/` file with no `tests/` path when that test file already
  exists; evidence cites source files but never their tests, so the inferred scope failed on
  every fold into a change with existing tests (brief 20260903-145001). An empty result emits no
  `files:` line, leaving compile's inference as before.
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
- **`cmd_evaluate()` resolves a group's `repo:` frontmatter against `repos_root` before using it
  as the evaluator subprocess `cwd` — never the raw frontmatter string.** It calls the same
  `dashboard._resolve_repo_dir(repo, repos_root)` already used elsewhere in this module (e.g. the
  WIP-cap check, `_apply_close`), not a second resolution path. When a group's `repo` value
  doesn't resolve to a real directory under `repos_root` (a bare non-canonical name like
  `repo: aspens`, or a typo), that group is skipped — logged and counted in `groups_unevaluated`
  — instead of using it directly as `subprocess.run()`'s `cwd`, which previously raised
  `FileNotFoundError` and aborted the *entire* `evaluate` run, including every other group that
  would otherwise have evaluated fine.
- **FOCUS text is scored through `cluster_detect._focus_overlap`; BODY text keeps the raw
  coefficient.** The overlap coefficient divides by the SMALLER token set, so a thin focus text
  is trivially a near-subset of any longer brief. Both of `score_candidates.py`'s scoring sites
  (`_score_against_queue`, `batch_candidates`) compare focus through `router/cluster_detect.py`'s
  `_focus_overlap`, which returns 0.0 when either side carries fewer than `MIN_FOCUS_TOKENS`
  (10) distinct tokens; body keeps the local `_overlap_coefficient`, since a brief body is always
  long enough for the denominator to mean something. The floor is imported rather than
  re-derived: this module's effective focus threshold is LOWER than cluster_detect's — with
  same-repo mandatory, `focus * 0.7 + 0.20 >= BATCH_MIN` means focus >= 0.357 against
  cluster_detect's 0.45 — so a weaker floor here could never be justified, and one floor with
  one rationale can't drift apart from a second. Only the "these two read alike" signal abstains:
  a structural signal such as a `related` link still batches a thin brief.
- **A durable-artifact label under two distinct tokens is skipped, not scored.**
  `create_handoff._scan_durable_artifact_overlaps` otherwise keeps the raw coefficient —
  `MIN_FOCUS_TOKENS` guards brief-to-brief comparisons, while this is a containment test ("what
  share of the label's words does the focus mention") against a label that is short by nature.
  A SINGLE-token label is the degenerate case: its coefficient can only be 1.0 or 0.0, so it is
  a bare word match carrying no evidence of overlap, and it always sorts to the top of the
  advisory list. Observed live 2026-09-19: the generic `docs/specs/` directories `epics` and
  `research` each warned at score 1.00 against briefs that merely used the word. Two tokens is
  the minimum at which the score can distinguish a partial match from a full one.

## Critical files
- `workqueue/work_queue.py` — the single implementation every consumer shares; do not reimplement
  claim/done/release logic at a new call site. Its frontmatter editors (`_set_fm_fields`,
  `_remove_fm_field`, `_set_fm_list_field`) share `_splice_fm_key` / `_fm_field_lines` /
  `_check_fm_fields`; add new frontmatter mutations on top of those, not with fresh line-matching
- `workqueue/queue_triage.py` — intake-triage verdict apply actions (stale-close, duplicate-of,
  fold-into-change, propose-change, keep); the only caller that closes briefs with `triaged=True`.
  `_fold_task_file_scope` derives the folded task's `files:` scope from evidence paths.
  `_worktree_pr_close()` is the shared fold-into-change/propose-change pipeline and claims the
  brief before any git/worktree/`land_pr` work (see the claim-first guard above). `cmd_evaluate()`
  resolves each group's `repo:` via `_resolve_repo_dir()` before using it as the evaluator `cwd`,
  skipping (not crashing on) a group whose repo doesn't resolve
- `workqueue/create_handoff.py` (via `worktrail-handoff`) — brief creation entrypoint; delegates
  repo inference to `repo_inference.infer_repo()` with a prefix-match fallback.
  `_scan_durable_artifact_overlaps` is the capture-time advisory scan over spec slugs, OpenSpec
  changes and open PRs; it shares `cluster_detect`'s tokenizer and `OVERLAP_THRESHOLD` so
  capture-time warnings and consume-time cluster detection agree on what "overlapping" means,
  and skips labels under two distinct tokens
- `workqueue/score_candidates.py` — brief-to-brief scoring (`_score_against_queue`,
  `batch_candidates`, `precheck_duplicate`); focus comparisons go through
  `cluster_detect._focus_overlap`, body comparisons through the local raw `_overlap_coefficient`
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
- Never move the `claim()` call in `_worktree_pr_close()` later in the pipeline — it must stay
  first, before `git fetch`/worktree creation, so a concurrent triage run on the same brief is
  rejected before any duplicate work starts.
- Never use a group's raw `repo:` frontmatter string directly as a subprocess `cwd` in
  `cmd_evaluate()` — always resolve it through `_resolve_repo_dir(repo, repos_root)` first and
  skip the group if it doesn't resolve, so one bad `repo:` value can't abort every other group's
  evaluation.
- Never score brief-to-brief focus text with the raw `_overlap_coefficient`, and never re-derive
  the token floor locally — import `_focus_overlap`/`MIN_FOCUS_TOKENS` from
  `router/cluster_detect.py` so the two floors stay one calibrated constant.

---
**Last Updated:** 2026-09-19
