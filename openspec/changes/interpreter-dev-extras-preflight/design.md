## Context

Worktrail's policy pins its own gates to `python3.14` (`.worktrail/policy.yaml`:
`integrate_smoke_cmd`, `pre_pr_cmd`, `pre_commit_cmd`), and `worktrail-detach launch` gives the
detached orchestrator the caller's PATH unchanged. On this host that word resolves to a uv-managed
3.14 (`/home/briank/.local/bin/python3.14`) which cannot import `pytest`; the repository's
development environment is the gitignored `<repo>/.venv`. Run `go-20261004-093132` (change
`model-tier-routing-env-profile-error-provenance`) failed the integrate smoke twice on
`No module named pytest`, quarantined every group after ~16 minutes of fan-out, and opened no PR
for them; the same command with `.venv/bin` first on PATH passed (7147 passed, 770.79s).

`AGENTS.md` documents both the hazard and the fix; what is missing is a check. See `proposal.md`
for the full motivation. This document records the decisions behind the check's shape and where it
is enforced — in particular why the fix is "verify and refuse" rather than "silently prepend", and
how this composes with the concurrently authored `python-interpreter-floor-preflight`.

## Goals / Non-Goals

**Goals:**

- One loud, actionable message before fan-out — interpreter word, path, missing tool, the gate
  command that named it, and a fix — instead of a per-group quarantine after 16 minutes.
- Repository-agnostic: judge the target repository's own declared gate commands, so a consuming
  repository with a different toolchain is handled without worktrail-specific constants.
- Cheap and side-effect free: stdlib only, no model calls, no network, no repository writes, no
  gate command executed, no repository code imported.

**Non-Goals:**

- Rewriting PATH inside `full-real`, or otherwise choosing an interpreter for the operator. Which
  interpreter should run a repository's gates is a property of that repository's environment; the
  code's job is to make a wrong resolution loud, not to guess a right one. Silently preferring a
  `.venv` would also hide a stale or broken one behind a passing fan-out.
- An environment-variable bypass. A host whose gates cannot run is exactly the state the check
  exists to refuse; an escape hatch restores the failure it removes.
- Re-deciding interpreter *versions*. Below-floor and missing interpreters are
  `python-interpreter-floor-preflight`'s findings (its spec owns "a gate-command interpreter that
  PATH does not resolve is a finding"); this check skips a word it cannot resolve rather than
  double-reporting it.
- Deciding whether a repository's *dev extras* are installed, by name. Mapping distribution names
  to importable module names is unreliable (`ruff`, `build`, `requests` each differ in shape), and
  it is not what the gate asks for — the command itself declares the tool it intends to run.
- Wiring `precheck` or the pre-PR gate / `worktrail-land-pr` paths. `full-real`'s refusal is the
  pre-fan-out enforcement the motivating failure needed, and the launch block's own check gives the
  interactive message; a second enforcement point here would buy no second failure mode. (The
  sibling floor change wires `precheck` because a version mismatch is what an interactive operator
  meets first; a deps mismatch is met at the same block that runs this check.)

## Decisions

### Verify the tools a gate command `-m`-invokes, not a "dev extras" set

