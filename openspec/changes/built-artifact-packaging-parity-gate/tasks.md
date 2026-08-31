## 1. Metadata extraction helpers

- [x] 1.1 Add `scripts/check_built_artifact_packaging.py` with functions to
      build a wheel and sdist via a `python -m build` subprocess into a
      `tempfile.TemporaryDirectory()` when no artifact paths are supplied,
      and to accept `--wheel`/`--sdist` paths instead (skipping the build)
      for the CI invocation. (Requirement: Hermetic artifact build)
- [x] 1.2 Add wheel metadata extraction: read `METADATA` and
      `entry_points.txt` from the built/given wheel via `zipfile`, parse
      `METADATA`'s `Version` header with `email.parser`, and parse
      `entry_points.txt`'s `[console_scripts]` section with `configparser`
      (treating a missing section as an empty set, not an error).
      (Requirement: Built-artifact metadata extraction)
- [ ] 1.3 Add sdist metadata extraction: read `PKG-INFO` from the
      built/given sdist via `tarfile`, parsing its `Version` header the
      same way as the wheel's `METADATA`. (Requirement: Built-artifact
      metadata extraction)
- [ ] 1.4 Raise a clear, distinguishable error (not an unhandled exception)
      when the build subprocess fails, or when a built/given artifact is
      missing its expected metadata file (`METADATA`, `entry_points.txt`,
      or `PKG-INFO`). (Requirement: Deterministic failure on malformed or
      missing artifact metadata)

## 2. Parity comparison and CLI

- [ ] 2.1 Import and reuse `checkout_console_scripts(repo)` and
      `checkout_version(repo)` from `scripts/check_packaging_metadata.py`
      for the checkout's declared values — do not re-parse
      `pyproject.toml`.
- [ ] 2.2 Compare the wheel's and sdist's extracted version against the
      checkout's declared version (exact string equality); on mismatch,
      report which artifact disagreed and both values. (Requirement:
      Version and console-script parity)
- [ ] 2.3 Compare the wheel's extracted `console_scripts` name set against
      the checkout's declared `[project.scripts]` name set; on mismatch,
      report missing and/or extra names. (Requirement: Version and
      console-script parity)
- [ ] 2.4 Add a `main()`/CLI entry point mirroring
      `check_packaging_metadata.py`'s shape: `--repo` (default cwd),
      optional `--wheel`/`--sdist`, non-zero exit and stderr messages on
      any failure, a single success line on stdout when all comparisons
      pass.
- [ ] 2.5 Ensure the temporary build directory (when the script builds
      artifacts itself) is always removed, including on failure paths.

## 3. Tests

- [ ] 3.1 Add `tests/test_built_artifact_packaging.py`: a matching-checkout
      fixture (using this repo's own checkout, or a minimal synthetic
      `pyproject.toml` fixture) builds cleanly and the check passes.
- [ ] 3.2 Test version drift: a synthetic checkout/artifact pair with
      differing versions fails with a message naming both values.
- [ ] 3.3 Test console-script drift: missing-script and extra-script cases
      each fail with a message naming the differing script name(s).
- [ ] 3.4 Test build failure handling: a build subprocess failure (e.g. a
      malformed `pyproject.toml`) produces a clear, non-crashing error.
- [ ] 3.5 Test missing-metadata-file handling: an artifact archive lacking
      its expected metadata file produces a clear, non-crashing error
      naming the missing file.
- [ ] 3.6 Test hermeticity: running the check does not modify the checkout's
      working tree and does not alter the currently-installed worktrail
      distribution's `importlib.metadata` state (e.g. assert no new/changed
      files under the repo root before/after, and installed distribution
      metadata equality before/after).
- [ ] 3.7 Test the `--wheel`/`--sdist` pre-built-artifact path: given
      existing artifact paths, the check reads them without invoking a
      build (e.g. assert the build subprocess is not invoked).

## 4. CI wiring

- [ ] 4.1 Extend `.github/workflows/ci.yml`'s `lint-test-build` job: after
      the existing `Build` step (`pip install build && python3 -m build`),
      add a step that runs `scripts/check_built_artifact_packaging.py`
      pointed at the `dist/*.whl` and `dist/*.tar.gz` that step just
      produced, gated the same way (`if:
      needs.changes.outputs.bookkeeping == 'false'`). (Requirement: CI
      integration without a new required check)
- [ ] 4.2 Confirm no new job or required-status-check name is introduced —
      the existing `Lint, Test & Build` check name continues to reflect
      this step's pass/fail result. (Requirement: CI integration without a
      new required check)

## 5. Verification

- [ ] 5.1 [e2e] Run `PYTHONPATH=src pytest -q` and confirm the new tests
      pass alongside the full suite.
- [ ] 5.2 [e2e] Run `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check` (golden regression) and
      confirm it remains green.
- [ ] 5.3 [e2e] Run `python3 scripts/check_built_artifact_packaging.py
      --repo .` locally against this checkout and confirm it reports
      success.
- [ ] 5.4 [e2e] Confirm `tests/test_plugin_surface.py` still passes (no
      changes to skills/commands in this change, but it also imports
      `check_packaging_metadata`, so verify the new script's reuse of that
      module doesn't introduce an import cycle or path issue).
