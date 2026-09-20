# automerge-preflight-pr-target-remote Specification

## Purpose
TBD - created by archiving change automerge-preflight-resolve-pr-target-remote. Update Purpose after archive.
## Requirements
### Requirement: Preflight resolves the PR target remote
`owner_repo_from_git` SHALL derive `owner/repo` from the remote the PR will be opened against:
an explicit `remote` argument when given, otherwise the value of
`git config --get remote.pushDefault`, otherwise `origin`. The GitHub URL parsing (HTTPS and
SCP-style SSH forms) SHALL apply to whichever remote is selected. When the selected remote does
not exist or is not a GitHub URL, the function SHALL return None and `required_checks_gate`
SHALL refuse with a reason that names the remote it consulted.

#### Scenario: Fork layout reads the fork
- **WHEN** a repo has `origin` = `https://github.com/upstream/proj.git`, `fork` =
  `git@github.com:me/proj.git`, and `remote.pushDefault` = `fork`
- **THEN** `owner_repo_from_git` returns `me/proj` and `required_checks_gate` queries
  `repos/me/proj/rules/branches/<branch>` and `repos/me/proj`, never `upstream/proj`

#### Scenario: Unset pushDefault falls back to origin
- **WHEN** a repo has no `remote.pushDefault` and `origin` = `https://github.com/acme/widgets.git`
- **THEN** `owner_repo_from_git` returns `acme/widgets`, unchanged from today

#### Scenario: Explicit remote overrides pushDefault
- **WHEN** `owner_repo_from_git(repo, remote="upstream")` is called on a repo whose
  `remote.pushDefault` is `fork`
- **THEN** the URL of `upstream` is parsed and `pushDefault` is not consulted

#### Scenario: pushDefault names a missing remote
- **WHEN** `remote.pushDefault` = `fork` but `git remote get-url fork` fails
- **THEN** `owner_repo_from_git` returns None and `required_checks_gate` returns
  `(False, reason)` where `reason` names `fork` and `is_preflight_query_error(reason)` is False

### Requirement: Orchestrator preflight adapter covers every git call
`verify._preflight_runner` SHALL rewrite every `git ...` command issued by
`automerge_preflight` to `git -C <repo> ...`, not only `git remote get-url`, so the
`remote.pushDefault` read runs against the group's repo rather than the verifier's cwd.

#### Scenario: git config read is scoped to the repo
- **WHEN** `_preflight_runner(["git", "config", "--get", "remote.pushDefault"])` is called
- **THEN** the injected runner receives `["git", "-C", "<repo>", "config", "--get",
  "remote.pushDefault"]`

