## 1. Policy support

- [ ] 1.1 Add `add_ons` to `DEFAULTS` in `src/worktrail/router/policy.py` (default `{}`) and to `KNOWN_KEYS`, alongside the existing `pre_pr_cmd`/`integrate_smoke_cmd` keys. Implements Requirement: Add-ons are opt-in per repo (a repo with no `add_ons:` key gets the `{}` default, so the runner in 3.1 iterates zero entries).
- [ ] 1.2 Add/extend `tests/router/test_policy.py` to assert: `add_ons` defaults to `{}`, a configured `add_ons:` block round-trips through `load_policy`, and it is not reported in `unknown_keys`.

## 2. Add-on interface and resolver

- [ ] 2.1 Create `src/worktrail/addons/__init__.py` and `src/worktrail/addons/base.py` defining the `AddOn` `Protocol` (`name`, `install(ctx)`, `configure(ctx)`, `run(ctx) -> AddOnResult`) and an `AddOnResult` dataclass (`changed: bool`, `detail: str`, `paths: list[Path]`), mirroring `src/worktrail/taskformats/base.py`'s docstring/seam pattern. Implements Requirement: Add-ons are pluggable behind a common interface.
- [ ] 2.2 Create `src/worktrail/addons/resolve.py` with an `addon_for(name: str) -> AddOn` if/elif dispatch (mirroring `taskformats/resolve.py:73-82`'s `task_source_for`) that raises a clear error for an unresolved name (per design D5). Implements Requirement: Unknown add-on names fail closed.
- [ ] 2.3 Add `tests/addons/test_base.py` and `tests/addons/test_resolve.py` covering: known name resolves, unknown name raises with the name in the message.

## 3. Shared stage-and-commit runner

- [ ] 3.1 Create `src/worktrail/addons/runner.py` with `run_addons(worktree: Path, repo: Path, policy: dict) -> list[AddOnRunLog]`: for each enabled entry in `policy["add_ons"]`, resolve the add-on, call `install`/`configure`/`run` with a bounded timeout (named constant, mirroring `SMOKE_TIMEOUT_DEFAULT`), then stage and commit any changed paths using the `git add` → `git diff --cached --quiet` → `git commit -q -m "chore(<name>): <summary>"` sequence from `_write_group_task_status` (`integrate.py:274-323`). Implements Requirement: Add-on output is staged and committed before push.
- [ ] 3.2 Implement non-fatal-by-default failure handling per design D4: catch `TimeoutExpired`/`OSError`/add-on exceptions, log and continue unless `policy["add_ons"][name].get("required")` is true, in which case propagate as a blocking failure. Implements Requirement: Add-on failures do not block delivery by default.
- [ ] 3.3 Add `tests/addons/test_runner.py` covering: changed files get committed with the expected message prefix, no-op run produces no commit, non-fatal failure is swallowed and logged, `required: true` failure propagates.

## 4. Group-PR path wiring

- [ ] 4.1 In `src/worktrail/orchestrator/integrate.py`'s `integrate_one`, call `addons.runner.run_addons(iw, repo, policy)` after the per-task merge loop and alongside `_write_group_task_status` (~`integrate.py:1076`), before `_run_drift_gate` (~1085) and `_run_integration_smoke` (~1088). Implements (group-PR half of) Requirement: Hook runs in both PR paths.
- [ ] 4.2 On a `required` add-on failure, quarantine the group the same way an existing failing drift/smoke gate does (`quarantined[name] = ...`, matching the pattern at `integrate.py:1088-1094`).
- [ ] 4.3 Extend `tests/orchestrator/test_integrate_extras.py` (or add `tests/orchestrator/test_integrate_addons.py`) covering: an unconfigured repo's group integration produces no add-on commit; a configured add-on's output is committed before push; a `required` add-on failure quarantines the group.

## 5. One-off path wiring

- [ ] 5.1 In `src/worktrail/router/preflight.py`'s `run` command, call `addons.runner.run_addons(worktree, repo, policy)` after the agent's own commit and before invoking `pre_pr_gate.py`'s pass/fail check. Implements (one-off half of) Requirement: Hook runs in both PR paths.
- [ ] 5.2 On a `required` add-on failure, fail `worktrail-preflight run` the same way an unconfigured/failing `pre_pr_cmd` fails it today, so the existing PreToolUse `gh pr create` guard (`router/preflight.py` `check()`) blocks the PR the same way it would for a failed smoke gate.
- [ ] 5.3 Extend `tests/router/test_preflight.py` covering: unconfigured repo sees no add-on invocation; configured add-on's output is committed before the pass/fail gate runs; `required` add-on failure fails the preflight run.

## 6. aspens add-on

- [ ] 6.1 Create `src/worktrail/addons/aspens.py` implementing `AddOn`: `install()` ensures the `aspens` CLI is present, checking a machine-local marker (`~/.cache/worktrail/addons/aspens/last-check`, default 24h interval per design D7) before re-checking/updating. Implements Requirement: aspens CLI availability is worktrail's responsibility.
- [ ] 6.2 Implement `configure()`: run `aspens doc init` (with the add-on's configured target/backend, or sane defaults) only when `.aspens.json` is absent; leave an existing `.aspens.json` untouched. Explicitly do not call aspens' own `--install-hook`. Implements Requirement: aspens is configured, not left uninitialized and Requirement: aspens' own post-commit hook is never installed.
- [ ] 6.3 Implement `run()`: execute `aspens doc sync` (or `--refresh`), following the `subprocess.run` house style (`shell=True`/explicit list, `cwd=worktree`, `capture_output=True, text=True`, named timeout) from `integrate.py:651-658`, and report changed paths for the shared runner to stage/commit. Implements Requirement: aspens sync runs and commits after each task.
- [ ] 6.4 Register `aspens` in `addons/resolve.py`'s dispatch.
- [ ] 6.5 Add `tests/addons/test_aspens.py` covering: install skips when marker is fresh, install runs when marker stale/missing, configure is a no-op when `.aspens.json` exists, configure initializes when absent, run never invokes `--install-hook`, run reports changed paths for a successful sync.

## 7. Plugin/CI surface

- [ ] 7.1 [cleanup] Confirm `tests/test_plugin_surface.py` still passes unmodified (no new skill/command surface is introduced by this change — the add-on mechanism is internal to `integrate.py`/`preflight.py`). Verification-only: no files are changed by this task.
- [ ] 7.2 [cleanup] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` and confirm both are green. Verification-only: no files are changed by this task.

## 8. Rollout: enable the aspens add-on in this repo (dogfood)

- [ ] 8.1 Enable `add_ons: { aspens: {...} }` in this repo's (`worktrail`) own `docs/specs/go-policy.yaml`, running a first-time `aspens doc init` as part of enabling; verify a real task's sync commit lands in its own PR (dogfood, per design Migration Plan step 3).

Enabling the `aspens` add-on in `datalena`, `gracefully-giving-back`, `mailbox-service`,
`kudera-consulting`, and `pullhook` is **not** part of this change: this repo's
orchestrator can only commit within its own checkout (`$SPEC_ROOT`), so a task here
cannot write another repo's `go-policy.yaml`/`.aspens.json` — `worktrail-compile`
rejects any attempt (`file path outside the repo`), and it is also a different-repo
change under this repo's own PR-scope doctrine (`CLAUDE.repo.md` §PR Scope
Discipline). Each of those five repos gets its own follow-up brief once this
framework merges, scoped to a `go-policy.yaml` change in that repo's own worktree/PR
(design Migration Plan steps 4-5 describe the intended per-repo rollout order, not a
single cross-repo task list).
