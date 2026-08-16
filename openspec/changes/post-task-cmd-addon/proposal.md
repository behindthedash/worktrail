## Why

Aspens' own post-commit hook runs `doc sync` fully detached/async and never
`git add`s or commits its output. Combined with worktrail's doctrine of
tearing down worktrees the same turn a PR merges, any skill-doc update that
finishes after commit/push (or isn't noticed before teardown) is silently
lost with no signal. The existing `pre_pr_cmd`/`integrate_smoke_cmd` gate
(`router/pre_pr_gate.py`, `orchestrator/integrate.py`) is a pure pass/fail
exit-code check — nothing in it stages or commits file output — so it cannot
be repurposed for this without moving the same data-loss race earlier.
worktrail needs a real place to run a repo-opted-in "produce files, then
commit them into the same PR" step after a task's own commit and before
push/PR creation, generalized as an add-on mechanism (not a one-off aspens
special case) so a future add-on can reuse it without another core-code
change.

## What Changes

- Add a generic, opt-in **worktrail add-on** mechanism: a new `add_ons:`
  block in a repo's `docs/specs/go-policy.yaml` names which add-on(s) are
  enabled and their config. Repos that configure nothing see zero behavior
  change — no add-on step runs, empty or otherwise, for any task in any repo
  that doesn't opt in.
- Add an `AddOn` interface (mirroring the existing `TaskSource` protocol
  pattern in `taskformats/base.py`) with an `install`/`configure`/`run`
  lifecycle, so a future unrelated add-on plugs into the same mechanism
  without touching `integrate.py`/`live.py`/`preflight.py` again.
- Add a `post_task_cmd`-equivalent hook step that runs an add-on's `run()`
  after a task's own commit, stages any file output the add-on produced,
  and commits it (`git add` → `git diff --cached --quiet` → `git commit`,
  the pattern already used by `_write_group_task_status`,
  `integrate.py:274-323`) so updates land in the same PR the task produced
  instead of racing worktree teardown.
- Wire the hook into **both** PR paths:
  - the group-PR path in `integrate.py` (`integrate_one`), after task
    branches are merged into the integration worktree and alongside
    `_write_group_task_status`, before the drift/smoke gates and push;
  - the one-off/single-task path, which is agent/skill-driven
    (`worktrail-sdd-workflow` SKILL.md: agent commits → `worktrail-preflight
    run` → `gh pr create`) rather than orchestrator-driven — the hook runs
    inside `worktrail-preflight run` (`router/preflight.py`), after the
    agent's own commit and before the pass/fail smoke gate and PR creation.
- Add the first add-on, `aspens`, implementing the full lifecycle worktrail
  now owns end-to-end so a repo owner never has to hand-run `npm install -g
  aspens`, `aspens doc init`, or aspens' own `--install-hook`:
  - **Install**: ensure the `aspens` CLI is present/up to date before first
    use.
  - **Configure**: run/verify `aspens doc init` (or equivalent) so
    `.aspens.json` and initial skills exist, per the add-on's config (or
    sane defaults).
  - **Set up**: never install aspens' own async post-commit `--install-hook`
    — that mechanism is exactly what this change replaces.
  - **Maintain**: run `aspens doc sync`/`--refresh` via the new hook after
    each task's own commit, following the commit pattern above.
- Enable the `aspens` add-on in `worktrail`'s own `go-policy.yaml` (dogfood —
  this repo has never run aspens, so this is also its first-time
  `aspens doc init`). Enabling it for `datalena`, `gracefully-giving-back`,
  `mailbox-service`, `kudera-consulting`, and `pullhook` is **not** part of
  this change: this repo's orchestrator can only write within its own
  checkout, so a task here cannot commit another repo's `go-policy.yaml`/
  `.aspens.json` (`worktrail-compile` rejects it outright, and it is a
  different repo under this repo's own PR-scope doctrine besides). Each of
  those five repos gets its own follow-up brief/PR once this framework
  merges — `datalena` and `gracefully-giving-back` need a genuine
  `aspens doc init` re-init over their stale/vestigial `.aspens.json` from a
  prior unadopted run; the other three have never run aspens and need a
  first-time `aspens doc init` as part of enabling.

## Capabilities

### New Capabilities
- `post-task-addon-framework`: the opt-in, per-repo `add_ons:` policy
  config, the `AddOn` interface, and the stage-and-commit hook invocation
  wired into both the group-PR (`integrate.py`) and one-off
  (`router/preflight.py`) paths. No add-on runs for a repo that does not
  configure one.
- `aspens-addon`: the concrete `aspens` add-on built on the framework —
  install/configure/set-up/maintain lifecycle, never installing aspens' own
  post-commit hook, and its enablement across the six named repos.

### Modified Capabilities
(none — no existing `openspec/specs/` capability governs policy loading,
`integrate_one`, or `worktrail-preflight`; this change only adds new,
additive behavior gated behind an opt-in config key)

## Impact

- `src/worktrail/router/policy.py`: new `add_ons` key in `DEFAULTS`/
  `KNOWN_KEYS` (default `None`/empty — no behavior change for unconfigured
  repos).
- `src/worktrail/addons/` (new package): `base.py` (`AddOn` protocol,
  mirroring `taskformats/base.py`), `resolve.py` (name → add-on dispatch,
  mirroring `taskformats/resolve.py`), `aspens.py` (first concrete add-on),
  and the shared stage-and-commit runner.
- `src/worktrail/orchestrator/integrate.py`: `integrate_one` gains an
  add-on-hook call after task-branch merge, before the drift/smoke gates.
- `src/worktrail/router/preflight.py`: `worktrail-preflight run` gains the
  same add-on-hook call after the agent's own commit, before the pass/fail
  gate.
- `docs/specs/go-policy.yaml` in this repo (`worktrail`): new `add_ons:`
  block enabling `aspens`. (`datalena`, `gracefully-giving-back`,
  `mailbox-service`, `kudera-consulting`, `pullhook` get the same change via
  their own follow-up PRs, out of scope here — see What Changes.)
- Tests: `tests/router/test_policy.py` (new key), new
  `tests/addons/test_aspens.py` and `tests/addons/test_base.py`,
  `tests/orchestrator/test_integrate_extras.py` (group-PR hook wiring),
  `tests/router/test_preflight*.py` (one-off hook wiring).