The observed failure is `python3.14 -m pytest` and `No module named pytest`. The command states the
tool it needs, so the command is the source of truth: extract each `-m <module>` target (and its
leading `VAR=value` prefix, which the probe replays so a tool supplied via `PYTHONPATH` counts) and
verify that tool. Alternatives rejected: reading `[project.optional-dependencies].dev` and checking
each entry (name→module mapping is unreliable, and a repository may install its extras by other
means); executing the gate command (expensive, side-effecting, and exactly the thing that must not
run on a broken host); and probing the ambient `python3` (not what the gates run — that is the
floor check's surface).

### Locate the module; never import it

`importlib.util.find_spec` on the target's **top-level** name determines availability without
executing the module: locating a top-level name runs no module-level code, while importing
`worktrail.orchestrator.orchestrate` to "check" it would execute repository code as a side effect
of a preflight. Reducing to the top-level name is deliberate for the same reason —
`find_spec("a.b")` imports the parent package `a`, so probing `worktrail.orchestrator.orchestrate`
would run `worktrail/__init__.py`. Probing `worktrail` answers the same question (is the package
importable) with no execution. A probe whose interpreter times out, exits non-zero for a reason
other than absence, or prints nothing readable is reported as *undetermined* — a finding, because
an unreadable probe is not evidence that the gate will run.

### The candidate set is the gate commands' own interpreter words

Both the floor check and this one need the same thing — the Python interpreter words a shell would
resolve out of `pre_pr_cmd` / `pre_commit_cmd` / `integrate_smoke_cmd`, including `VAR=value`
prefixes and chained segments — and `python-interpreter-floor-preflight` is being authored
concurrently with its own implementation of it. This change therefore implements the extraction
locally rather than depending on an unlanded module; consolidating the two into one helper is a
recognised follow-up once both are on `main`, not part of either change.

### Refuse at the launch boundary; put the PATH repair in the documented launch block

`worktrail-live full-real` is where "before any worker is launched" is meaningful, so the refusal
lives there: it runs the check before the `RunLock`, any worktree, the journal, or any spawn, and
raises a dedicated error the CLI maps to a printed report and a non-zero exit (the same shape the
sibling floor change chose, for the same reason — an unattended caller such as `drain` logs the
exit code and treats it as the outcome, and the motivating failure was an outcome that looked like
ordinary work continuing).

The *repair* lives one level up, in the `#orchestrator` launch block, which is the surface the
brief identified as the gap: it prepends `<spec-checkout>/.venv/bin` then `<target-checkout>/.venv/bin`
to PATH when each exists, and then runs the check in the same shell. The ordering is what makes the
prepend safe: the check immediately verifies the resolution the prepend produced, so a stale or
broken `.venv` cannot hide — it becomes a finding before the detach, not a silent choice inside
`full-real`. The detach then inherits that PATH for the orchestrator process, and `full-real`'s own
refusal remains the backstop for every caller that does not go through the block (a hand-run
`worktrail-live full-real`, `drain`, a resume action).

## Risks / Trade-offs

- [A module that locates but fails to import at gate time — a broken package, a binary ABI
  mismatch] → out of scope: the check answers availability, not health. The gate's own failure
  there is loud and names the module, which is the outcome the check exists to reach cheaply.
- [The launch block's prepend prefers a stale `.venv`] → the check runs immediately after the
  prepend in the same shell, so a `.venv` that cannot run the gates is reported before the detach
  rather than used silently; the operator fixes it or drops the prepend.
- [A repository whose gates invoke their tools as scripts or bare commands (`python x.py`, `uv run
  pytest`)] → no `-m` target is extracted and the check is inert there, exactly as the floor check
  is inert with no declared floor. Accepted: those surfaces are not the one that failed, and
  guessing a tool set for them would be the unreliable name-mapping rejected above.
- [Two checks, one seam] → `python-interpreter-floor-preflight` and this change both guard
  `full_real`'s boundary; they are independent findings and compose in sequence (a version failure
  refuses first, since a below-floor interpreter cannot be probed for tools meaningfully). Only the
  small insertion point overlaps, which is an ordinary merge conflict, not a semantic one.
- [Duplicate interpreter-word extraction across the two in-flight changes] → noted above;
  consolidation is a follow-up, and each implementation is a pure function with its own tests.
- [Guard interfering with existing runs and tests] → inert wherever a repository's gate commands
  name no `-m` tool, which is every current test fixture, and CI's own environment satisfies the
  check. Focused tests stub PATH rather than depending on the host's real interpreters.

## Migration Plan

1. Land the check, its CLI, and focused tests (no wiring; the code is inert until called).
2. Wire `full-real`'s refusal in the same change so the enforced behaviour and its message land
   together; update the `#orchestrator` launch block's prepend-and-verify in the same change so the
   documented path and the enforced path agree.
3. Rollback consists of reverting the wiring; the CLI and its tests are harmless on their own, and
   the launch block's prepend is a PATH change with no persisted state.
