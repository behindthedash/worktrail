## Context

See `proposal.md` - Why. Relevant existing surface:

- `scripts/check_packaging_metadata.py` already exposes
  `checkout_console_scripts(repo)` and `checkout_version(repo)`, which parse
  `pyproject.toml`'s `[project.scripts]`/`[project]` tables with a
  dependency-free regex (Worktrail has no TOML-parsing dependency; the repo
  targets Python 3.10, before `tomllib` is in the stdlib as a 3.11+-only
  module usable without a backport). This change reuses those two functions
  rather than re-parsing `pyproject.toml`.
- CI's `lint-test-build` job (`.github/workflows/ci.yml`) already runs
  `pip install build && python3 -m build` unconditionally for any PR the
  `changes` job's bookkeeping filter doesn't skip, writing `dist/*.whl` and
  `dist/*.tar.gz` into the runner's (ephemeral) working tree. Nothing
  currently inspects those artifacts.
- `scripts/dev-install.sh` runs `check_packaging_metadata.py` after every
  `pip install -e ".[dev]"`, verifying the *installed* distribution. This
  change does not touch that path.

## Goals / Non-Goals

**Goals:**
- Prove that a wheel and sdist built by `setuptools.build_meta` from the
  current checkout carry the declared version and the full declared
  `console_scripts` entry-point set.
- Keep the check hermetic and fast enough to run on every non-bookkeeping PR
  without a second required check or a meaningfully longer CI run.

**Non-Goals:**
- Validating wheel/sdist contents beyond metadata (file lists, RECORD
  hashes, wheel tags) — out of scope; this is a metadata-parity check, not a
  full packaging-conformance auditor.
- Replacing or changing `check_packaging_metadata.py`'s installed-distribution
  check or its `dev-install.sh` wiring.
- Publishing or uploading the built artifacts anywhere; they exist only for
  the duration of the check.
- General TOML parsing — this reuses the existing regex-based
  `checkout_console_scripts`/`checkout_version`, which already documents its
  "simple string-valued tables only" limitation; extending that parser is
  out of scope for this change.

## Decisions

**Build invocation: `python -m build` subprocess, not in-process
`build_meta` hooks.** Shelling out to the same `python -m build` CI already
runs (rather than calling `setuptools.build_meta.build_wheel`/
`build_sdist` in-process) matches exactly what a real `pip install
worktrail` triggers, avoids importing `setuptools` internals into the
checking process, and keeps failure modes identical to CI's existing build
step (a build failure surfaces the same way whether it happens in the
existing step or inside this check).

**Reuse checkout artifact paths when supplied; otherwise build into a fresh
`tempfile.TemporaryDirectory()`.** The script accepts optional
`--wheel`/`--sdist` arguments. When given, it skips building and reads those
paths directly — this is how CI invokes it, pointing at the wheel/sdist the
job's existing `python3 -m build` step already produced in `dist/`, so the
package is built exactly once per CI run. When omitted (the default, used
by the deterministic tests and any standalone/local invocation), the script
builds fresh into a `tempfile.TemporaryDirectory()` that is always cleaned
up (via a `finally`/context manager), so a developer running the script
directly gets a fully hermetic, working-tree-clean check without needing to
know about `dist/`. This avoids a second build in CI (each build costs a
couple of seconds for this pure-Python, no-compiled-extension package, but
"couple of seconds x every PR forever" is waste worth skipping when it's
free to avoid) while keeping the script's default behavior — and therefore
its test suite — fully self-contained.
Alternative considered: always build fresh, ignoring any existing `dist/`.
Rejected because it silently doubles CI's build cost for no verification
benefit — CI's existing `python3 -m build` step already proves the build
succeeds from the exact same checkout state; the new check only needs to
read what that step produced.

**Metadata parsing: stdlib `zipfile`/`tarfile` + `email.parser`, no
`packaging` dependency.** A wheel's `METADATA` and an sdist's `PKG-INFO` are
RFC 822-style files; `email.parser.Parser().parsestr(...)['Version']` reads
the `Version:` header without adding a dependency. `entry_points.txt` inside
a wheel's `.dist-info/` is an INI file; stdlib `configparser` reads its
`[console_scripts]` section keys. This mirrors
`check_packaging_metadata.py`'s existing no-extra-dependency posture — the
check must work in CI right after `pip install build`, without assuming
`packaging` (a transitive dependency of `build`/`setuptools`, not a direct
one) stays resolvable.

**Version comparison: exact string equality, not PEP 440 semantic
equality.** Worktrail's own version strings (`pyproject.toml`'s
`version = "1.1.41"`) are already canonical PEP 440 releases with no local
segments, epochs, or pre-release suffixes that setuptools would normalize
differently. Exact string equality after the two values are read is
sufficient to catch real drift and keeps the comparison free of a
`packaging.version.Version` dependency. If Worktrail ever adopts a
non-canonical version string, this comparison would need revisiting —
that's a real but currently hypothetical future concern, not a reason to
add the dependency now.

**Console-script comparison: name set only, not target callables.** The
spec compares the `console_scripts` *names* the wheel exposes against
`checkout_console_scripts()`'s declared names (matching how
`check_packaging_metadata.py` already compares the installed distribution).
Comparing entry-point target strings (`module:function`) too would be
stricter, but the declared name set is what downstream consumers
(`tests/test_plugin_surface.py`, skill/command docs, `PATH` lookups) actually
depend on; a target-string mismatch with an unchanged name would still be
caught by pytest's own import-time coverage of those modules.

## Risks / Trade-offs

- [Risk] `python -m build` requires the `build` package, which is not a
  declared dependency of worktrail (CI installs it ad hoc:
  `pip install build`). → Mitigation: document this in the script's
  docstring and in `AGENTS.md` if the pre-PR gate ever adopts it (it does
  not, per this change — see proposal.md Impact); CI already installs
  `build` for the existing `python3 -m build` step, so no new CI dependency
  is introduced.
- [Risk] A second, redundant build in local/standalone runs (no
  `--wheel`/`--sdist` given) costs a few seconds. → Mitigation: acceptable
  for a pure-Python package with no compiled extensions; this mode exists
  specifically for hermetic testability and standalone use, not for the
  CI hot path.
- [Risk] `entry_points.txt`'s `[console_scripts]` section could theoretically
  be absent from a wheel with zero console scripts, and `configparser` would
  need to treat that as an empty set rather than an error. → Mitigation:
  covered by a deterministic test asserting the parser treats a missing
  section as an empty set (worktrail always has scripts today, but the
  parser should not crash on the edge case).

## Migration Plan

Purely additive: new script, new test file, one CI step extension. No
existing behavior changes, no data migration, nothing to roll back beyond
reverting the PR if the new check proves flaky.
