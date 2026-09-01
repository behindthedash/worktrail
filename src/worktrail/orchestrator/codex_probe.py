"""Live contract probe: verify the direct orchestrator codex spawn path
(`skill_dispatch.prepare_codex_child_environment` + `spawnlib.build_cmd`)
still works end-to-end, without doing any repository work.

On-demand only. See `openspec/changes/managed-codex-probe-contract/design.md`
for the full contract this module implements.
"""

from __future__ import annotations

import enum
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass

from worktrail.orchestrator.spawnlib import build_cmd, is_infra_failure
from worktrail.router.skill_dispatch import prepare_codex_child_environment
from worktrail.runtime.selection import Cell

PROBE_PROMPT = "Reply with exactly the single word: ok"
EXPECTED_REPLY = "ok"

# The real subprocess.run, captured at import: the hermetic spawn tests script
# `codex_probe.subprocess.run` with fake codex outcomes, and the pre-spawn
# git snapshot below must never consume one of those scripted outcomes (or
# feed its own output into them). Mirrors spawnlib._REAL_SUBPROCESS_RUN.
_REAL_SUBPROCESS_RUN = subprocess.run

# Wall-clock bound for the pre-spawn `git status --porcelain` snapshot call,
# matching spawnlib's own git-status timeout so the snapshot can never be the
# thing that makes run_probe_command's overall timeout contract a lie.
_SNAPSHOT_TIMEOUT_SECONDS = 15


class StageOutcome(str, enum.Enum):
    """The fixed, ordered set of stages the probe classifies a run into.

    Evaluated in this order; the first stage that fails is the reported
    outcome. `REPORT_BACK` with `success=True` is the only passing outcome.
    """

    ENVIRONMENT_PREPARATION = "environment_preparation"
    STARTUP = "startup"
    PROVIDER_SELECTION = "provider_selection"
    AUTHENTICATION = "authentication"
    TIMEOUT = "timeout"
    REPORT_BACK = "report_back"


@dataclass(frozen=True)
class ProbeReport:
    """Structured, JSON-serializable outcome of one probe run.

    Every field here is redaction-safe by construction -- see design.md's
    "Redaction by construction, not by scrubbing raw output" decision. No
    field on this object may ever hold raw subprocess stdout/stderr or
    credential file contents.
    """

    stage: StageOutcome
    success: bool
    diagnostic: str
    codex_home: str | None = None
    automatic_home: bool | None = None
    # NOT a provider/model identity value. `codex exec --json`'s documented
    # non-interactive event stream (top-level `thread.started`, `turn.started`,
    # `item.*`, `turn.completed`/`turn.failed`, `error` -- confirmed against
    # live codex-cli 0.149.1 output and
    # https://developers.openai.com/codex/noninteractive) carries no
    # model/provider-name field at all. This holds `thread.started`'s
    # `thread_id` instead: proof a session was stood up, nothing more. Task
    # 4.2's literal AC ("extract the provider/model identity field") cannot be
    # met from this documented wire format; using `thread_id` as a stand-in is
    # a flagged, acknowledged substitution -- not a claim that this value
    # identifies or validates a provider/model.
    session_started_marker: str | None = None
    auth_usable: bool | None = None


def prepare_environment(
    codex_home_override: str | None = None,
    *,
    inherit_auth: bool = True,
) -> tuple[dict[str, str], str, bool] | ProbeReport:
    """Run the probe's environment-preparation stage.

    Calls `prepare_codex_child_environment` directly -- no probe-local
    reimplementation -- so this stage stays parity-tested against the same
    helper the direct orchestrator Codex spawn path uses. On success, returns
    the `(child_env, codex_home, automatic_home)` tuple it produced. On
    `OSError` (e.g. no writable child home could be resolved or created),
    returns a failing `ENVIRONMENT_PREPARATION` report with the raised
    message as the diagnostic -- already safe to surface as-is, since
    `codex_home_write_remediation` never embeds credential content.
    """
    try:
        return prepare_codex_child_environment(
            codex_home_override, inherit_auth=inherit_auth
        )
    except OSError as exc:
        return ProbeReport(
            stage=StageOutcome.ENVIRONMENT_PREPARATION,
            success=False,
            diagnostic=str(exc),
        )


