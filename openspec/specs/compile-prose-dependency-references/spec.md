# compile-prose-dependency-references Specification

## Purpose
Defines how an explicitly authored prose dependency reference in an OpenSpec task's text ("depends on 1.1, 1.2") becomes a dependency edge in the compiled RunPlan, on every compile path, so an ordering constraint the author stated in the artifact cannot be lost merely because the two tasks declare disjoint file scope.
## Requirements
### Requirement: Authored prose dependency references become plan edges
The compile step SHALL read each task's authored text for an explicit prose dependency reference naming one or more task ids from the same change, and SHALL include every referenced id in that task's compiled `deps`. This SHALL hold whether or not the two tasks declare any file in common. The union SHALL be additive only: no dependency edge present before the union is removed by it.

#### Scenario: A prose reference between file-disjoint tasks is honored
- **WHEN** a task's authored text states that it depends on two earlier tasks, and that task's declared files share no path with either earlier task
- **THEN** the compiled plan records both earlier task ids in that task's dependencies

#### Scenario: Existing edges are preserved
- **WHEN** a task carries dependency edges from the compile step's own reasoning and also states a prose dependency reference
- **THEN** the compiled plan records the union of both, dropping neither

#### Scenario: A task with no prose reference is unaffected
- **WHEN** a task's authored text states no dependency reference
- **THEN** that task's compiled dependencies are exactly what the compile step produced without this rule

#### Scenario: A self-reference is not an edge
- **WHEN** a task's authored text names its own id in a dependency reference
- **THEN** no self-edge is added to the compiled plan

### Requirement: Prose dependency references are honored on every compile path
The prose-reference union SHALL be applied both when the compile step infers a plan with a model and when it produces a plan without one because the artifact already declares file scope. A change whose tasks all declare file scope SHALL therefore still receive its authored prose dependency edges.

#### Scenario: The no-model path unions prose references
- **WHEN** every task in a change declares file scope, so no model pass runs, and one task states a prose dependency on another
- **THEN** the plan produced without a model still records that dependency edge

#### Scenario: The model path unions prose references
- **WHEN** the compile step infers file scope with a model and the model's answer omits an edge the authored text states
- **THEN** the compiled plan still records that edge

### Requirement: An unresolvable prose dependency reference is reported
When an authored prose dependency reference names an identifier that matches no task in the change, the compile step SHALL report it as a compile problem naming the referring task and the unmatched identifier, rather than discarding it silently. The problem SHALL NOT be raised for a reference whose every identifier resolves.

#### Scenario: A typo'd reference is surfaced
- **WHEN** a task states a dependency on an identifier that names no task in the change
- **THEN** compile reports a problem naming the referring task and the unmatched identifier

#### Scenario: A fully resolvable reference is not reported
- **WHEN** every identifier in a task's prose dependency reference names a task in the change
- **THEN** no unresolvable-reference problem is reported for that task

### Requirement: The compile prompt asks the model to honor prose dependency references
The compile step's model prompt SHALL instruct the model to treat an explicit prose dependency sentence in a task's text as a real ordering constraint, in addition to the shared-file ordering check it already performs, so the model's own answer agrees with the deterministic union rather than contradicting it.

#### Scenario: The prompt names the prose-reference constraint
- **WHEN** the compile prompt is composed
- **THEN** it instructs the model that a task's stated dependency on another task is an ordering constraint even when the two tasks share no file

#### Scenario: The instruction reaches the formatted prompt
- **WHEN** the compile step formats the prompt for a real change
- **THEN** the prose-reference instruction is present in the text actually sent

