## MODIFIED Requirements

### Requirement: Declared scope satisfies compilation without a model call

When every non-tail task in a change carries at least one declared file, plan compilation
SHALL produce the change's RunPlan purely from the parsed artifact — recording it as
seeded-from-artifact — without any model invocation, matching the free path devkit specs
already take. A change where only some tasks declare files SHALL still have its declared
scopes honored while remaining undeclared tasks go through the existing inference path. The
plan that inference produces SHALL carry a declaring task's parsed declaration verbatim,
exactly as the seeded plan does, on every plan source a compile settles on — a model answer
for such a task's `files` SHALL NOT displace it, whether the answer is empty or a different
list, so a task whose declaration survives parsing can never be reported by the post-compile
scope check.

#### Scenario: Fully declared change takes the no-model seed path

- **WHEN** plan compilation runs against a change whose implementation tasks all declare
  at least one file (tail-kind tasks need none)
- **THEN** the produced plan's per-task file scopes equal the declared lists, the plan is
  recorded as seeded rather than model-inferred or baseline, and no inference call is made

#### Scenario: Partially declared change still compiles the gaps

- **WHEN** some implementation tasks declare files and others do not
- **THEN** the declaring tasks' scopes are used as declared, and only the undeclared tasks'
  scopes are subject to inference — with the conservative baseline behavior unchanged when
  inference is unavailable

#### Scenario: A model answer never displaces a declaration

- **WHEN** some implementation tasks declare files and the inference pass run on the
  undeclared tasks' behalf answers an empty (or differing) `files` list for a declaring task
- **THEN** the compiled plan carries that task's declared list verbatim, the post-compile
  scope check reports no gap for it, and only the tasks that declared nothing depend on the
  model's answer

#### Scenario: An accepted declaration outside the repo survives compilation

- **WHEN** a `files:` declaration accepted by the parser names a path outside the repo — a
  run-journal citation beside the worktree — and the inference pass answers for its task
- **THEN** the compiled plan carries the declared path verbatim, exactly as a seeded plan
  does; the rejection of absolute and parent-directory paths applies only to paths the model
  supplies for undeclared tasks

#### Scenario: An undeclared task's repo-escaping answer is still rejected

- **WHEN** the inference pass returns an absolute path or a parent-directory traversal for a
  task that declares no files
- **THEN** the payload is rejected and the plan degrades to the baseline, exactly as before

#### Scenario: Editing a declaration invalidates the cached plan

- **WHEN** a change's cached plan exists and a task's declaration is subsequently edited
- **THEN** the next compilation does not serve the stale cache entry, because the change's
  planning fingerprint incorporates the declared file lists
