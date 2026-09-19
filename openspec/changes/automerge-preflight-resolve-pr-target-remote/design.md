## Context

The preflight gate answers "does the base branch of the repo this PR targets have required
checks and auto-merge enabled". It derives `owner/repo` from `git remote get-url origin`. The
push side of the same pipeline (`land_pr._push_target`, `queue_triage._push_target`) already
picks the PR target as `remote.pushDefault` else `origin`, so in a fork layout the two halves
disagree: the branch is pushed to and the PR opened on the fork, the gate inspects upstream.

## Goals / Non-Goals

- Goal: the gate reads the same repo the PR is opened against, using the rule the push side
  already uses, with no new policy surface.
- Goal: keep fail-closed posture: an unresolvable remote is still "not eligible".
- Non-goal: a `--remote` CLI flag on `automerge_preflight.py` or a `routing.yaml`/policy knob.
  The brief listed those as options; `remote.pushDefault` is the standard git knob the repo
  already honours, so adding a parallel worktrail-specific setting would create a second place
  for the two halves to drift apart. The function accepts an explicit `remote=` for callers
  that already know their target (the orchestrator has `self.remote`), which is enough.
- Non-goal: land-pr's fork PR "merged externally" misreport (PR #1265).

## Decisions

- **Resolution order**: explicit `remote=` argument, else `git config --get remote.pushDefault`,
  else `origin`. This is `_push_target`'s rule verbatim; the helper is not shared because
  `land_pr` and `queue_triage` each deliberately re-derive it to stay within their file scope.
- **Where it lives**: inside `owner_repo_from_git`, so `check_review_threads` and `pr_labels`
  (which reuse the helper for bare PR numbers) are fixed by the same change with no edits.
- **Orchestrator adapter**: `verify._preflight_runner` currently rewrites only
  `["git", "remote", "get-url", ...]` to add `-C <repo>`; it must rewrite any `git` invocation
  from the helper, otherwise the new `git config` read runs in the verifier's cwd and reads the
  wrong repo's (or no) `pushDefault`.
- **Error text**: the gate's unresolved-remote reason names the remote consulted
  (`... from the git '<remote>' remote`) so a fork-layout misconfiguration is diagnosable
  from the run record.

## Risks / Trade-offs

- A repo with `remote.pushDefault` pointing at a remote that no longer exists now fails the gate
  where it previously (wrongly) passed via `origin`. This is the fail-closed posture and the
  same state already breaks the push step, so it surfaces nothing new.
