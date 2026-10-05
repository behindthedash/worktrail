## 1. Parse the declaration in the OpenSpec checklist

- [ ] 1.1 In `src/worktrail/taskformats/openspec/schema.py`, add a fourth continuation-line
      pattern beside `FILES_RE` (`:64`), `REVIEW_RE` (`:74`) and `DEPENDS_RE` (`:83`):
      `TIMEOUT_RE = re.compile(r"^[ \t]+timeout:\s*(.*?)\s*$", re.IGNORECASE)`, carrying a comment
      in the same voice as its three neighbours stating that the value is a whole number of
      seconds and that a task declaring none runs on the run-wide worker timeout. Give
      `ParsedTask` a `timeout: int | None = None` field (`:113-130`). Add the declaration branch to
      `parse_tasks_md`'s follow-line window (`:189-237`), in the same shape as the `files:` branch:
      strip surrounding whitespace and any backtick wrapping, parse with `int()`, and warn plus
      leave the field `None` -- never a hard parse error -- when the value is empty, is not a whole
      number, or is `<= 0`; record a warning and keep the first declaration when a second
      `timeout:` line follows the same task. Normalising a non-positive value to `None` rather than
      to the written value is deliberate and mirrors `devkit/source.py:272-275`: the orchestrator
      reads `task.get("timeout") or run_default`, so `0` must not reach `subprocess` as "no timeout
      at all". A top-level unindented `timeout:` line stays undeclared, exactly as
      `REVIEW_RE`/`DEPENDS_RE` already require of their own lines.
      Add cases to `tests/taskformats/openspec/test_openspec_schema.py` in the file's existing
      style (each a `textwrap.dedent`ed `tasks.md` plus an assertion on `parsed.by_id(...)`):
      a declaration under a task parses to that int; a task with no such line parses to `None`
      with `parsed.warnings == []`; `timeout: 0`, `timeout: -5`, `timeout: soon` and an empty
      `timeout:` each warn and leave `None`; a duplicate line warns and keeps the first value;
      a backtick-wrapped and whitespace-padded `timeout: ` `` `3600` `` parses to `3600`; a
      top-level unindented `timeout: 3600` is not a declaration; a `files:` line and a `timeout:`
      line coexist under one task in either order; and `set_task_checked` on a task that declared
      a budget changes only the checkbox byte, leaving the `timeout:` line intact (mirroring
      `test_set_task_checked_leaves_depends_line_untouched`, `:352`).
      (Requirement: Inline worker-budget declaration parsing)
      files: src/worktrail/taskformats/openspec/schema.py, tests/taskformats/openspec/test_openspec_schema.py

## 2. Carry the declaration onto the loaded task

- [ ] 2.1 In `src/worktrail/taskformats/openspec/source.py`, surface the parsed budget on the task
      dict `OpenSpecTaskSource.load()` builds (`:111-124`), as `"timeout": t.timeout` beside the
      existing `"files": list(t.files)` entry, and note it in the docstring paragraph that already
      explains which authored declarations reach the loaded task. No orchestrator change is needed
      or made: `taskformats/base.py:30` already types `timeout: int | None` on `TaskDict`, and the
      per-task override that consumes it is already format-agnostic --
      `live.py:2978` (`effective_timeout = task.get("timeout") or self.timeout`), re-read for the
      start banner at `:5190` and for the timeout message at `:5212`. Add cases to
      `tests/taskformats/openspec/test_openspec_source.py` alongside the existing
      `test_load_carries_declared_files_into_task_dict` (`:127`) and
      `test_load_surfaces_empty_files_declaration_as_a_frontmatter_warning` (`:149`): a change whose
      tail `[e2e]` task declares a budget loads that task with the declared int; a task without a
      declaration loads `timeout` as `None` (so `task.get("timeout") or run_default` still yields
      the run-wide default); a malformed declaration surfaces in the loaded task's
      `frontmatter_warnings`; and one further case asserting the composition the reported failure
      turned on -- for a changed `[e2e]` task, `not (task["kind"] in coordinator.TAIL_KINDS and
      not task["timeout"])`, which is the exact guard the run-wide-default advisory at
      `live.py:5257` applies, is `False` with a declaration and `True` without one, so the task
      that declared its own budget is no longer advised to declare one.
      (Requirements: Inline worker-budget declaration parsing; A declared budget is the worker
      budget for its task)
      depends: 1.1
      files: src/worktrail/taskformats/openspec/source.py, tests/taskformats/openspec/test_openspec_source.py