def build_probe_command() -> tuple[list[str], str]:
    """Build the probe's `codex` argv and an isolated scratch directory to
    run it from.

    Calls `spawnlib.build_cmd` directly -- no probe-local reimplementation
    -- with a `codex` `Cell`, so this stays parity-tested against the same
    helper the direct orchestrator Codex spawn path uses. The returned
    scratch directory is created fresh under `tempfile.mkdtemp`, for the
    caller to run the command with as `cwd`: never the invoking repository's
    working tree, so the nested process has no repository state to mutate.
    """
    cell = Cell(
        target="codex-probe",
        harness="codex",
        model=None,
        effort=None,
        pool="subscription",
    )
    cmd = build_cmd(PROBE_PROMPT, cell)
    scratch_dir = tempfile.mkdtemp(prefix="codex-probe-")
    return cmd, scratch_dir


def extract_session_started_marker(stdout: str) -> str | None:
    """Best-effort, explicitly flagged substitute for `provider_selection`'s
    required "provider/model identity field" -- see the FLAGGED SUBSTITUTION
    note below and on `ProbeReport.session_started_marker`.

    `build_cmd` always requests `codex exec --json`, so a started nested
    process emits one JSON object per line using the documented public
    non-interactive-mode event shape (top-level `thread.started`,
    `turn.started`, `item.*`, `turn.completed`/`turn.failed`, `error` --
    see https://developers.openai.com/codex/noninteractive, confirmed against
    live codex-cli 0.149.1 output). That stream carries no model/provider
    name field anywhere, so task 4.2's literal AC cannot be satisfied from
    it. `thread.started`'s `thread_id` is the only documented marker that
    codex actually stood up a session at all, so its presence (and
    non-emptiness) is used here as a targeted, non-secret, but *not*
    identity-bearing signal.

    FLAGGED SUBSTITUTION (not silently accepted as a fix): a thread/session
    id proves a thread was created, not which provider or model served it.
    Resolving this properly needs a planner/human decision -- either an
    upstream codex-cli change that exposes a real identity field in
    `codex exec --json`, or an explicitly sanctioned alternate signal (e.g.
    parsing `--model`/config resolution before spawn, which this probe does
    not currently do). Until then this function extracts only `thread_id`
    and nothing else -- never the full event stream -- matching design.md's
    "targeted, already-safe signals" redaction discipline. Malformed lines
    (partial output from a killed/timed-out process) are skipped rather than
    raising, since a missing signal here is exactly what should be classified
    as a `provider_selection` failure by the caller, not a crash.
    """
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") != "thread.started":
            continue
        thread_id = event.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            return thread_id
    return None


class GitStatusUnavailable(RuntimeError):
    """Raised when `git status --porcelain` exits non-zero while taking a
    no-op scope snapshot.

    A non-zero exit (not a git repo, unreadable index, `dubious ownership`
    refusal, ...) must never be read as "clean" -- that would make the
    repository half of the no-op scope check silently unverifiable while
    still reporting success. Callers must classify this as a failure.
    """


@dataclass(frozen=True)
class NoOpScopeSnapshot:
    """A point-in-time snapshot of everything the probe's no-op scope
    guarantee covers: the scratch directory's contents and the invoking
    repository's working-tree status.

    Taken once before spawning and again after the run completes; a diff
    between the two proves (or disproves) that the nested process did no
    repository work.
    """

    scratch_listing: tuple[str, ...]
    repo_git_status: str


def snapshot_no_op_scope(scratch_dir: str, repo_dir: str) -> NoOpScopeSnapshot:
    """Snapshot the probe's no-op scope before spawning.

    Captures the scratch directory's file listing (the directory the probe
    itself runs in as `cwd`) and `git status --porcelain` of `repo_dir` --
    the maintainer's repository working tree the probe was invoked from,
    never the scratch dir -- so a post-run re-snapshot can detect any
    mutation to either root.

    Raises `GitStatusUnavailable` if `git status` exits non-zero: `repo_dir`
    not being a git repository (or any other git failure) must never be
    read as "clean", or the mutation check downstream would silently
    become a no-op while still reporting success.
    """
    scratch_listing = tuple(sorted(os.listdir(scratch_dir)))
    status = _REAL_SUBPROCESS_RUN(
        ["git", "status", "--porcelain"],
        cwd=repo_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=_SNAPSHOT_TIMEOUT_SECONDS,
    )
    if status.returncode != 0:
        raise GitStatusUnavailable(
            f"git status --porcelain exited {status.returncode} in "
            f"{repo_dir}: {status.stderr.strip()}"
        )
    return NoOpScopeSnapshot(
        scratch_listing=scratch_listing,
        repo_git_status=status.stdout,
    )


