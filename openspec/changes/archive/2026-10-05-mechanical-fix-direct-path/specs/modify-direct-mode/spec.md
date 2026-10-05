## Purpose

Define a direct execution mode for the modify pipeline: a change that reduces to one
mechanical fix is implemented inline in its change worktree instead of paying a full
orchestrated run, gated by a code-enforced eligibility predicate so the branch decision is
never prose-remembered.

## ADDED Requirements

### Requirement: Direct-mode eligibility gate is an executable verdict

The direct-mode gate SHALL be a registered console script (`worktrail-modify-direct-gate`)
that accepts one change directory and computes a deterministic, read-only eligibility
verdict: no model calls, no network, no writes to the repository. With `--json` it SHALL
print exactly `{"eligible": bool, "reason": string, "task_count": int, "files": [...]}`,
where `files` is the sorted list of declared file paths across the change's tasks. The exit
status SHALL be 0 whenever a verdict is computed — `eligible: true` and `eligible: false`
both exit 0, so a nonzero exit never encodes ineligibility (the verdict convention the
route classifier already follows) — and a usage error (missing or unreadable change
directory) SHALL exit 2. The `reason` string SHALL begin with a stable condition token —
`eligible` for an eligible verdict, otherwise the failing condition's token from the fixed
set `change_shape`, `task_count`, `files_undeclared`, `files_over_cap`, `routing_surface`,
`delta_over_cap` — followed by the detail that explains it, naming the observed value
against its cap where one applies.

#### Scenario: An eligible change produces the full verdict shape

- **WHEN** the gate runs with `--json` against a change directory whose single task declares
  a small in-surface file set and whose delta specs are small
- **THEN** it prints `{"eligible": true, "reason": "eligible: ...", "task_count": 1,
  "files": [...]}` with the declared files sorted, and exits 0

#### Scenario: An ineligible change is a verdict, never an error

- **WHEN** the gate runs against a change that fails any eligibility condition
- **THEN** it prints `"eligible": false` with a `reason` carrying the failing condition's
  token and its detail, and exits 0 — no nonzero exit is ever used for ineligibility

#### Scenario: A usage error exits 2, not a verdict

- **WHEN** the gate is invoked with a change directory that does not exist or is not a
  directory
- **THEN** it exits 2 with a diagnostic and prints no verdict object

### Requirement: Eligibility conditions are capped by named constants

The gate SHALL declare `eligible: true` only when every one of the following holds,
evaluated in this fixed order so the reported reason is deterministic:

