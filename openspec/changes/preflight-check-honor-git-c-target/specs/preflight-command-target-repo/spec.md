## ADDED Requirements

### Requirement: Preflight check gates the repo the command targets
`worktrail-preflight check` SHALL resolve its gate target from the gated command when that
command is a `git` invocation whose global options redirect the repository (`-C`, `--git-dir`,
`--work-tree`, in both `--opt value` and `--opt=value` forms), and SHALL use `--repo` otherwise.
Resolution SHALL replay the parsed global options to `git ... rev-parse --show-toplevel` so that
composed `-C` options and a `--git-dir` naming a linked worktree's private git dir resolve the
same way git itself resolves them. Every part of the verdict -- dirty-tree refusal, policy and
docs-only resolution, and pass-marker lookup -- SHALL be computed against the resolved target.

#### Scenario: git -C push is gated against the named worktree
- **WHEN** `check(repo=<cwd-repo>, command="git -C /w/feature push")` runs and `/w/feature` has
  a pass marker matching its own clean tree while `<cwd-repo>` has none
- **THEN** the verdict is `allow` with reason "pass marker matches current tree", and
  `target_repo` is `/w/feature`

#### Scenario: git -C push is refused for a dirty named worktree
- **WHEN** `check(repo=<clean-repo-with-valid-marker>, command="git -C /w/feature push")` runs
  and `/w/feature` has uncommitted changes to tracked files
- **THEN** the verdict is `deny` with the dirty-tree reason, and `target_repo` is `/w/feature`

#### Scenario: --git-dir of a linked worktree resolves to that worktree's root
- **WHEN** the command is `git --git-dir=<main>/.git/worktrees/feature push` and that linked
  worktree's root is `/w/feature`
- **THEN** the resolved target is `/w/feature`, not `<main>` and not the git dir's parent

#### Scenario: composed -C options follow git's own composition
- **WHEN** the command is `git -C /w -C feature push`
- **THEN** the resolved target is the repo root reached from `/w/feature`

### Requirement: Non-redirecting commands keep the --repo target
`worktrail-preflight check` SHALL leave its target as `--repo`, and SHALL omit `target_repo`
from the verdict, whenever the gated command does not redirect the repository. This SHALL
include: no `--command` at all; a command whose first token is not `git`; a `git` command with
no repo-redirecting global option; a `-C` appearing only after the git subcommand; a command
`shlex.split` cannot tokenize; and a redirect naming a path that does not exist or is not a git
repository. No resolution failure SHALL cause `check` to error or to change its exit code
contract.

#### Scenario: bare git push is unaffected
- **WHEN** `check(repo=<repo>, command="git push -u origin HEAD")` runs
- **THEN** the verdict is computed against `<repo>` and carries no `target_repo` key

#### Scenario: gh pr create is unaffected
- **WHEN** `check(repo=<repo>, command="gh pr create --title x --label go:risk-low")` runs
- **THEN** the verdict is computed against `<repo>`, the existing required-label check applies
  unchanged, and no `target_repo` key is present

#### Scenario: unparseable command falls back to --repo
- **WHEN** the command cannot be tokenized by `shlex.split` (e.g. an unbalanced quote from a
  heredoc body)
- **THEN** the verdict is computed against `<repo>` without raising

#### Scenario: -C naming a non-repo falls back to --repo
- **WHEN** the command is `git -C /tmp/not-a-repo push`
- **THEN** the verdict is computed against `<repo>` and carries no `target_repo` key
