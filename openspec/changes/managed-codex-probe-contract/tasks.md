## 1. Probe module skeleton

- [x] 1.1 Create the probe module under `src/worktrail/orchestrator/` (e.g.
      `codex_probe.py`) with a `StageOutcome` enum/literal covering exactly
      `environment_preparation`, `startup`, `provider_selection`,
      `authentication`, `timeout`, `report_back`, and a structured report
      dataclass/NamedTuple with fields for stage, success, diagnostic
      message, and the non-sensitive signals from design.md (codex_home,
      automatic_home, provider identity, auth usable).
- [x] 1.2 Define the fixed no-op probe prompt and expected sentinel reply as
      module-level constants (mirroring `check_agent_contract.CONTRACT_PROMPT`
      / `EXPECTED_REPLY`).

## 2. Environment preparation and spawn (path parity)

- [x] 2.1 Implement the launcher's environment-preparation step by calling
      `router.skill_dispatch.prepare_codex_child_environment` directly
      (no probe-local reimplementation); on `OSError` classify as
      `environment_preparation` failure with the raised message as the
      diagnostic (already safe: `codex_home_write_remediation` never embeds
      credential content). (Requirement: Explicit read-only parent
      CODEX_HOME is honored, never silently reused)
- [x] 2.2 Implement command building via `orchestrator.spawnlib.build_cmd`
      with a `codex` `Cell`, using an isolated per-run scratch directory
      (create under `tempfile.mkdtemp`) as `cwd` — never the invoking
      repository's working tree. (Requirement: Probe enters the direct
      orchestrator Codex spawn path)
- [x] 2.3 Run the built command with `subprocess.run(..., timeout=<bound>,
      capture_output=True, text=True)`; propagate `subprocess.TimeoutExpired`
      into a `timeout` stage outcome instead of letting it raise to the
      caller. (Requirement: Probe execution is wall-clock bounded)

## 3. No-op scope enforcement

- [x] 3.1 Before spawning, snapshot the scratch directory's file listing and
      capture `git status --porcelain` output from the directory the probe
      was invoked from (the maintainer's repository working tree, not the
      scratch dir).
- [x] 3.2 After the run (success or failure), re-check both snapshots; if
      either changed, override the outcome to a failure with a diagnostic
      naming which root mutated, even if the nested process otherwise
      reported success. (Requirement: Probe performs no repository work)

## 4. Redaction and stage classification

- [x] 4.1 Derive `startup` from `spawnlib.is_infra_failure(returncode,
      stdout)` — never store raw stdout/stderr on the report object.
- [ ] 4.2 Derive `provider_selection` from a targeted, non-secret signal
      returned by the nested process (e.g. exit-code/known-marker check
      appropriate to `codex exec --json`'s documented output) — extract only
      the provider/model identity field, not the full JSON event stream.
- [ ] 4.3 Derive `authentication` from whether `prepare_codex_child_environment`
      completed auth inheritance without raising, plus (if available) a
      non-secret "authenticated" signal from the nested process's own
      output — never read or forward `auth.json` contents.
- [ ] 4.4 Derive `report_back` from whether the parsed final reply matches
      the expected no-op sentinel within the timeout.
- [ ] 4.5 Implement the ordered stage-classification function from
      design.md (environment_preparation → startup → provider_selection →
      authentication → timeout → report_back), returning the first failing
      stage or a successful `report_back` outcome. (Requirement: Every run
      reports exactly one classified stage outcome; Sensitive values are
      redacted from every reported surface)

## 5. Launcher entry point

- [ ] 5.1 Add a CLI entry point module (`argparse`-based, mirroring
      `check_agent_contract.py`'s `main()`) accepting an explicit read-only
      or writable parent `CODEX_HOME` override and a required/bounded
      `--timeout`. (Requirement: Probe is independently invocable on demand)
- [ ] 5.2 Register the entry point in `pyproject.toml`'s `[project.scripts]`
      following this repo's `worktrail-*` naming convention.
- [ ] 5.3 Print the structured report (stage, success, diagnostic, redacted
      signals) to stdout in a machine-parseable form (JSON) and exit non-zero
      on any non-`report_back`-success outcome.

## 6. Tests

- [ ] 6.1 Test that the probe's environment-preparation and command-building
      steps call `skill_dispatch.prepare_codex_child_environment` and
      `spawnlib.build_cmd` respectively (path-parity assertion), using
      mocking/monkeypatching consistent with existing `spawnlib`/
      `skill_dispatch` test patterns in `tests/`.
- [ ] 6.2 Test that a read-only parent `CODEX_HOME` fixture resolves to a
      writable, different child home, and that an unwritable-everywhere
      fixture produces an `environment_preparation` failure.
- [ ] 6.3 Test no-op scope enforcement: assert a successful run leaves a
      fixture repository's `git status --porcelain` unchanged, and assert
      that a simulated out-of-scope mutation is reported as a failure.
- [ ] 6.4 Test timeout behavior: a subprocess mock that raises
      `subprocess.TimeoutExpired` (or blocks past a short configured
      timeout) yields a `timeout` stage outcome, and confirm the launcher
      requires or defaults a bounded timeout (no unbounded run is possible).
- [ ] 6.5 Test secret redaction: feed a fixture nested-process stdout/stderr
      containing fake token/cookie-shaped content and assert none of it
      appears in the structured report, only presence/usability booleans.
- [ ] 6.6 Test each of the six stage-outcome classifications independently
      (one fixture per stage) so a future regression pinpoints which stage
      broke.
- [ ] 6.7 Add/update `tests/test_plugin_surface.py` coverage if the new
      entry point needs plugin-surface registration; otherwise confirm no
      plugin surface changes are required (this is an operator CLI, not a
      skill-facing command).

## 7. Verification

- [ ] 7.1 [e2e] Run `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`
      green.
- [ ] 7.2 [e2e] Manually invoke the new entry point locally (real `codex` CLI,
      real but disposable `CODEX_HOME`) once to confirm a live `report_back`
      success end to end, and once against a deliberately read-only
      `CODEX_HOME` to confirm the `environment_preparation` fallback and
      report shape match the spec's scenarios.