def check_no_op_scope_violation(
    pre_snapshot: NoOpScopeSnapshot, scratch_dir: str, repo_dir: str
) -> ProbeReport | None:
    """Re-snapshot the probe's no-op scope after the run and diff against
    `pre_snapshot`.

    Checked regardless of whether the nested process itself reported
    success -- a nested process that mutates the maintainer's repository
    working tree while still exiting 0 and replying with the expected
    sentinel must still be classified as a no-op-scope failure, since that
    guarantee is about repository state, not the nested process's own exit
    status. Returns a failing `ProbeReport` naming which root mutated
    (`scratch_dir` and/or `repo_dir`), or `None` if neither changed.

    Raises `GitStatusUnavailable` if the post-run `git status` exits
    non-zero -- the same fail-closed rule as `snapshot_no_op_scope`: a
    failed git call must never be read as "unchanged", since that would
    make the repository half of this check silently unverifiable.
    """
    scratch_listing = tuple(sorted(os.listdir(scratch_dir)))
    status = _REAL_SUBPROCESS_RUN(
        ["git", "status", "--porcelain"],
        cwd=repo_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=_SNAPSHOT_TIMEOUT_SECONDS,
    )
    if status.returncode != 0:
        raise GitStatusUnavailable(
            f"git status --porcelain exited {status.returncode} in "
            f"{repo_dir}: {status.stderr.strip()}"
        )
    post_snapshot = NoOpScopeSnapshot(
        scratch_listing=scratch_listing,
        repo_git_status=status.stdout,
    )
    mutated_roots = []
    if post_snapshot.scratch_listing != pre_snapshot.scratch_listing:
        mutated_roots.append(f"scratch directory ({scratch_dir})")
    if post_snapshot.repo_git_status != pre_snapshot.repo_git_status:
        mutated_roots.append(f"repository working tree ({repo_dir})")
    if not mutated_roots:
        return None
    return ProbeReport(
        stage=StageOutcome.REPORT_BACK,
        success=False,
        diagnostic=(
            "no-op scope violated: " + " and ".join(mutated_roots) + " mutated"
        ),
    )


