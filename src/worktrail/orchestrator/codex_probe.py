"""Live contract probe: verify the direct orchestrator codex spawn path
(`skill_dispatch.prepare_codex_child_environment` + `spawnlib.build_cmd`)
still works end-to-end, without doing any repository work.

On-demand only. See `openspec/changes/managed-codex-probe-contract/design.md`
for the full contract this module implements.
"""

from __future__ import annotations

import enum
import os
import subprocess
import tempfile
from dataclasses import dataclass

from worktrail.orchestrator.spawnlib import build_cmd
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
    provider_identity: str | None = None
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
    return NoOpScopeSnapshot(
        scratch_listing=scratch_listing,
        repo_git_status=status.stdout,
    )


def run_probe_command(
    cmd: list[str],
    scratch_dir: str,
    child_env: dict[str, str],
    timeout: float,
    repo_dir: str,
) -> tuple[subprocess.CompletedProcess[str] | ProbeReport, NoOpScopeSnapshot | None]:
    """Snapshot the probe's no-op scope, then run its `codex` command,
    wall-clock bounded by `timeout`.

    Calls `snapshot_no_op_scope(scratch_dir, repo_dir)` before spawning --
    never after -- so the returned snapshot reflects the scratch directory's
    listing and the invoking repository's `git status --porcelain` as they
    stood immediately prior to `subprocess.run`. The caller is expected to
    re-snapshot after the run completes and diff against this pre-spawn
    snapshot to detect any repository mutation.

    The snapshot's own `git status` call is wall-clock bounded
    (`_SNAPSHOT_TIMEOUT_SECONDS`) and never propagates a raw exception: an
    `OSError` (e.g. `git` missing from `PATH`, `repo_dir` not found) or a
    `subprocess.TimeoutExpired` on that call is classified into a failing
    `ProbeReport` -- returned alongside `None` in place of a snapshot, since
    none could be taken -- rather than escaping this function as a
    traceback.

    The `subprocess.run` call for the probe itself mirrors
    `spawnlib.spawn_agent`'s own call exactly (same
    `cwd`/`capture_output`/`text`/`timeout`/`env` arguments), so this stays
    parity-tested against the same invocation shape the direct orchestrator
    Codex spawn path uses. Unlike that call, `subprocess.TimeoutExpired` is
    caught here and classified as a `timeout` stage outcome rather than
    propagated to the caller -- the probe's whole purpose is to prove the
    run is wall-clock bounded, not to assume its caller will.
    """
    try:
        pre_spawn_snapshot: NoOpScopeSnapshot | None = snapshot_no_op_scope(
            scratch_dir, repo_dir
        )
    except subprocess.TimeoutExpired:
        return (
            ProbeReport(
                stage=StageOutcome.TIMEOUT,
                success=False,
                diagnostic=(
                    "pre-spawn git status snapshot exceeded "
                    f"{_SNAPSHOT_TIMEOUT_SECONDS}s timeout"
                ),
            ),
            None,
        )
    except OSError as exc:
        return (
            ProbeReport(
                stage=StageOutcome.ENVIRONMENT_PREPARATION,
                success=False,
                diagnostic=str(exc),
            ),
            None,
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
    return result, pre_spawn_snapshot
