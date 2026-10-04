## ADDED Requirements

### Requirement: Base branch resolution for fold and propose applies

After a `fold-into-change` or `propose-change` verdict's brief is claimed, and before the
base-branch fetch or the worktree creation, `apply --confirm` SHALL resolve the base branch
name it fetches and branches from, against the same push remote it already resolves for
that fetch, the unpushed-base check, and the worktree base ref (per "Fold and propose are
applied as a pull request, fail-closed"). The resolution SHALL try three sources in order:
(1) the target repo's policy `base_branch`, when it is a non-empty string; (2) the branch
named by the checkout's local `refs/remotes/<remote>/HEAD` ref; (3) the branch the remote
reports for its own `HEAD` symref, read over the wire as `git ls-remote --symref <remote>
HEAD` and taken from its `ref: refs/heads/<name>` line, so a repo whose default branch is
not `main` resolves even when its checkout carries no local remote-HEAD ref. The resolution
SHALL NOT fall back to `main`, or to any branch name no source named, when all three
sources fail. When no source resolves a base branch, the apply SHALL fail closed before any
fetch or worktree creation, with a `status: "error"` action-log entry whose error names the
repo's `base_branch` policy key as the remedy and carries the branch name the apply would
have used, and SHALL release the claimed brief back to `queue/` unmodified, matching the
failure shape of every other pre-PR failure in this path. When the resolved base branch's
fetch fails, the reported error SHALL additionally name the resolved `<remote>/<base
branch>` and the `base_branch` policy key as the remedy for a missing or misspelled base
branch, alongside the underlying git failure. All three sources are consulted only after a
successful claim: a brief observed as already claimed SHALL reach none of them and SHALL
return the concurrent-claim error as before.

#### Scenario: A non-main default resolves without local state

- **WHEN** `apply --confirm` executes `fold-into-change` or `propose-change` against a repo
  whose policy sets no `base_branch` and whose checkout has no local
  `refs/remotes/origin/HEAD`, and `git ls-remote --symref origin HEAD` answers `ref:
  refs/heads/dev`
- **THEN** the apply runs `git fetch origin dev`, creates the worktree from `origin/dev`,
  never fetches or branches off `origin/main`, and reports no base-branch-resolution error

#### Scenario: The local remote-HEAD ref answers without a wire probe

- **WHEN** the policy sets no `base_branch` and the checkout's `refs/remotes/<remote>/HEAD`
  names `dev`
- **THEN** the apply resolves `dev`, issues no `ls-remote` call, and fetches `dev`

#### Scenario: An explicit policy base_branch wins

- **WHEN** the repo's policy sets `base_branch: dev` and the remote's default branch is
  `main`
- **THEN** the apply resolves `dev` without consulting either remote-HEAD source, and the
  fetch, the unpushed-base check, and the worktree base ref all use `<remote>/dev`

#### Scenario: An unresolvable base branch fails closed before any ref work

- **WHEN** the policy sets no `base_branch`, the checkout has no
  `refs/remotes/<remote>/HEAD`, and the `git ls-remote --symref <remote> HEAD` query fails
  or reports no default branch
- **THEN** the action-log entry reports `status: error` with an error naming the repo's
  `base_branch` policy key, no fetch and no worktree creation is issued, the claimed brief
  is back in `queue/` unmodified, and the entry carries the branch name the apply would
  have used

#### Scenario: Fetch failure names the policy-key remedy

- **WHEN** the resolved base branch's `git fetch <remote> <base branch>` fails
- **THEN** the error reports the underlying git failure, the resolved `<remote>/<base
  branch>`, and the repo's `base_branch` policy key as the remedy, and the claimed brief is
  released back to `queue/` unmodified

#### Scenario: A fork-configured checkout resolves from the push remote

- **WHEN** `remote.pushDefault` is `fork`, the policy sets no `base_branch`, and the
  checkout has no local `refs/remotes/fork/HEAD`
- **THEN** the `ls-remote --symref` probe runs against `fork` and never `origin`, and the
  branch it resolves drives the `fork/<base branch>` fetch and worktree base ref

#### Scenario: An already-claimed brief performs no base-branch work

- **WHEN** a second `apply --confirm` observes the brief already claimed by a concurrent run
- **THEN** it issues no `symbolic-ref`, `ls-remote`, or `fetch` call, and returns the
  concurrent-claim error naming that run, as before
