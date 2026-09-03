## Context

`create_handoff.py` writes a brief's `repo:` frontmatter verbatim (see
`dashboard.py:_resolve_repo_dir()`'s own docstring, which documents this
exact fact for the same reason it exists). A brief's `repo:` can therefore
be an absolute path, a `~`-relative path, a bare name (`devops`), or an
`owner/name`-style value, depending on how the brief was authored.

`queue_triage.py` already treats `repo` this way loosely in several
apply-independent spots (`_repo_wip_cap`, `_check_repo_archived`, ranking) —
those either tolerate a bad path gracefully (e.g. `load_policy()` on a
nonexistent path just finds no policy file) or are out of this change's
scope per the brief's evidence, which is specific to the two functions that
actually shell out `git -C <repo> worktree add` against the unresolved
value: `_apply_fold_into_change()` and `_apply_propose_change()`. Those are
the only two call sites where a wrong path causes a hard failure (or a
silent wrong-target op) rather than a soft no-match.

`dashboard.py:_resolve_repo_dir(repo, repos_root)` already implements the
exact resolution needed: absolute/`~`-relative paths resolve directly by
`is_dir()`; otherwise the value's basename is looked up under `repos_root`.
`dashboard.py`'s own CLI defaults `repos_root` to `str(Path.home() /
"projects")` when no `--repos` flag is given — the standard workspace
layout this codebase assumes elsewhere (`resolve_repo.py`'s
`is_workspace_root()` doctest-level convention, `~/REPOS.md` + `~/projects/`).

## Goals / Non-Goals

**Goals:**
- Make a bare-name or `owner/name`-style `repo:` value resolve to the
  correct on-disk checkout before `_apply_fold_into_change`/
  `_apply_propose_change` run any worktree/git operation against it.
- Fail closed with a clear `error` action-log entry when the value can't be
  resolved, rather than either crashing on a `git` command or (worse)
  silently operating against an unrelated directory that happens to exist
  at the literal relative path.
- Keep today's only working case — an absolute path `repo:` value, which is
  what every existing test in `test_queue_triage.py` already uses —
  unaffected.

**Non-Goals:**
- Fixing every other `Path(repo)`/`repo` string use in `queue_triage.py`
  (`_repo_wip_cap`, `_check_repo_archived`, `rank_change_candidates`, the
  `evaluate` path's repo-scoped `cwd`). Those are separate call sites with
  separate failure modes (soft no-match, not a hard `git`/filesystem
  failure) and are not what the triaging brief's evidence identified;
  changing them is out of scope here.
- Changing how `repo:` frontmatter is written (`create_handoff.py`) — that
  remains verbatim, matching `_resolve_repo_dir()`'s existing contract.
- Adding a new resolution algorithm — this change reuses
  `dashboard._resolve_repo_dir()` as-is rather than duplicating or
  generalizing it.

## Decisions

**Decision 1: reuse `dashboard._resolve_repo_dir()` directly, imported
by queue_triage.py, rather than re-implementing basename resolution.**
It already has the exact contract needed (absolute/home-relative path
resolves directly, otherwise basename-under-`repos_root`) and is exercised
by existing dashboard tests. `queue_triage.py` already imports private
helpers from sibling modules at the top of the file (e.g.
`from ..orchestrator.integrate import _refresh_pr_labels`), so an
`from ..router.dashboard import _resolve_repo_dir` import is consistent
with this module's existing style.

**Decision 2: `repos_root` is a new parameter threaded from `cmd_apply()`'s
new `--repos-root` flag, not a hidden global/env lookup.** `apply_verdicts()`
already takes `agent` this way from `cmd_apply`'s `--agent`; matching that
shape keeps the function testable without patching module state. Default
value mirrors `dashboard.py`'s own default (`~/projects`) so the two tools
agree on the workspace convention without requiring an operator to pass the
flag explicitly on a standard machine.

**Decision 3: an unresolvable `repo` produces the same `error` action-log
shape the functions already use for "missing repo or target_change"/
"missing repo or proposed_change_name".** No new status vocabulary; the
existing `{"status": "error", "path": None, "error": "..."}" shape already
covers "this verdict can't be applied," and a resolution failure is exactly
that.

**Decision 4: a repo-less group may propose, but only into a repo the
evaluator was shown.** `evaluate_group()` already refuses to let a
`fold-into-change` name a change it never presented; the same closed-list
rule applies to `target_repo` for a `__none__` group. `evaluate_group()`
lists the basenames of the directories under `repos_root` (the same root
`_resolve_repo_dir()` resolves against) in the prompt as `known_repos`, and
`_has_valid_target()` accepts a repo-less `propose-change` only when
`target_repo` is in that list. `fold-into-change` stays invalid for the
group: no candidate changes are ranked without a repo, so there is nothing
it could legally name. `needs-decision` remains the fail-open answer when the
evidence cannot name a repo. For a repo-bearing group the existing behavior
is unchanged: `target_repo` is informational and the group's `repo` wins.

**Decision 5: apply stamps `repo:` on the brief before proposing, using the
resolved bare name, and stamps it even if the PR later fails.** The
evaluator's repo identification is durable evidence about the brief, not
about this one apply run; recording it via `work_queue._set_fm_fields()`
(the same helper `_apply_work_directly()` uses) means the next triage pass —
scheduled or an interactive `worktrail-go BRIEF-ID` pickup — evaluates the
brief in its real repo group with ranked candidates. The stamp happens after
`_resolve_repo_dir()` succeeds and before `_worktree_pr_close()`, so an
unresolvable `target_repo` never stamps anything (fail closed, Decision 3)
while a proposal-PR failure downstream leaves the brief queued but now
correctly attributed. The verdict's effective repo — the group `repo` when
set, else the resolved `target_repo` — is a small local helper shared by
`_apply_propose_change()`, `_propose_change_over_cap()`, and
`_preview_verdict()`, so the WIP cap and the dry-run preview see the same
repo the apply would act on.

## Risks / Trade-offs

- A `repos_root` default of `~/projects` is this operator's/this
  codebase's own convention (already hardcoded as `dashboard.py`'s CLI
  default) — a different machine layout would need `--repos-root` passed
  explicitly. Same trade-off `dashboard.py` already accepts; not new risk
  introduced by this change.
- `_resolve_repo_dir()` matches by basename only, so two sibling checkouts
  that happen to share a basename under different parents would collide.
  Pre-existing behavior of the reused function; not introduced here.

## Migration Plan

No data migration. Existing verdict files and briefs with an absolute-path
`repo:` value are unaffected (that branch of `_resolve_repo_dir()` is
identical to today's `Path(repo)` behavior). No `--repos-root` flag use
required for the default-convention case.

- Listing every directory under `repos_root` in the evaluator prompt costs
  a few dozen tokens per repo-less group and exposes the evaluator to
  non-repo directories (e.g. `*-worktrees` containers). The closed-list
  check plus `_resolve_repo_dir()`'s `is_dir()` keep a wrong pick from
  doing anything harmful; a wrong-but-existing pick lands as a proposal PR
  in the wrong repo, which is reviewable and no worse than today's
  `propose-change` for a mis-attributed repo-bearing brief.

## Open Questions

None.
