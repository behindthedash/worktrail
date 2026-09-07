## MODIFIED Requirements

### Requirement: Authored prose dependency references become plan edges
The compile step SHALL read each task's authored text for explicit prose dependency references naming one or more task ids from the same change, and SHALL include every referenced id in that task's compiled `deps`. A dependency reference SHALL be recognised from either the `depends on <ids>` phrasing or the `after <ids>` phrasing. Every dependency phrase occurring in a task's text SHALL be scanned, not only the first. This SHALL hold whether or not the two tasks declare any file in common. The union SHALL be additive only: no dependency edge present before the union is removed by it, and no self-edge is ever added.

#### Scenario: An "after" reference between file-disjoint tasks is honored
- **WHEN** a task's authored text states that it runs after an earlier task, and that task's declared files share no path with the earlier task
- **THEN** the compiled plan records the earlier task id in that task's dependencies

#### Scenario: Both phrasings in one task are collected
- **WHEN** a task's authored text contains both an "after" reference and a "depends on" reference naming different tasks
- **THEN** the compiled plan records every referenced id, in authored order and without duplicates

#### Scenario: A prose reference between file-disjoint tasks is honored
- **WHEN** a task's authored text states that it depends on two earlier tasks, and that task's declared files share no path with either earlier task
- **THEN** the compiled plan records both earlier task ids in that task's dependencies

#### Scenario: Existing edges are preserved
- **WHEN** a task carries dependency edges from the compile step's own reasoning and also states a prose dependency reference
- **THEN** the compiled plan records the union of both, dropping neither

#### Scenario: A task with no prose reference is unaffected
- **WHEN** a task's authored text states no dependency reference in any recognised phrasing
- **THEN** that task's compiled dependencies are exactly what the compile step produced without this rule

#### Scenario: A self-reference is not an edge
- **WHEN** a task's authored text names its own id in a dependency reference
- **THEN** no self-edge is added to the compiled plan

### Requirement: An unresolvable prose dependency reference is reported
When a `depends on` dependency reference names an identifier that matches no task in the change, the compile step SHALL report it as a compile problem naming the referring task and the unmatched identifier, rather than discarding it silently. The problem SHALL NOT be raised for a reference whose every identifier resolves. An identifier collected from an `after` reference that matches no task in the change SHALL be dropped silently and SHALL NOT be reported as a compile problem, because the phrase occurs in ordinary task prose where the following token is not a task id.

#### Scenario: A typo'd reference is surfaced
- **WHEN** a task states a "depends on" dependency on an identifier that names no task in the change
- **THEN** compile reports a problem naming the referring task and the unmatched identifier

#### Scenario: A fully resolvable reference is not reported
- **WHEN** every identifier in a task's prose dependency reference names a task in the change
- **THEN** no unresolvable-reference problem is reported for that task

#### Scenario: An unmatched "after" token is not a problem
- **WHEN** a task's text says it retries after 3 attempts and no task in the change has the id 3
- **THEN** no dependency edge is added and no compile problem is reported for that task
