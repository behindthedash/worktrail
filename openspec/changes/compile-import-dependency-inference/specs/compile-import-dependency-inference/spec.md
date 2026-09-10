## Purpose

Defines how the compile step derives a dependency edge from a Python import between two
tasks' declared files, so a task that consumes a module another task owns is ordered behind
it even when the two tasks declare disjoint file scope, and states what an on-disk parse can
and cannot see so the author knows when to declare the edge instead.

## ADDED Requirements

### Requirement: Python imports between tasks' declared files become plan edges

The compile step SHALL, for each non-tail task, parse the imports of every declared file that
is a Python source file present on disk under the repository, resolve each import to a
repo-relative path, and where that path is declared in the file scope of a different task in
the same change, include that task's id in the importing task's compiled `deps`. This SHALL
hold whether or not the two tasks declare any file in common. The union SHALL be additive
only: no dependency edge present before the union is removed by it, and no self-edge is ever
added.

#### Scenario: A consumer of a sibling task's module is ordered behind it

- **WHEN** task B declares a Python file that imports a module whose path task A declares,
  and A's and B's declared files share no path
- **THEN** the compiled plan records A in B's dependencies

#### Scenario: Direction follows the import, not authored order

- **WHEN** task 1.1 declares a file that imports a module task 2.1 declares, and no
  dependency edge already leads from 2.1 back to 1.1
- **THEN** the compiled plan records 2.1 in 1.1's dependencies

#### Scenario: Files that import nothing of each other are unaffected

- **WHEN** no declared file of any task imports a module declared by another task
- **THEN** every task's compiled dependencies are exactly what the compile step produced
  without this rule

#### Scenario: A file importing its own task's other file adds no edge

- **WHEN** a task declares two files and one imports the other
- **THEN** no edge is added for that import

### Requirement: Both relative and absolute in-repo imports are resolved to paths

The compile step SHALL resolve a relative import against the importing file's package
directory, and SHALL resolve an absolute import by trying each source root of the repository
(`src/`, then the repository root) as a module file or a package `__init__.py`. An import that
resolves to no file under the repository SHALL be ignored. Matching against other tasks'
declared scope SHALL be by repo-relative path equality, never by module stem alone.

#### Scenario: An absolute import resolves under src/

- **WHEN** task B's file contains `from worktrail.router import smoke_flake_selfcheck` and
  task A declares `src/worktrail/router/smoke_flake_selfcheck.py`
- **THEN** the compiled plan records A in B's dependencies

#### Scenario: A relative import resolves against the importing package

- **WHEN** task B's file `src/pkg/consumer.py` contains `from . import helper` and task A
  declares `src/pkg/helper.py`
- **THEN** the compiled plan records A in B's dependencies

#### Scenario: A third-party import adds nothing

- **WHEN** a task's file imports a module that resolves to no file under the repository
- **THEN** no edge is added and no problem or warning is reported for that import

#### Scenario: A same-named module in another package is not matched

- **WHEN** task B's file imports `a.util` and task A declares `src/b/util.py` but no task
  declares the file `a.util` resolves to
- **THEN** no edge is added between A and B

### Requirement: Import inference is applied on every compile path

The import-edge union SHALL be applied both when the compile step produces a plan without a
model because the artifact already declares file scope, and when it infers a plan with a
model. A change whose tasks all declare file scope SHALL therefore still receive its inferred
import edges.

#### Scenario: The seeded path unions import edges

- **WHEN** every task in a change declares file scope, so no model pass runs, and one task's
  file imports a module another task declares
- **THEN** the plan produced without a model records that dependency edge

#### Scenario: The model path unions import edges

- **WHEN** the compile step infers file scope with a model and the model's answer omits an
  edge that an on-disk import between two tasks' declared files establishes
- **THEN** the compiled plan still records that edge

### Requirement: Inference never introduces a cycle and never fails a compile

An inferred edge that would make a task depend, directly or transitively, on a task that
already depends on it SHALL be skipped, and the skip SHALL be recorded as a compile warning
naming both tasks. A declared file that is missing from disk, is not a Python source file, or
does not parse SHALL contribute no edges and SHALL NOT raise or produce a compile problem.

#### Scenario: A mutual import keeps the plan acyclic

- **WHEN** task A's file imports task B's file and task B's file imports task A's file
- **THEN** the compiled plan records exactly one edge between them, the plan remains acyclic,
  and a warning names both tasks

#### Scenario: A not-yet-created file contributes nothing

- **WHEN** a task declares a path that does not exist on disk
- **THEN** that path is skipped and the compile proceeds without a problem

#### Scenario: An unparseable file contributes nothing

- **WHEN** a task declares a Python file that raises a syntax error when parsed
- **THEN** that file is skipped and the compile proceeds without a problem

### Requirement: The compile prompt names import relationships as ordering constraints

The compile step's model prompt SHALL instruct the model that a task whose files import a
module another task creates or modifies depends on that task, even when the two tasks share
no file, so the model's answer agrees with the deterministic union.

#### Scenario: The instruction reaches the formatted prompt

- **WHEN** the compile step formats the prompt for a real change
- **THEN** the import-relationship instruction is present in the text actually sent