(a) **Change shape** — the change directory contains `tasks.md` and at least one delta
spec under `specs/**/spec.md`;
(b) **Task count** — the change has exactly 1 task;
(c) **Declared files** — that task declares at least one file, and the number of distinct
declared files is at most the file cap (3);
(d) **Routing surface** — no declared file equals or lies under any entry of the
routing/classification surface: the route classifier and its classification-support
modules, the risk and policy modules, and the routing cassette locations;
(e) **Delta size** — the estimated changed lines are at most the changed-line cap (40),
where the estimate is the sum, over the change's delta spec files, of their non-blank
lines not present in the corresponding base spec (`openspec/specs/<capability>/spec.md`
beside the change's `openspec/changes/` root); a base spec that does not exist counts
every delta line as new.

The caps and the routing-surface list SHALL be named constants in the gate module — one
place, no configuration knobs. The `reason` for an ineligible verdict SHALL name the
first failing condition with its observed value.

#### Scenario: The motivating one-task mechanical fix is eligible

- **WHEN** the gate reads a change with exactly one task, two declared files, a delta that
  restates an existing requirement adding only a few new lines over the base spec, and no
  routing-surface file
- **THEN** `eligible` is true

#### Scenario: More than one task is ineligible with its own reason

- **WHEN** the change's `tasks.md` declares two or more tasks
- **THEN** `eligible` is false with reason token `task_count`

#### Scenario: Over-cap declared files are ineligible with their own reason

- **WHEN** the single task declares more distinct files than the file cap
- **THEN** `eligible` is false with reason token `files_over_cap`, naming the observed
  count against the cap

#### Scenario: An undeclared task cannot be bounded and is ineligible

- **WHEN** the single task declares no files
- **THEN** `eligible` is false with reason token `files_undeclared`

#### Scenario: An over-cap delta is ineligible with its own reason

- **WHEN** the estimated changed lines exceed the changed-line cap
- **THEN** `eligible` is false with reason token `delta_over_cap`, naming the estimate
  against the cap

#### Scenario: A routing or classification touch is always ineligible

- **WHEN** any declared file is, or lies under, an entry of the routing/classification
  surface — including the routing cassette
- **THEN** `eligible` is false with reason token `routing_surface` naming that file,
  regardless of task count, file count, or delta size

#### Scenario: Delta size is measured against the base spec, not raw file length

- **WHEN** a delta spec restates a modified requirement whose lines already exist in the
  base spec and adds only a few new lines
- **THEN** the estimate counts only the lines absent from the base spec, so the change
  stays within the cap even though the delta file itself is longer than the cap

#### Scenario: A missing change artifact is a shape failure

- **WHEN** the change directory has no `tasks.md` or no delta spec file
- **THEN** `eligible` is false with reason token `change_shape`

### Requirement: The modify pipeline branches on the gate's verdict

The modify pipeline (`pipeline-details.md#modify-pipeline`) SHALL run the gate before its
compile step and SHALL read the branch decision exclusively from the gate's `--json`
verdict — the pipeline's prose SHALL name the gate invocation as the decision input and
SHALL NOT instruct deciding by inspection. On `eligible: true` the pipeline SHALL take the
direct branch: the executor implements the change's single task inline in the change
worktree (no orchestrator launch, no worker spawn, no task worktree), runs the same
compile, `openspec validate`, pre-PR gate, and CI the orchestrated path runs, lands
exactly ONE PR via `worktrail-land-pr` carrying the change artifacts together with the
implementation, and then syncs/archives through the same step the orchestrated path uses.
On `eligible: false` the pipeline SHALL run the existing orchestrated path unchanged, with
the gate's reason surfaced — the fallback is never silent.

#### Scenario: Eligible runs implement inline and land one PR

- **WHEN** the gate returns `eligible: true` for a modify-pipeline change
- **THEN** the executor implements the single task in the change worktree, no orchestrator
  is launched and no task worktree is created, and exactly one PR via `worktrail-land-pr`
  carries both the change artifacts and the implementation before the standard sync step

#### Scenario: Ineligible runs the orchestrated path with the reason surfaced

- **WHEN** the gate returns `eligible: false`
- **THEN** the pipeline proceeds through the existing orchestrator launch exactly as
  before this change, and the gate's reason is surfaced to the operator and the run

#### Scenario: The documented decision input is the script

- **WHEN** the modify pipeline's documentation is inspected
- **THEN** it names `worktrail-modify-direct-gate <change-dir> --json` as the branch
  decision's input and does not ask the agent to judge eligibility by reading the change

### Requirement: The chosen mode is recorded on the run record

Every modify-pipeline run SHALL append exactly one run-record `decisions` entry recording
the mode decision: the entry's text SHALL begin with the stable token
`modify-direct-mode:` and SHALL name the chosen mode (direct or orchestrated) together
with the gate's reason. The entry SHALL be recorded before the direct branch begins
implementing, and on the fallback path before the orchestrator is launched, so the audit
trail shows why a run skipped fan-out.

#### Scenario: A direct run records direct and the reason

- **WHEN** a modify run takes the direct branch
- **THEN** its run record gains a `decisions` entry beginning `modify-direct-mode:` that
  names direct mode and carries the gate's reason

#### Scenario: A fallback run records orchestrated and the reason

- **WHEN** a modify run falls back to the orchestrated path
- **THEN** its run record gains a `decisions` entry beginning `modify-direct-mode:` that
  names orchestrated mode and carries the gate's reason

### Requirement: Direct mode relaxes no existing gate

The direct branch SHALL NOT skip or weaken any gate the orchestrated path applies: the
compile scope gate and its committed `.compile-ok` marker, `openspec validate`, the
repository's pre-PR gate, CI, and `worktrail-land-pr`'s own compile re-run at landing all
still run. Direct mode replaces only the orchestrator launch, the task worktrees, and the
worker spawns. The pipeline documentation's direct-branch section SHALL list the retained
gates explicitly and state that changes touching the routing/classification surface can
never ride direct mode.

#### Scenario: Every gate still runs on the direct branch

- **WHEN** a run takes the direct branch
- **THEN** compile (writing and committing `.compile-ok`), `openspec validate`, the
  pre-PR gate, and CI all run before the single PR lands, and `worktrail-land-pr`
  re-runs compile at landing — nothing in the branch's documented steps skips any of them

#### Scenario: The documentation names the retained gates and the exclusion

- **WHEN** the modify pipeline's direct-branch documentation is inspected
- **THEN** it lists the retained gates (compile/`.compile-ok`, `openspec validate`,
  pre-PR gate, CI) and states that a change touching the routing/classification surface
  is never eligible
