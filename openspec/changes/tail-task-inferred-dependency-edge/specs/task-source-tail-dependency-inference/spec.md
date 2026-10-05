## Purpose

Keeps a tail-kind (`e2e`/`cleanup`) task's inferred dependency set honest about the change it
verifies, so an edge the tail-dispatch gate reads is never missing merely because the task
that produces the artifact it needs lives further down `tasks.md`.

## ADDED Requirements

### Requirement: Tail-kind baseline dependencies cover every non-tail task in the change
A task source SHALL give a tail-kind (`e2e`/`cleanup`) task inferred dependencies that
include every non-tail task of the change, regardless of where that task appears in file
order and regardless of which group it belongs to. When the tail task has a within-group
predecessor, that predecessor SHALL remain in the inferred set as well.

#### Scenario: Producer appears in a later group
- **WHEN** a tail-kind task in group 1 declares no `depends:` and a non-tail task in group 2 appears after it in `tasks.md`
- **THEN** the loaded tail task's dependencies include that group-2 task id

#### Scenario: Producers appear earlier in file order
- **WHEN** a tail-kind task follows non-tail tasks from several earlier groups
- **THEN** the loaded tail task's dependencies include every one of those ids

#### Scenario: Tail task is first in its group
- **WHEN** a tail-kind task is the first task of its group and non-tail tasks follow it in the same group
- **THEN** the loaded tail task's dependencies include those following non-tail task ids, and the following tasks do not gain a dependency on the tail task, so no cycle is created

#### Scenario: Every task is a tail kind
- **WHEN** a change contains only tail-kind tasks
- **THEN** each task's inferred dependencies are limited to its within-group predecessor, exactly as before

### Requirement: Non-tail baseline dependencies are unchanged
A task source SHALL keep a non-tail task's inferred dependencies anchored on the nearest
preceding non-tail sibling within its own group, so ordinary groups stay independent of one
another and their tasks remain eligible for the parallel fan-out.

#### Scenario: Cross-group implementation tasks stay independent
- **WHEN** two non-tail tasks in different groups declare no `depends:` and share no other inferred edge
- **THEN** neither appears in the other's dependencies

#### Scenario: A tail-kind sibling is never an implementation baseline
- **WHEN** a tail-kind task is the first task of a group and non-tail tasks follow it
- **THEN** the first following non-tail task has no baseline dependency, and the next one's baseline is that first non-tail task -- never the tail-kind task

### Requirement: Tail dependency inference stays additive and deterministic
Inference SHALL union the baseline set with any authored `depends:` entries rather than
replacing either, SHALL not count the tail task among the non-tail tasks it depends on, and
SHALL emit the resulting list sorted and free of duplicates, so the same `tasks.md` always
loads to the same edges.

#### Scenario: Authored declaration is unioned with the complete baseline
- **WHEN** a tail-kind task carries a `depends:` entry naming a non-tail task that the baseline already covers
- **THEN** that id appears exactly once in the loaded dependency list, and every other baseline id is still present

#### Scenario: A tail task is not its own dependency
- **WHEN** a tail-kind task's inferred set is computed
- **THEN** its own id is not in the set, because the tail task is excluded from the non-tail tasks the set is built from

#### Scenario: Repeated loads agree
- **WHEN** the same `tasks.md` is loaded twice
- **THEN** both loads produce an identical dependency list for every task
