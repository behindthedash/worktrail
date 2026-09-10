## Context

Triage evidence comes from two places that both execute inside the target repo's canonical
checkout: `premise_check.run_premise_check()` (mechanical, runs at most one allow-listed
command) and the evaluator agent's own tool calls (prompted by `EVALUATOR_PROMPT_TEMPLATE`).
The canonical checkout's `node_modules` is operator-owned and refreshed by hand; it drifts
from the lockfile whenever a merged PR bumps a dependency and nobody re-runs `npm ci`. The
2026-09-10 incident (vitest 4.1.11 installed, 5.0.0 locked) turned that drift into a
confident, wrong `no candidate change fits` triage note with no record of the cause.

`bootstrap_node_modules.lockfile_matches()` compares two lockfiles byte-for-byte to decide
whether a hardlink clone is safe. It answers a different question (are these two worktrees'
lockfiles identical?) and cannot detect a stale install, so it is not reused.

## Goals / Non-Goals

**Goals:**

- Detect, before any reproduction runs, that an npm package root's installed direct
  dependencies do not match its lockfile.
- Make the mechanical premise check refuse to run `npm test` through a stale tree.
- Put the freshness state in front of the evaluator with an explicit rule about what it may
  and may not conclude from reproduction output, and persist it on the verdict.

**Non-Goals:**

- Installing or refreshing dependencies. The check reads only; fixing the canonical checkout
  stays an operator action, and the triage note now tells them to do it.
- Freshness for Python virtualenvs, Go modules, Cargo, or any non-npm ecosystem. The
  incident and the allow-list's only package-manager command (`npm test`) are both npm.
- Transitive dependency verification. Direct dependencies of the root package are enough to
  catch a runner or framework bump and keep the check to a few dozen file reads.
- Changing the premise check's allow-list or the `work-directly` acceptance rule.

## Decisions

### D1: Compare the lockfile's pinned versions to what is installed, not lockfile to lockfile

For each tracked `package-lock.json` (found with `git ls-files`, so an untracked
`node_modules/.package-lock.json` is never mistaken for a root), read `packages[""]`'s
`dependencies` and `devDependencies` names, look up each `node_modules/<name>` entry's
`version` in the lockfile, and compare it to `<root>/node_modules/<name>/package.json`'s
`version`. A missing install directory or a different version is a mismatch; a lockfile
without a `packages` map (lockfile v1) or an unreadable JSON file makes the root `unknown`.

**Alternative rejected:** comparing `package-lock.json` to npm's hidden
`node_modules/.package-lock.json`. The hidden lockfile is an npm implementation detail,
absent after some install paths, and would still not prove the on-disk package matches.

### D2: Skip only the affected command, not the whole premise check

Quoted-string and path needles are `git grep`/filesystem checks and are unaffected by an
install. `pytest` and the other allow-listed runners do not go through `node_modules`. So
`run_premise_check` gains a `dependency_freshness` argument and skips a command needle only
when it is `npm test` (or starts with it) and any reported package root is not `fresh`. The
skipped needle still counts as the one command "slot" consumed, mirroring the existing
"another command needle already ran" behaviour, and its `detail` names the root and the
mismatched packages so the evaluator sees why it was not run.

### D3: The evaluator gets a rule, not just a fact

The prompt block alone would leave the evaluator free to run `npx vitest` itself and trust
the output, which is exactly what happened. The template therefore states: when any package
root is `stale` or `unknown`, output produced by running that root's tooling is not
reproduction evidence; do not return `stale-close`, `needs-update`, or `work-directly` on
its basis, do not treat a failing run as proof that no candidate change fits, and prefer
`keep` with evidence that names the stale root and the mismatched packages. A `fresh`
block adds nothing to the evaluator's obligations.

### D4: Persist alongside `premise_check`

`evaluate_group()` already returns `premise_by_brief` and `parse_verdicts()` copies it onto
`Verdict.premise_check`. The freshness results follow the same route as a group-level list
(`dependency_freshness` on the result dict and on each `Verdict`, `[]` for the no-repo
group and the archived short-circuit). This keeps the change additive: every existing caller
of `parse_verdicts` that omits the new argument gets `[]`.

## Risks / Trade-offs

- A repo whose direct dependencies are legitimately installed at a different version than
  the lockfile (a local override) will be reported stale on every triage pass and its
  `npm test` needle never runs. Accepted: that state is exactly the one the check exists to
  surface, and the evaluator can still `keep`.
- Lockfile v1 files report `unknown` and are treated as not fresh. Accepted: npm 7+ has
  written v2/v3 since 2021, and refusing to trust an unverifiable tree is the safe default.
