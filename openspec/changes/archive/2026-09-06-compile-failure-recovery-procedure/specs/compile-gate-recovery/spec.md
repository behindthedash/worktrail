## ADDED Requirements

### Requirement: The compile step is a documented pre-launch gate
The orchestrator pre-launch gate documentation SHALL carry a dedicated, anchored section for
the run-plan compile step, sibling to the already-implemented and precheck gate sections, so
that the compile gate is discoverable and citable by anchor rather than described only inside
one caller's shell snippet.

Every site that runs the compile step before launching the orchestrator or pushing a spec
branch -- the orchestrator invocation block and each pipeline's scope-check step -- SHALL
direct the reader to that section on a failure instead of restating a partial procedure or
telling the reader to inspect the command's output unaided.

#### Scenario: The gate has its own section
- **WHEN** the pre-launch gate documentation is read
- **THEN** it contains an anchored compile-gate section alongside the other pre-launch gates,
  and that anchor resolves within the skill bundle

#### Scenario: A caller defers to the gate section
- **WHEN** a documented caller's compile invocation exits non-zero
- **THEN** the surrounding prose names the gate section as the recovery procedure, and no
  caller instructs the reader merely to inspect the error output before retrying

### Requirement: Compile failure recovery names an action per failure class
The compile gate section SHALL enumerate the compile step's distinct failure classes and give
each one its own recovery action. The enumeration SHALL cover, at minimum: a plan-shape
rejection (serial chain, same-file dependent chain, implementation task lacking test scope,
verification-bodied cleanup task); tasks left with no file scope; files claimed by two tasks
with no ordering between them; requirements no task covers; an unusable spec path or a
directory outside a git repository; and a refused forced recompile while task worktrees for
that spec already exist.

The section SHALL state which classes are authoring defects in the change's own task list --
ones a re-run of the compile step cannot resolve -- and SHALL direct the reader to edit the
task list for those rather than to retry. It SHALL also cover the case where the compile step
exits zero but records a degraded plan, and SHALL state that this case is invisible to a
non-zero-exit check and must be read from the compile step's own output.

#### Scenario: A plan-shape rejection is not answered with a retry
- **WHEN** the compile step rejects a plan for its shape
- **THEN** the documented action is to edit the change's task list as the rejection line
  instructs, and re-running the compile step unchanged is explicitly not the response

#### Scenario: A scope gap is distinguished from a shape rejection
- **WHEN** the compile step reports tasks with no file scope
- **THEN** the documented action names the three remedies the diagnostic itself offers --
  declaring file scope, tagging the task with the tail kind matching what it executes, or
  recompiling with more context in the change's supporting documents

#### Scenario: A silent degrade is caught
- **WHEN** the compile step exits zero having fallen back to the artifact's own dependencies
- **THEN** the section states that the exit status alone does not detect this, names the
  output that does, and gives the action for it

### Requirement: A compile failure has an unattended fallback
The compile gate SHALL document its unattended-mode behaviour both in-section and as a
per-site entry in the route-execution auto-mode fallback list, consistent with the other
pre-launch gates. An unattended run SHALL NOT decide how to repair a task list it did not
author: on a failure class that is an authoring defect it SHALL finish as a blocked product
decision quoting the compile output, leaving the claimed brief claimed rather than completing
or releasing it. A failure class with a safe documented default SHALL take that default
silently and record it on the run record rather than blocking.

#### Scenario: An unattended run hits a plan-shape rejection
- **WHEN** the compile gate fails on an authoring defect with unattended mode set
- **THEN** the run finishes as a blocked product decision quoting the compile output, no
  question is asked, and the brief remains claimed

#### Scenario: The fallback list carries the site
- **WHEN** the route-execution auto-mode fallback list is read
- **THEN** it contains an entry for the compile gate naming its unattended outcome, as it does
  for the other pre-launch gates

### Requirement: The compile gate documentation is enforced
An automated test SHALL fail the build when the compile gate section is missing, when it is
not placed among the pre-launch gates, when any enumerated failure class is absent from it,
when a documented caller reverts to telling the reader to inspect the error output unaided, or
when the auto-mode fallback list loses its compile-gate entry.

#### Scenario: The section is deleted or renamed
- **WHEN** the compile gate anchor no longer exists in the pre-launch gate documentation
- **THEN** the test fails naming the missing anchor

#### Scenario: The bare retry wording returns
- **WHEN** a caller's compile failure branch is edited back to instructing the reader only to
  inspect the error before retrying
- **THEN** the test fails naming that site
