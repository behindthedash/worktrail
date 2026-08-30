## Why

`scripts/check_packaging_metadata.py` (PR #300) verifies that the checkout's
`pyproject.toml` declarations agree with the *currently installed*
distribution's metadata and the Codex plugin manifest — but it never builds
an artifact. It reads `importlib.metadata` for whatever is already installed
in the environment (an editable install locally, or whatever `pip install -e
".[dev]"` produced in CI) and hand-parses `[project]`/`[project.scripts]`
with a regex that intentionally only handles simple string-valued tables.
Nothing in the suite proves that `setuptools.build_meta` actually generates
correct `console_scripts` entry points and version metadata when it builds a
real wheel or sdist — the artifact `pip install worktrail` (the supported,
non-editable install path) would actually consume. A future change to
`pyproject.toml`'s packaging syntax (e.g. a dynamic version, a differently
shaped `[project.scripts]` table, a build-backend option) could pass the
existing checks while silently producing a broken or incomplete published
artifact, and nothing in CI would catch it before release.

## What Changes

- Add `scripts/check_built_artifact_packaging.py`: builds a wheel and an
  sdist for the checkout in an isolated temporary directory (never touching
  the canonical editable install or `sys.path`), inspects the wheel's
  `METADATA`/`entry_points.txt` and the sdist's `PKG-INFO` via
  `zipfile`/`tarfile` + `email.parser` (stdlib only, matching
  `check_packaging_metadata.py`'s existing no-TOML-dependency constraint),
  and compares the normalized project version and the set of
  `worktrail-*` console-script names against the checkout's
  `pyproject.toml` declarations (reusing
  `check_packaging_metadata.checkout_console_scripts`/`checkout_version`
  rather than re-parsing).
- Add `tests/test_built_artifact_packaging.py` with deterministic coverage:
  matching wheel/sdist metadata passes; a version drift fails; a
  console-script-name drift (missing/extra) fails; malformed/missing
  artifact metadata produces a clear error rather than a stack trace.
- Extend the existing CI `Build` step (`.github/workflows/ci.yml`,
  `lint-test-build` job) to run the new check immediately after `python3 -m
  build`, against the wheel/sdist that step already produces — no new job,
  no new required check name, no separate artifact build. The step already
  runs unconditionally on any PR the bookkeeping-changes gate doesn't skip
  (docs-only PRs already skip the whole job), so packaging syntax changes
  are covered without adding a second gating layer to configure and keep in
  sync.
- Do not change `scripts/check_packaging_metadata.py`'s behavior or its
  `dev-install.sh` wiring; the new check is additive and independent, run at
  a different point in the pipeline (build time, not post-install time).

## Capabilities

### New Capabilities
- `built-artifact-packaging-parity-gate`: building a wheel and sdist in
  isolation and verifying their generated metadata (version, console-script
  entry points) matches the checkout's `pyproject.toml` declarations, wired
  into the existing CI build step.

### Modified Capabilities
(none — `check_packaging_metadata.py`'s installed-distribution behavior is
unchanged; this adds a new, independent verification surface)

## Impact

- New files: `scripts/check_built_artifact_packaging.py`,
  `tests/test_built_artifact_packaging.py`.
- Modified: `.github/workflows/ci.yml` (`lint-test-build` job's `Build`
  step).
- No change to `pyproject.toml`, `scripts/dev-install.sh`, or
  `scripts/check_packaging_metadata.py`.
- Adds a `python3 -m build` artifact-inspection pass to CI; the build itself
  already runs today, so the added cost is limited to a stdlib-only
  zipfile/tarfile read and a set/string comparison, not a second build.
