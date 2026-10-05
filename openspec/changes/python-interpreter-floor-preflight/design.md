## Context

Worktrail's source is Python 3.14 (`requires-python = ">=3.14"`, PEP 758 `except A, B:`
handlers throughout), and its own gates and `AGENTS.md` recipes are pinned to `python3.14`
(capability `python-314-development-toolchain`). Nothing yet checks the interpreter a host
resolves through PATH. On this host bare `python3` is 3.12.3 against a 3.14 floor: running any
tracked module with it fails at parse time with a message about exception syntax, not about
interpreters. The motivating run (`go-20260930-181818`, brief
`20260930-191011-python-interpreter-floor-preflight`) lost two groups to that failure mode
before anyone could read it as "wrong interpreter".

See `proposal.md` for the full motivation. This document records the decisions behind the
shape of the check and where it is enforced.

## Goals / Non-Goals

**Goals:**

- One loud, actionable message — interpreter, version, path, declared floor, fix — instead of a
  parse error deep in whichever command happened to trip first.
- Repository-agnostic: judge the target repository's declared floor and the interpreters *its*
  commands will resolve, so a consuming repo with a different floor is handled without
  worktrail-specific constants.
- Cheap and side-effect free: stdlib only, no model calls, no network, no repository writes.

**Non-Goals:**

- Verifying commands that resolve their own interpreter (`pytest`, `uv run …`, a console
  script's shebang). The check covers the Python command words the repository declares plus the
  ambient `python3`.
- Parsing or executing the gate commands themselves. The check resolves interpreter *words* and
  probes those interpreters; it never runs `pre_pr_cmd` or any part of it.
- Wiring the pre-PR gate / `worktrail-land-pr` paths. The brief lists them as reuse sites; the
  console script keeps them reachable, but those surfaces run the gate's own pinned command
  words, and the ambient-`python3` rule is about dispatch environments. Wiring them would buy a
  second enforcement point without a second failure mode.
- An environment-variable bypass. A below-floor host is exactly the state the check exists to
  refuse; an escape hatch restores the silent failure it removes.

## Decisions

### Probe versions by executing the interpreter with a floor-agnostic snippet

Each resolved interpreter is asked its version with
`<interpreter> -c "import sys; sys.stdout.write('%d.%d.%d' % sys.version_info[:3])"` under a
bounded timeout. The snippet is deliberately parseable by any interpreter older than the floor —
the failure this check reports is a *parse-time* one, so a probe that is itself 3.14-only would
reproduce it. Alternatives rejected: parsing `python3 --version` output (format varies across
implementations and wrappers can misreport), importing repository code to read
`sys.version_info` (executes repository code and may not parse — the exact trap), and reading
the version in-process (the check must measure the *other* interpreter, by definition).

### The candidate set is gate-command interpreter words plus the ambient python3

In the motivating run the failing commands were not the policy gates — those already say
`python3.14` — but documented recipes and agent commands that use bare `python3`. A check
restricted to gate-command words would have passed on exactly the host that failed, so the
ambient `python3` is load-bearing, not decorative. Gate words are included too: a repository
declaring `>=3.14` and pinning `python3.14` needs that pin verified, since a missing pinned
interpreter is equally opaque ("command not found" inside a detached log). Non-interpreter
command words are not resolved — a version proves nothing about `uv`, `pytest`, or `bash`, and
resolving every word on a PATH is unbounded. Absence is treated asymmetrically: an ambient
`python3` that PATH does not provide is not a finding (its absence already fails loudly), while
a gate-command interpreter that PATH does not provide is, because the declared command cannot
run at all.

### The floor comes from the target repository's `requires-python` lower bound

The check runs for arbitrary target repositories, so it must read *their* declaration, not
worktrail's own `>=3.14`. Only `>=` lower bounds are enforced, and the highest one wins; full
PEP 440 resolution is out of scope. A `requires-python` that declares no `>=` bound (or is
absent, or missing entirely) resolves to "no enforceable floor" and the check passes: refusing a
launch because the checker could not parse a specifier would be a failure the operator cannot
act on, which is the fatal-error-vs-pass discipline this repo already applies elsewhere.

### Enforce at the launch boundary (`worktrail-live`), not in the policy loader

`load_policy()` is a deterministic loader shared by read-only diagnostics (drift checks,
dashboards, the front door's own environment resolution). Failing it would break the commands
that must stay able to *report* a mismatched host, and would turn a loader into a gate.
`worktrail-live` is where "before any worker is launched" is meaningful: `full-real` refuses
before the run lock, worktrees, journal, or any spawn; `precheck` — already the documented
pre-dispatch gate in the dispatch playbook — reports the same findings ahead of its task-DAG
diagnostics so the interactive path stops before it asks the user anything. Prose alone is not
the enforcement: the skill explains the reaction, the code guarantees the refusal.

### Refusal is a hard failure, raised through `full_real` and surfaced as a non-zero exit

`full_real` today signals lock contention by returning an aborted result dict (rc 0, the log
line carrying the signal). A precondition refusal is a different thing: unattended callers such
as `drain`'s resume actions log the spawned command's exit code and treat it as the outcome, and
the motivating failure was precisely an outcome that looked like ordinary work continuing.
`full_real` therefore raises a dedicated `InterpreterFloorError` carrying the report; `live`'s
CLI maps it to a printed report and exit 1, and `precheck` returns 1 with the findings under a
distinct `FAIL (interpreter-floor):` label the playbook can name.

## Risks / Trade-offs

- [A host whose ambient `python3` is below floor while every run-relevant command is pinned]
  → dispatch is refused until the operator fixes PATH. Accepted: this repo's own agent recipes
  use bare `python3`, and the motivating run proved that path is reachable from worker sessions;
  the message states both fixes (fix the ambient interpreter, or pin the affected command).
- [Probe cost] one `shutil.which` plus one bounded subprocess per distinct interpreter, once per
  launch — negligible next to a run, and spent before any model call or worktree.
- [A wrapper named `python3` that misreports its version] → out of scope; it would misreport to
  the gate commands too, and no cheap check can see through it.
- [Guard interfering with existing runs and tests] → inert where no floor is declared, which is
  every current test fixture; CI's interpreter already satisfies this repo's floor. Focused
  tests stub PATH rather than depending on the host's real interpreters.
- [A repository declares a floor no released interpreter satisfies] → the check refuses launches
  with the declared floor in the message, which is the correct outcome for a repository whose
  own contract cannot be met.

## Migration Plan

1. Land the check, its CLI, and focused tests (no wiring; the code is inert until called).
2. Wire `full-real` / `precheck` and update the playbook's `#precheck-gate` reaction text in the
   same change so the documented reaction and the enforced behaviour land together.
3. Rollback consists of reverting the wiring; the CLI and its tests are harmless on their own.
