## Context

See `proposal.md - Why` for the live incident this change fixes
(`go-20260814-140159`). Two relevant code paths, already traced against the
current worktree:

- `dispatch.py:build_worker_prompt` (`ROLE_CLEANUP`/tail branch, line ~385)
  computes `scope = ", ".join(task.get("files", [])) or "(see task file)"`
  unconditionally for every role, including `ROLE_CLEANUP`/`ROLE_IMPLEMENT`
  workers driving a `kind: e2e`/`kind: cleanup` task.
- `live.py`'s pre-flight file-existence/modify-only validation loop
  (`~line 731`) explicitly `continue`s for `kind in ("e2e", "cleanup",
  "docs")` before ever reaching the files-empty check — confirming nothing
  in the dispatch path treats a zero-file tail task specially today.
- Tail tasks never go through the impl-group `integrate_one` PR flow at
  all — they run in their own stacked per-task worktree
  (`_dispatch_pending_tail` → `live_run_real(with_tail=True)`), and PR
  creation for tail-task commits happens later and separately, via
  `integrate.detect_unreconciled_tail_evidence` +
  `reconcile_unreconciled_tail_evidence` (the `tail-task-auto-
  reconciliation` capability). `detect_unreconciled_tail_evidence`'s own
  docstring already states the *intended* contract: "A task with an empty
  `files:` scope that genuinely made no commits ... is not flagged; only
  one whose HEAD is not an ancestor of `<remote>/<base>` is" — i.e. the
  no-PR-for-zero-commits behavior is already correctly implemented at the
  reconciliation layer. The incident's near-duplicate PR happened because
  the worker *did* make commits (it reimplemented the whole change), not
  because the reconciliation layer misbehaved.
- `integrate.py:integrate_one`'s operator-PR-discovery block (`~line
  1071-1094`) runs `gh pr list --search "<group-name> <spec-id>"` — free
  text over PR title+body — and accepts `matches[0]` unconditionally. `gb =
  f"{run_id}/{name}"` (line 914) and `pr_base = target` (line 919, falling
  back to `base` at line 949) are both already in scope at that point,
  giving the fix free access to the group's own branch and target without
  new plumbing.

## Goals / Non-Goals

**Goals:**
- Make the dispatched prompt for a zero-file tail task unambiguous enough
  that a compliant worker makes no commits, so the existing (already
  correct) reconciliation-skip logic in `detect_unreconciled_tail_evidence`
  naturally never fires for that task.
- Add a branch-correspondence filter to operator-PR discovery so a
  free-text search match is only accepted when it is verifiably the
  group's own PR.
- Lock in both behaviors with regression tests so a future edit can't
  silently reintroduce either failure mode.

**Non-Goals:**
- Changing `detect_unreconciled_tail_evidence`'s ancestor-check logic
  itself — it is already correct for the genuinely-zero-commit case; this
  change does not touch it beyond adding test coverage that documents the
  existing contract.
- Adding a *runtime* guard that detects and rejects commits a worker makes
  despite the no-op instruction (e.g. quarantining a tail task that ignored
  the instruction and committed anyway). The proposal's "must not open a
  new PR when no files changed" requirement is satisfied by preventing the
  commits from happening in the first place (the prompt fix); policing a
  worker that disregards an explicit instruction is out of scope here and,
  if it recurs, is a separate incident to design a guard for.
- Changing `tail-task-auto-reconciliation`'s behavior for tasks that do
  have genuine unmerged commits — untouched.
- Any change to how non-tail tasks, or tail tasks with a non-empty `files:`
  list, are dispatched or integrated.

## Decisions

### 1. Zero-file tail-task no-op instruction lives in `build_worker_prompt`, gated on `kind` + empty `files`

Add a branch immediately after the existing `scope = ...` line:
`is_noop_tail = task.get("kind") in ("e2e", "cleanup") and not
task.get("files")`. When true, render a distinct scope line (e.g. `"Scope:
NO FILES ARE EXPECTED TO CHANGE — this is a verification-only task against
the already-integrated base. Do not reimplement, re-derive, or recreate any
part of this change. Run only the verification your task brief describes,
make no commits, and report success with files_touched: []."`) instead of
the current `f"Scope (only touch these): {scope}"` line, for every role that
renders that line (currently `ROLE_IMPLEMENT`/`ROLE_CLEANUP`/etc. share the
same final `"\n".join([...])` render, so the branch only needs to change
what `scope`/that one line evaluates to, not the overall render structure).

