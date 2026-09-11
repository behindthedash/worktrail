## 1. TypeScript/JavaScript import scanning (`compile-import-dependency-inference`)

- [ ] 1.1 In `src/worktrail/conductor/import_deps.py`, replace the `.py` suffix gate in
      `import_dep_edges` with a suffix-to-scanner dispatch; keep the existing `ast` walk as the
      Python scanner and add a `_js_imported_paths` scanner that regex-extracts string-literal
      specifiers from `import ... from`, side-effect `import`, `export ... from`, dynamic
      `import(...)`, and `require(...)`, resolving only `./` and `../` specifiers against the
      importing file's directory in the design's order (as written; each supported extension
      appended; `.js`-family suffix rewritten to its `.ts`-family source; `index` file in a
      directory). Bare and alias specifiers are dropped. Owner matching, the cycle guard,
      warnings, and the never-fail `except` remain shared and unchanged. Update the module
      docstring to name both languages and the alias exclusion. Extend
      `tests/conductor/test_import_deps.py` with cases for each specifier form, each
      resolution step, the `.js` -> `.ts` rewrite, bare and alias specifiers being ignored, an
      escaping relative specifier, a mutual `.ts` import producing one edge plus a warning,
      and `.md` files still being skipped; the existing Python cases must pass unchanged.
      files: src/worktrail/conductor/import_deps.py, tests/conductor/test_import_deps.py
      Covers: Imports between tasks' declared files become plan edges; Relative TypeScript and JavaScript specifiers are resolved to paths; Inference never introduces a cycle and never fails a compile

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run `pytest -q tests/conductor/test_import_deps.py
      tests/conductor/test_compile.py`, then `pytest -q` and
      `python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate compile-import-inference-typescript --strict` and
      `worktrail-compile openspec/changes/compile-import-inference-typescript`.
