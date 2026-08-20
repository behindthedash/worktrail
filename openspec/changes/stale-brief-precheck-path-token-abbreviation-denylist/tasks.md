## 1. Implementation

- [x] 1.1 In `src/worktrail/router/check_brief_staleness.py`, add a module-level
  `_PATH_TOKEN_DENYLIST` frozenset containing `e.g`, `i.e`, `etc`, `vs`, `a.k.a`, and add a
  case-insensitive check in `_is_path_token()` that rejects a token whose lowercased form is in
  the denylist, alongside the existing absolute-path/parens/no-letter exclusions. (Requirement:
  Evidence Probe Extraction From Brief Text)

## 2. Tests

- [x] 2.1 In `tests/router/test_check_brief_staleness.py`, add test coverage asserting that
  `e.g`, `i.e`, and `a.k.a` (from prose such as "see e.g. the router", "i.e. the same module",
  "a.k.a. the guard", covering both trailing-period and bare forms and mixed case) are not
  extracted as path probes by `extract_probes()`.
- [x] 2.2 In the same file, add test coverage asserting that legitimate short-extension
  backtick-quoted tokens (`guard.py`, `README.md`, `deploy.sh`) are still extracted as path
  probes, and confirm the existing `test_bare_filename_qualifies_as_path_probe` test still
  passes unmodified.

## 3. Verification

- [x] 3.1 [e2e] Run `PYTHONPATH=src pytest -q` and confirm the full suite passes, including the
  new and existing `_is_path_token`/`extract_probes` coverage, and that no other test relying on
  `etc`/`vs`/`e.g`/`i.e`/`a.k.a`-shaped tokens regresses.