Alternative considered: special-case only `ROLE_CLEANUP`. Rejected —
`build_worker_prompt` is role-generic at the point `scope` is computed, and
tail tasks can drive other roles (e.g. `ROLE_IMPLEMENT` for an `e2e` task's
first pass) that would hit the same misleading fallback; gating on
`kind`+`files` rather than `role` covers every role a zero-file tail task
can be dispatched under.

### 2. No new "don't open a PR" code — cover the existing contract with a regression test instead

`detect_unreconciled_tail_evidence` already treats a task whose worktree
HEAD never moved past its stacked base as unflagged (no finding, no
reconciliation, no PR). Once decision 1 stops the worker from committing,
this existing logic already yields "no PR" for the zero-file case. Adding
a second, independent enforcement point (e.g. in `_dispatch_pending_tail`
or `reconcile_unreconciled_tail_evidence`) would duplicate a check that
already exists and risks the two disagreeing. Instead, add a regression
test against `detect_unreconciled_tail_evidence` that pins the
zero-commit-tail-task-produces-no-finding behavior explicitly (today it is
only asserted by a docstring), so this contract can't silently regress.

### 3. Branch-correspondence filter added at the `matches` list, requesting `baseRefName` in the `--json` fields

Change the `gh pr list --search` call's `--json` field list from
`number,state,url,headRefName,isDraft` to `number,state,url,headRefName,
baseRefName,isDraft` (one extra field, no new `gh` call), then filter:
`matches = [m for m in matches if m.get("headRefName") == gb or
m.get("baseRefName") == pr_base]` before taking `matches[0]`. Both `gb`
and `pr_base` are already local variables at that point in `integrate_one`.

Alternative considered: reject on `headRefName` only (ignore
`baseRefName`). Rejected — `pr_base` is exactly the value the group's own
`gh pr create` call below already uses for `--base`, so checking it too
costs nothing and covers a case where an operator manually renamed the head
branch but the PR still targets the group's correct base.

Alternative considered: an exact-match `gh pr list --head <gb>` call
instead of filtering `--search` results. Rejected as a larger behavior
change than needed for this fix — `--search` intentionally also catches
operator-created PRs whose head branch is NOT `gb` (that's the whole point
of "operator PR discovery": a human opened a differently-named branch for
the same group). Narrowing to `--head` would remove that legitimate
discovery path entirely, not just fix the false-positive. Keeping `--search`
and adding a correspondence *filter* preserves the legitimate discovery
case (differently-named head branch, but targeting the group's base) while
rejecting the incident's case (branch belongs to a different workflow
entirely).

## Risks / Trade-offs

- [Decision 1: a worker may still ignore the explicit no-op instruction and
  commit anyway] → Not eliminated by this change (see Non-Goals). Mitigated
  by making the instruction as unambiguous as the existing prompt format
  allows; a worker doing so afterward is caught by the existing failure
  modes (a genuine unmerged-commit finding, human-visible in the dashboard)
  rather than silently producing a duplicate PR unnoticed.
- [Decision 3: an operator-created PR whose base branch was *also* changed
  from the group's original target] → Rejected by this filter (neither
  `headRefName` nor `baseRefName` would match), falling through to `gh pr
  create` and potentially creating a second PR alongside the operator's.
  Accepted trade-off: this is the same failure mode the incident
  demonstrated in reverse (accepting an unrelated PR) is far more costly
  than occasionally creating a redundant PR for a legitimate but
  unrecognizable operator branch, which a human reviewer can close.

## Migration Plan

No data migration. Both changes are pure code/prompt-text changes behind
existing entry points (`build_worker_prompt`, `integrate_one`); no config,
schema, or journal-format changes. Rollback is a plain revert.
