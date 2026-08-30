## Owning Epic

`docs/specs/epics/001-managed-codex-runtime-validation.md` — Feature 1
(`managed-codex-probe-contract`).

## Why

PR #323 isolated Codex orchestrator worker homes with process-level test
doubles, but nothing exercises the real `spawn_agent` → `prepare_codex_child_environment`
→ `build_cmd` path against a genuinely read-only inherited `CODEX_HOME`. That
runtime boundary is exactly what failed in the managed environment. Without a
deterministic, credential-safe probe that walks the same production path a
real orchestrator worker takes, maintainers cannot get release evidence for
that boundary without running a full orchestration job against a target repo.

## What Changes

- Add a no-op probe worker contract: a bounded, structured-output launcher
  that enters `src/worktrail/orchestrator/spawnlib.py`'s direct Codex
  preparation/spawn path (the same `prepare_codex_child_environment` +
  `build_cmd` + `subprocess.run` sequence `spawn_agent` uses for a codex
  cell) instead of reimplementing it.
- The probe accepts an explicitly read-only parent `CODEX_HOME` and asserts
  Worktrail's existing fallback (`select_codex_home` /
  `codex_home_write_remediation`) produces a writable child home, never
  silently reusing the read-only parent.
- The probe prompt is a fixed no-op contract (reply with a sentinel value,
  no file edits) run with the minimum filesystem roots needed for Codex
  startup; the probe verifies afterward that no path outside the isolated
  child home and its own scratch output changed.
- Execution is wall-clock bounded (a required timeout with no unbounded
  default) and every run terminates with one classified stage outcome:
  environment preparation, startup, provider selection, authentication,
  timeout, or report-back.
- All reported fields are redacted before being written anywhere (stdout,
  run record, artifact): no raw credential file contents, tokens, or
  cookies are ever captured, only presence/usability signals and stage
  classification.
- Add a console-script entry point (`worktrail-*`) so the probe is
  independently invocable, matching this repo's existing
  `check_agent_contract.py`-style on-demand diagnostics (not wired into CI
  or a schedule by this change).

## Capabilities

### New Capabilities
- `managed-codex-probe-contract`: the no-op probe worker contract, its
  structured stage-outcome report shape, redaction rules, timeout bound, and
  the safe launcher that drives it through the direct Codex spawn path.

### Modified Capabilities
(none — this change adds a new diagnostic surface; it does not change the
behavior of `spawnlib.py`, `skill_dispatch.py`, or any existing orchestrator
requirement)

## Impact

- **New code**: a probe module/entry point under `src/worktrail/orchestrator/`
  (or a sibling diagnostics module) plus its `[project.scripts]` entry and
  `tests/` coverage — no production orchestrator code is modified.
- **Depends on** (already on `main`): PR #323 (`fix: isolate Codex
  orchestrator worker homes`, merged `b6dc96e`) and the current
  `prepare_codex_child_environment` / `build_cmd` / `spawn_agent` codex path
  in `spawnlib.py` and `skill_dispatch.py`.
- **Out of scope**: re-testing every parallel-orchestrator task or launching
  implementation work in a target repository; printing, copying into
  artifacts, or otherwise exposing authentication tokens, cookies, or
  credential files; replacing unit/integration coverage around
  `spawnlib.py` or `skill_dispatch.py`; broadening nested-worker permissions
  to make the probe pass; running the probe in the managed environment
  itself (that is Feature 2, `managed-codex-runtime-attestation`) or wiring
  it into a recurring gate (Feature 3, `managed-codex-runtime-canary`).
