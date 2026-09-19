## Context

`check(repo, command)` today uses `repo` for every decision (dirty tree, policy, docs-only,
marker) and uses `command` only for the `gh pr create` label check. The gated command can name a
different repo than `repo` via git's repo-redirecting global options, and the caller (a hook that
sees a shell command string, not a resolved path) cannot reliably resolve it -- which is why the
human decision put the parsing here.

## Goals / Non-Goals

- Goal: a `git -C <path> push` is gated against `<path>`.
- Goal: no behaviour change for any command that does not redirect the repo.
- Non-Goal: changing the devops hook (a separate repo, tracked as a companion handoff).
- Non-Goal: redirecting `record-pr`, `run`, or `wait`; only `check` receives a gated command.

## Decisions

**Resolve via `git rev-parse --show-toplevel`, not path arithmetic.** The parsed options are
replayed to git itself (`git -C a -C b --git-dir=d rev-parse --show-toplevel`) and the answer is
whatever git says the worktree root is. This gets `-C` composition, `--git-dir` for a linked
worktree (whose private git dir is `<main>/.git/worktrees/<name>`, not a sibling of its root),
and `--work-tree` right for free, instead of re-deriving three sets of git semantics.
Alternative rejected: treating `--git-dir=<d>` as "worktree is `<d>`'s parent", which is wrong
for every linked worktree -- precisely the case in the reproduction.

**Fail back to `--repo`, never error.** An unparseable command (`shlex.split` raises -- the same
failure mode `is_unparseable_command()` already names), a path that does not exist, or a
non-repo target all fall back to `--repo`. The gate then behaves exactly as it does today;
making the gate itself fail on a malformed command would convert a parsing quirk into a hard
block on pushing.

**Only `git` commands redirect.** `gh` has no `-C`; a `gh pr create --repo owner/name` names a
GitHub slug, not a local tree, and cannot be gated locally. The first token must be `git`, and
parsing stops at the first non-option token, which is the git subcommand -- so a `-C` appearing
*after* the subcommand is not mistaken for a global option.

**`target_repo` is present only when redirected.** Callers keying off its presence get an
unambiguous signal; an always-present key would just restate `--repo` and add noise to every
existing verdict.
