## Context

`import_dep_edges` walks each non-tail task's declared files, parses the ones that exist on
disk, resolves their imports to repo-relative paths, and adds an edge to whichever other
task declares that path. Everything after "resolve to a path" is language-independent. The
only Python-specific parts are the `.py` suffix gate, `ast.parse`, and `_resolve` /
`_module_candidates`. This change adds a second scanner behind the same gate and keeps the
edge, cycle, and warning logic single-sourced.

## Decisions

### D1. Regex scanner, not a parser

There is one runtime dependency (`pyyaml`) and adding a JS/TS parser to infer plan edges is
not justified. The scanner extracts string-literal specifiers from these forms only:

- `import ... from "spec"` and side-effect `import "spec"`
- `export ... from "spec"`
- `import("spec")` (dynamic import)
- `require("spec")`

A match inside a comment or an unrelated string can over-collect, but an over-collected
specifier only produces an edge if it resolves to a file another task in the same change
declares, and inference is additive and cycle-guarded, so the worst case is a spurious
ordering edge between two tasks that already touch related code. Template-literal and
computed specifiers are not matched.

### D2. Relative specifiers only

A relative specifier (`./x`, `../x`) is resolved against the importing file's directory in
this order, stopping at the first file that exists:

1. the specifier as written;
2. the specifier with each supported extension appended, in the order
   `.ts .tsx .mts .cts .js .jsx .mjs .cjs .d.ts`;
3. if the specifier ends in `.js`, `.jsx`, `.mjs`, or `.cjs` and step 1 missed, the same
   path with that extension replaced by `.ts`/`.tsx`/`.mts`/`.cts` respectively (the
   TypeScript ESM convention of importing the emitted name);
4. `<specifier>/index` with each extension from step 2.

Bare specifiers (`react`, `@scope/pkg`, `lodash/fp`) are package imports and are dropped
without resolution, matching how third-party Python imports are treated. `tsconfig.json`
`paths` / `baseUrl` aliases (`@/lib/x`) are also dropped: resolving them means locating
and parsing the right `tsconfig.json` (possibly with `extends`) per importing file, and a
wrong guess produces edges between unrelated tasks. This is left for a follow-up if a
consuming repo needs it; the `depends:` continuation line covers it meanwhile.

### D3. One dispatch point, shared tail

`import_dep_edges` replaces `if not f.endswith(".py"): continue` with a suffix lookup that
selects a scanner (`_py_imported_paths`, the existing `ast` walk, or
`_js_imported_paths`). Both return repo-relative paths, and the owner match, `_reachable`
cycle check, warning text, and never-fail `except` behaviour are unchanged. A declared file
whose suffix has no scanner is skipped exactly as today. `_under_repo` still discards a
resolved path that escapes the repository (e.g. `../../outside`).

## Risks / Trade-offs

- Over-collection from comments (D1) is accepted for simplicity. It cannot fail a compile
  or create a cycle.
- Aliased imports (D2) are silently unordered, the same as a not-yet-created Python module
  today. The docstring and spec say so.
