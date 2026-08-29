## 1. Parser support (`src/worktrail/taskformats/openspec/schema.py`)

- [x] 1.1 Extend `parse_tasks_md` to recognize an indented `files:` continuation line immediately following a `- [ ] N.M` task line (window ended by a group heading, the next task line, or a blank line), split its content into repo-relative path tokens (comma- and/or whitespace-separated, backtick-wrapped tokens unwrapped), add a `files: list[str]` field to `ParsedTask`, and update the module docstring's "no per-task metadata, no file scope" claim to describe the optional declaration (Requirement: Inline file-scope declaration parsing)
- [x] 1.2 Emit a tolerant-parse warning (via the existing `warnings` list) when an indented `files:` line under a task names no paths, and when more than one indented `files:` line follows the same task — carrying only the first declaration's paths in both cases, never raising (Requirement: Tolerant handling of malformed declarations)
- [x] 1.3 Add parser tests covering: declaration parsed into `ParsedTask.files`; task without a continuation line parses with empty `files` identically to today; comma-separated, space-separated, mixed, and backticked token forms; the two warning cases above; and that `set_task_checked` on a declared task changes only the checkbox marker byte-for-byte elsewhere (Requirements: Inline file-scope declaration parsing, Tolerant handling of malformed declarations)

## 2. Source adapter (`src/worktrail/taskformats/openspec/source.py`)

- [x] 2.1 Change `load()` to emit each parsed task's `files` into the task dict in place of the hard-coded `"files": []`, and update the class docstring so "deliberately does not synthesise files" reads as "never invents scope; carries only what the artifact declares" (Requirement: Inline file-scope declaration parsing)
- [x] 2.2 Add adapter tests asserting declared paths reach `task["files"]` through `OpenSpecTaskSource.load()` and ride the existing `frontmatter_warnings` plumbing for the malformed cases (Requirements: Inline file-scope declaration parsing, Tolerant handling of malformed declarations)

## 3. Seed-path behavior (`tests/conductor/test_compile.py`)

- [x] 3.1 Add a compile test proving a change whose implementation tasks all declare at least one file yields `SOURCE_SEED` without invoking the injectable `spawn` callable, with per-task scopes equal to the declared lists and tail-kind (`[e2e]`/`[cleanup]`) tasks exempt from declaring (Requirement: Declared scope satisfies compilation without a model call)
- [x] 3.2 Add compile tests for the partial case (declared scopes honored, remaining gaps still routed through inference/baseline when spawning fails) and for fingerprint invalidation (editing one declaration produces a new fingerprint so no stale cached plan is served) (Requirement: Declared scope satisfies compilation without a model call)
- [x] 3.3 Re-check `_print_scope_gap_error`'s remediation text and `compile.py`'s module docstring for accuracy now that "add explicit \`files:\` scope to the artifact" is actionable for OpenSpec, adjusting wording if it implies devkit-only (no behavioral change expected)

## 4. Documentation

- [x] 4.1 Add the optional indented `files:` declaration to the `openspec-propose` skill's tasks-artifact guidance — syntax example, opt-in per task, create-or-modify paths only, full-declaration compiles without a model call — and amend its "OpenSpec's checklist schema carries no field for that" sentence accordingly (Requirement: Authoring documentation of the declaration syntax)
- [x] 4.2 Add a short amendment note to `docs/design/conductor-lanes.md` §4.5/D2 recording the opt-in inline declaration as a scoped exception to "file scope comes from the compiled RunPlan", preserving D2's compile-pass decision rather than reversing it

## 5. Verification

- [x] 5.1 [e2e] Run `PYTHONPATH=src pytest -q && PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` and confirm both pass with no regressions
- [x] 5.2 [e2e] Confirm `skills/openspec-propose/SKILL.md` still passes `tests/test_plugin_surface.py` (prose-only edit; no new skill directory or console script introduced by this change)
