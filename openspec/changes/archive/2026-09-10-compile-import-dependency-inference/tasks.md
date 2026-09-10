# Tasks

## 1. `depends:` continuation line

- [x] 1.1 Parse an indented `depends:` continuation line in `parse_tasks_md`, in the same
      window as `files:`/`review:`, into a new `ParsedTask.depends` list of task ids
      (comma/whitespace separated, backticks stripped, self-reference dropped); warn on a
      duplicate line or an empty value like `files:` does. Tests cover: a single id, several
      ids, no line, a duplicate line, an empty value, a self-reference, and that
      `set_task_checked` leaves the line untouched.
      (Requirement: Inline dependency declaration parsing)
  files: src/worktrail/taskformats/openspec/schema.py tests/taskformats/openspec/test_openspec_schema.py

- [x] 1.2 Union `ParsedTask.depends` into the loaded task's `deps` in
      `OpenSpecTaskSource.load()` (additive to the baseline within-group predecessor, no
      duplicates) and prove `validate_dependencies()` reports a declared id that names no
      task. Tests cover: cross-group declaration alongside a baseline predecessor, a tail task
      declaring an extra dependency, and the unresolvable-id report.
      (Requirement: Inline dependency declaration parsing)
  files: src/worktrail/taskformats/openspec/source.py tests/taskformats/openspec/test_openspec_source.py

## 2. Import inference in compile

- [x] 2.1 Add `src/worktrail/conductor/import_deps.py` exposing
      `import_dep_edges(tasks, repo) -> (edges, warnings)`: for each non-tail task, parse
      every declared `.py` file present under the repo with `ast`, resolve relative imports
      against the file's package and absolute imports under `src/` then the repo root (module
      file or package `__init__.py`), match resolved paths against other tasks' declared
      files by path equality, and emit importer -> owner edges. Skip a missing, non-`.py`,
      undecodable, or unparseable file silently. Add an edge only if the owner cannot already
      reach the importer through edges present so far (baseline deps plus edges added
      earlier in authored order); a skipped edge yields a warning naming both tasks. Tests
      cover: absolute import under `src/`, relative import, third-party import, same-stem
      module in another package, same-task import, backward (2.1 -> 1.1 direction) edge,
      mutual import keeping one edge plus a warning, missing file, and syntax-error file.
      (Requirement: Python imports between tasks' declared files become plan edges)
      (Requirement: Both relative and absolute in-repo imports are resolved to paths)
      (Requirement: Inference never introduces a cycle and never fails a compile)
  files: src/worktrail/conductor/import_deps.py tests/conductor/test_import_deps.py

- [x] 2.2 Union `import_dep_edges()` into `deps` in both `_plan_from_tasks()` and
      `_validate()` next to the existing prose-reference union, threading `repo` to both and
      logging any returned warnings; extend `PROMPT` so the "files are not the only source
      of ordering" paragraph also names an import relationship between two tasks' files as
      an ordering constraint. Tests cover: the seeded (all-`files:`) path recording the edge,
      the model path recording an edge the model's answer omitted, the incident regression
      fixture (task A owns a module, task B's on-disk file imports it, disjoint `files:`,
      compile emits B deps=A), the same fixture ordered by `depends:` alone with neither file
      on disk, and pinning the new prompt text through `.format()`. Depends on 2.1.
      (Requirement: Import inference is applied on every compile path)
      (Requirement: The compile prompt names import relationships as ordering constraints)
      (Requirement: Python imports between tasks' declared files become plan edges)
  files: src/worktrail/conductor/compile.py tests/conductor/test_compile.py

## 3. Authoring guidance

- [x] 3.1 In the `openspec-propose` skill's `tasks.md` rules, document the `depends:`
      continuation line next to the `files:`/`review: skip` guidance, with the rule: when a
      task's `files:` names a path that does not exist on the base branch and another task
      will import it, the importing task carries `depends: <creator id>`. Note that
      `worktrail-compile` infers the edge on its own only when the import is already on
      disk.
  files: skills/openspec-propose/SKILL.md
  review: skip

## 4. Verification

- [x] 4.1 [e2e] Run `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` and confirm both
      pass. Then run `worktrail-compile --no-llm` against the archived
      `openspec/changes/archive/2026-09-10-smoke-flake-dashboard-surface` tasks.md and
      confirm the seeded plan now records `2.1 deps=1.1` (dashboard.py's import of
      smoke_flake_selfcheck is on main), the shape that quarantined run go-20260910-085218.