## 3. Keep the AC-target precheck's metadata list complete

- [ ] 3.1 In `src/worktrail/conductor/ac_targets.py`, add `schema.TIMEOUT_RE` to the metadata
      skip-list in `_task_block_text` (`:86-92`), whose comment already enumerates the
      continuation lines that are "metadata, not prose" and must not be read as a task's
      acceptance-criteria text. Without this the new declaration line would be the one
      continuation line the precheck reads as task prose, silently widening what
      `find_missing_ac_targets` scans. Add a case to `tests/conductor/test_ac_targets.py` in the
      file's existing style: a task whose continuation block carries a `timeout:` declaration
      plus an update sentence that *would* otherwise report a finding yields the same
      `find_missing_ac_targets` result as the same task without the declaration line (the
      declaration contributes nothing to the extracted text), while the file's existing
      `files:`/`depends:`/`review:` skip cases keep their current expectations.
      (Requirement: Inline worker-budget declaration parsing)
      depends: 1.1
      files: src/worktrail/conductor/ac_targets.py, tests/conductor/test_ac_targets.py

## 4. Document the convention where tasks.md is drafted

- [ ] 4.1 In `skills/openspec-propose/SKILL.md`'s tasks-artifact guidance, add a `timeout:`
      bullet beside the existing `files:` (`:80-92`), `review: skip` (`:131-134`) and `depends:`
      (`:135-140`) bullets, in the same voice and with an example continuation line such as
      `timeout: 3600` under a task. The bullet shall state that the value is whole seconds, that
      the declaration is opt-in per task, that a task declaring none runs on the run-wide worker
      timeout, and when a declaration is expected: a task whose body predictably outruns that
      timeout -- notably a tail `[e2e]` task that runs the repository's whole test suite together
      with the targeted-suite, golden-record and lint-wrapper commands its body names -- declares
      a budget sized to that work instead of being left to fail on the default. Name no new
      `worktrail-*` command and touch no other section of the skill: the only console script this
      bullet may reference is the already-documented `worktrail-compile`. Add the rule to the
      pre-handoff re-check list at `:184-189`, which already enumerates the requirement-coverage,
      file-less-task, task-sizing, test-co-scoping and `review: skip` rules.
      (Requirement: Authoring documentation of the budget declaration)
      review: skip
      files: skills/openspec-propose/SKILL.md

## 5. Verification

- [ ] 5.1 [e2e] Run `PYTHONPATH=src pytest -q tests/taskformats/openspec tests/conductor`,
      then `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`, then `python3 scripts/ci/ruff_pinned.py check .`,
      `python3 scripts/ci/ruff_pinned.py format --check .` and `python3
      scripts/ci/check_shebang_exec_bits.py`; confirm all pass. Then `openspec validate
      e2e-task-timeout-budget-floor --strict` and `worktrail-compile
      openspec/changes/e2e-task-timeout-budget-floor`. Probe the declaration by hand against a
      scratch copy of a `tasks.md` under `/tmp`: confirm `parse_tasks_md` returns the declared int
      for a task that carries `timeout: 3600`, and `None` for `timeout: 0`, `timeout: soon`, an
      empty `timeout:` and no line at all, each with a warning except the last; confirm
      `resolve.load_spec` over the scratch change surfaces that int on the loaded task dict's
      `timeout` key; and confirm the run-wide-default advisory predicate
      (`task["kind"] in coordinator.TAIL_KINDS and not task["timeout"]`) is `False` for the
      declaring tail task and `True` for the same task with the declaration deleted. No file
      changes are expected from this task.
      (Requirements: A declared budget is the worker budget for its task)
      depends: 1.1, 2.1, 3.1, 4.1
      timeout: 3600
