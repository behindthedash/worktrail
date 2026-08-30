## Context

`src/worktrail/orchestrator/spawnlib.py`'s `spawn_agent` already has the
production path we need parity with: for a `codex` cell it calls
`skill_dispatch.prepare_codex_child_environment(codex_home_override,
inherit_auth=inherit_auth)` to get `(child_env, codex_home, automatic_home)`,
then `build_cmd(prompt, cell, ...)` to build the argv
(`["codex", "exec", "--json", "-s", "danger-full-access", ...]`), then runs it
with `subprocess.run(cmd, cwd=..., env=child_env, timeout=timeout,
capture_output=True, text=True)`.

`prepare_codex_child_environment` (in `router/skill_dispatch.py`) already
implements the read-only-parent-home fallback this feature needs to verify:
`select_codex_home` keeps an inherited `CODEX_HOME` only when
`codex_home_write_remediation` finds it writable, otherwise falls back to
`default_worktrail_codex_home()` (`~/.worktrail/codex-home`) and reports
`automatic_home=True`. Auth inheritance (`inherit_codex_chatgpt_auth`) copies
only a verified, owner-only, size-bounded `auth.json` into the child home
after confirming `codex login status` reports the ChatGPT ok line — it never
returns file contents to a caller.

`check_agent_contract.py` is the closest existing precedent: an on-demand,
not-CI-wired script that builds a `Cell`, calls `spawnlib.build_cmd`, runs the
subprocess directly, and classifies the result via `spawnlib.is_infra_failure`
— but it targets all three harnesses generically and only proves the response
parser recognizes real CLI output. This feature narrows to codex specifically
and adds the environment-preparation/auth/timeout/no-op axes that
`check_agent_contract.py` does not check.

## Goals / Non-Goals

**Goals:**
- Reuse `prepare_codex_child_environment` and `build_cmd` unmodified as the
  probe's environment-preparation and command-building steps — verified by
  the probe calling them directly, not a copy.
- Produce one structured, JSON-serializable stage-outcome report per run,
  safe to persist or print without a secondary redaction pass.
- Keep the probe a pure diagnostic: no orchestrator run state, run journal,
  or task plan is read or written.

**Non-Goals:**
- Running the probe in the managed environment itself, or building the
  managed-session harness that invokes it (Feature 2).
- Any recurring/scheduled invocation, retry policy, or alerting (Feature 3).
- Testing claude/opencode harness parity — this feature is codex-only,
  matching the epic's runtime boundary.
- Changing `spawnlib.py` or `skill_dispatch.py` behavior; the probe is a new
  caller, not a modification to the existing helpers.

## Decisions

**Reuse, don't reimplement, the two production entry points.** The probe
imports and calls `prepare_codex_child_environment` and `build_cmd` directly
from their existing modules. Alternative considered: write a probe-specific
"minimal" environment-prep function covering only what the probe needs.
Rejected — that would test the probe's own logic, not the production path,
defeating the point of Feature 1 (path-parity evidence). The whole value of
this feature is that a future change to the real preparation/spawn helpers is
automatically exercised by the probe without the probe needing an update.

**No-op contract is a fixed sentinel-reply prompt, not a task-shaped one.**
Mirrors `check_agent_contract.py`'s `CONTRACT_PROMPT` pattern (a fixed string,
an expected-reply check) rather than anything resembling a real orchestrator
task prompt (report-back JSON block, tool use expectations). This keeps the
contract minimal and avoids accidentally exercising report-back parsing paths
that belong to `dispatch.py`, not this probe.

**Minimum filesystem roots.** The probe runs `codex exec` with `cwd` set to a
dedicated, empty scratch directory created for the run (not the invoking
repository's working tree), so "no repository work" is structurally true
rather than only verified after the fact. The post-run mutation check (see
spec) is a second, independent line of defense — it walks the scratch
directory's own file listing before/after, and separately confirms the
invoking repository's working tree (`git status --porcelain` from the
directory the probe was invoked from) is unchanged. Using an isolated scratch
`cwd` is why the `-s danger-full-access` sandbox flag `build_cmd` already
supplies is safe here: full access is scoped to a throwaway directory the
probe owns, not the maintainer's repository.

**Redaction by construction, not by scrubbing raw output.** The probe never
copies raw subprocess stdout/stderr into its structured report. It derives
each report field (provider identity, auth usable, stage outcome) from
targeted, already-safe signals: `automatic_home`/`codex_home` (paths, never
file contents) from `prepare_codex_child_environment`; a boolean derived from
whether `inherit_codex_chatgpt_auth` raised; the nested process's exit code
and `spawnlib.is_infra_failure` classification; and the parsed no-op reply
text (expected to be a short fixed sentinel, not sensitive). Raw
stdout/stderr are held only in memory for the duration of stage
classification and are never assigned into the report dict or logged. This
mirrors `skill_dispatch._read_private_regular_file`'s existing discipline of
never returning credential bytes to a caller — the probe simply never
requests them in the first place, since `inherit_codex_chatgpt_auth` already
keeps `auth.json` contents internal to that function.

**Stage classification is a fixed six-value enum, decided in order.** The
probe evaluates, in sequence: did environment preparation raise
(`environment_preparation`) → did the subprocess start and produce non-infra
output before the timeout (`startup`, via `spawnlib.is_infra_failure`) → did
the nested process report a provider identity (`provider_selection`) → was
auth usable (`authentication`) → did the process exceed the timeout
(`timeout`, via `subprocess.TimeoutExpired`) → did the final reply match the
expected no-op sentinel (`report_back`). The first stage that fails is the
reported outcome; earlier stages passing is implicit in reaching a later
one. Alternative considered: a bitmask/multi-failure report. Rejected — the
epic's success metric asks for single-stage triage ("classified at least as
X"), and a single ordered classification is simpler to test and to read.

## Risks / Trade-offs

- **Scratch-directory `cwd` diverges from a real task worktree's directory
  shape** (no `.git`, no task files) → Mitigation: the goal is path parity
  for environment preparation and spawn, not worktree-shape parity; Feature 2
  is where the managed environment's real shape gets exercised end-to-end.
- **A future change to `prepare_codex_child_environment` or `build_cmd`
  signatures breaks the probe's direct call** → Mitigation: this is the
  intended failure mode (import/type error visible immediately in the
  probe's own test run) rather than a silent divergence; acceptable because
  it surfaces at the same time the production helpers change, not later in
  a managed session.
- **Redaction-by-construction still requires a human check that every
  reported field really is safe when a new field is added later** →
  Mitigation: the spec's scenario "raw process output contains
  credential-shaped content" gives an explicit test seam (feed a fixture
  stdout containing a fake token, assert it is absent from the report) that
  a future field addition should extend rather than bypass.