def run_probe_command(
    cmd: list[str],
    scratch_dir: str,
    child_env: dict[str, str],
    timeout: float,
    repo_dir: str,
) -> subprocess.CompletedProcess[str] | ProbeReport:
    """Snapshot the probe's no-op scope, run its `codex` command (wall-clock
    bounded by `timeout`), then re-snapshot and diff -- after the run,
    success or failure -- to detect any repository mutation.

    Calls `snapshot_no_op_scope(scratch_dir, repo_dir)` before spawning, and
    `check_no_op_scope_violation` against it after -- regardless of whether
    the spawn itself succeeded, timed out, or raised -- so a mutation left by
    a partially started process is never missed. Both snapshot calls are
    wall-clock bounded (`_SNAPSHOT_TIMEOUT_SECONDS`) and never propagate a
    raw exception: an `OSError` (e.g. `git` missing from `PATH`, `repo_dir`
    not found) or a `subprocess.TimeoutExpired` on either is classified into
    a failing `ProbeReport` instead of escaping this function as a traceback.

    The `subprocess.run` call for the probe itself mirrors
    `spawnlib.spawn_agent`'s own call exactly (same
    `cwd`/`capture_output`/`text`/`timeout`/`env` arguments), so this stays
    parity-tested against the same invocation shape the direct orchestrator
    Codex spawn path uses. Unlike that call, `subprocess.TimeoutExpired` and
    `OSError` (e.g. `codex` missing from `PATH`) are caught here and
    classified into a failing `ProbeReport` rather than propagated to the
    caller -- the probe's whole purpose is to prove the run is wall-clock
    bounded and spawnable, not to assume its caller will classify failures.

    A `StageOutcome.TIMEOUT` (or other earlier-precedence) result from the
    spawn itself keeps its stage when a no-op-scope violation is also found
    afterward, per this module's stage ordering: only a
    `subprocess.CompletedProcess` result -- i.e. the nested process actually
    ran to completion -- is eligible for a full override to
    `StageOutcome.REPORT_BACK`. Either way the violation is never silently
    dropped: when the stage is kept, the violation's which-root-mutated
    diagnostic is appended to the existing diagnostic instead.
    """
    try:
        pre_spawn_snapshot: NoOpScopeSnapshot | None = snapshot_no_op_scope(
            scratch_dir, repo_dir
        )
    except subprocess.TimeoutExpired:
        return ProbeReport(
            stage=StageOutcome.TIMEOUT,
            success=False,
            diagnostic=(
                "pre-spawn git status snapshot exceeded "
                f"{_SNAPSHOT_TIMEOUT_SECONDS}s timeout"
            ),
        )
    except OSError as exc:
        return ProbeReport(
            stage=StageOutcome.ENVIRONMENT_PREPARATION,
            success=False,
            diagnostic=str(exc),
        )
    except GitStatusUnavailable as exc:
        return ProbeReport(
            stage=StageOutcome.ENVIRONMENT_PREPARATION,
            success=False,
            diagnostic=str(exc),
        )
    try:
        result: subprocess.CompletedProcess[str] | ProbeReport = subprocess.run(
            cmd,
            check=False,
            cwd=scratch_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=child_env,
        )
    except subprocess.TimeoutExpired:
        result = ProbeReport(
            stage=StageOutcome.TIMEOUT,
            success=False,
            diagnostic=f"codex probe exceeded {timeout}s timeout",
        )
    except OSError as exc:
        result = ProbeReport(
            stage=StageOutcome.STARTUP,
            success=False,
            diagnostic=str(exc),
        )
    if isinstance(result, subprocess.CompletedProcess) and is_infra_failure(
        result.returncode, result.stdout
    ):
        # `is_infra_failure` classifies a nested spawn that never got off the
        # ground (non-zero exit, no usable output) as `STARTUP`, distinct from
        # a task-level failure the nested process reported after starting.
        # The diagnostic carries only the exit code -- never the raw
        # stdout/stderr, which may contain nested-process output.
        result = ProbeReport(
            stage=StageOutcome.STARTUP,
            success=False,
            diagnostic=(
                f"codex probe startup failed: exit code {result.returncode}, "
                "no usable output"
            ),
        )
    if isinstance(result, subprocess.CompletedProcess) and (
        extract_session_started_marker(result.stdout) is None
    ):
        # Startup already passed (the branch above would have overridden a
        # startup failure first), so a missing `thread.started` signal here
        # means the nested process ran but never reported that it stood up a
        # session at all -- classified distinctly from a startup failure per
        # design.md's ordered stage classification. See
        # `extract_session_started_marker`'s FLAGGED SUBSTITUTION note: this
        # is not a provider/model identity check, since the documented
        # stream carries no such field.
        result = ProbeReport(
            stage=StageOutcome.PROVIDER_SELECTION,
            success=False,
            diagnostic=(
                "codex probe reported no session-started signal "
                "(thread.started/thread_id) -- codex exec --json's documented "
                "output carries no provider/model identity field to check "
                "instead"
            ),
        )
    if pre_spawn_snapshot is not None:
        try:
            violation = check_no_op_scope_violation(
                pre_spawn_snapshot, scratch_dir, repo_dir
            )
        except subprocess.TimeoutExpired:
            violation = ProbeReport(
                stage=StageOutcome.TIMEOUT,
                success=False,
                diagnostic=(
                    "post-run git status snapshot exceeded "
                    f"{_SNAPSHOT_TIMEOUT_SECONDS}s timeout"
                ),
            )
        except OSError as exc:
            violation = ProbeReport(
                stage=StageOutcome.ENVIRONMENT_PREPARATION,
                success=False,
                diagnostic=str(exc),
            )
        except GitStatusUnavailable as exc:
            violation = ProbeReport(
                stage=StageOutcome.REPORT_BACK,
                success=False,
                diagnostic=f"no-op scope unverifiable: {exc}",
            )
        # A violation is checked and reported regardless of the run's own
        # outcome -- "success or failure" -- but a result already classified
        # into an earlier-precedence failing stage (e.g. TIMEOUT or a spawn
        # OSError) keeps that stage: only a still-unclassified
        # `CompletedProcess` is eligible for a full override. Otherwise the
        # violation's diagnostic is folded into the existing report so the
        # which-root-mutated detail is never silently dropped.
        if violation is not None:
            if isinstance(result, subprocess.CompletedProcess):
                result = violation
            else:
                result = ProbeReport(
                    stage=result.stage,
                    success=False,
                    diagnostic=f"{result.diagnostic}; {violation.diagnostic}",
                    codex_home=result.codex_home,
                    automatic_home=result.automatic_home,
                    session_started_marker=result.session_started_marker,
                    auth_usable=result.auth_usable,
                )
    return result
