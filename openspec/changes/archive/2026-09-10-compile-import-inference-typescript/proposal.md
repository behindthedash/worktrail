## Why

Import-based dependency inference (`src/worktrail/conductor/import_deps.py`, landed in
PR #1125, archived as `compile-import-dependency-inference`) orders a task behind the task
whose module it imports even when the two declare disjoint `files:`. It only sees Python:
`import_dep_edges` skips every declared file that does not end in `.py`, and resolution
only ever looks for `a/b.py` or `a/b/__init__.py`. A TypeScript or JavaScript change in a
consuming repo therefore gets none of this protection. Two tasks where `src/app/page.tsx`
imports `./lib/format` and a sibling task creates `src/app/lib/format.ts` compile to
parallel work, and the importer's worktree never sees the module, which is exactly the
failure the Python inference was added to stop. Worktrail is a repo-agnostic
orchestrator and is pointed at non-Python repos, so the gap is a real one.

## What Changes

- Extend `import_dep_edges` to also scan declared TypeScript and JavaScript source files
  (`.ts`, `.tsx`, `.mts`, `.cts`, `.js`, `.jsx`, `.mjs`, `.cjs`) for ES-module `import` /
  `export ... from` declarations, dynamic `import(...)` calls, and CommonJS `require(...)`
  calls with a string-literal specifier.
- Resolve only **relative** specifiers (`./`, `../`) against the importing file's
  directory, using the Node/TypeScript resolution order: the specifier as written, then
  each supported extension appended, then a directory `index` file with each extension,
  with the TypeScript convention that a `.js`-suffixed specifier may resolve to the `.ts`
  source. Bare package specifiers and `tsconfig` path aliases are ignored (deliberately out
  of scope; see design).
- Reuse the existing owner matching, cycle guard, warning, and never-fail behaviour
  unchanged: a resolved path declared by a different task yields an importer -> owner edge
  on both the seeded and model compile paths.
- Update the module docstring and the archived spec's "Python source file" wording so
  the contract names the supported languages.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `compile-import-dependency-inference`: declared TypeScript/JavaScript files are scanned
  for module specifiers in addition to Python files, with a stated resolution rule for
  relative specifiers and an explicit exclusion of bare and aliased specifiers.

## Impact

- `src/worktrail/conductor/import_deps.py`: a suffix-dispatched scanner; the Python path is
  untouched. No new runtime dependency (the scanner is a regex over the source text, not a
  TS parser). `compile.py` callers are unaffected.
- `tests/conductor/test_import_deps.py`: new cases for each specifier form, each
  resolution step, the `.js` -> `.ts` rewrite, bare specifiers being ignored, and the
  existing cycle/never-fail guarantees holding for TS files.
- Behaviour for Python-only changes is byte-for-byte unchanged; the existing
  `test_non_py_file_is_skipped` case (a `.md` file) still holds.
