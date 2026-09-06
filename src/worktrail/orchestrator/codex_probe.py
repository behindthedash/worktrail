"""Live contract probe: verify the direct orchestrator codex spawn path
(`skill_dispatch.prepare_codex_child_environment` + `spawnlib.build_cmd`)
still works end-to-end, without doing any repository work.

On-demand only. See `openspec/changes/managed-codex-probe-contract/design.md`
for the full contract this module implements.
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace

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

    When auth inheritance was requested and the raise came from that half of
    the helper (see `_home_preparation_succeeds_without_auth`), the failing
    report is stamped `AUTHENTICATION` with `auth_usable=False` instead: this
    is the pre-spawn half of the authentication stage, per design.md's ordered
    classification. `inherit_codex_chatgpt_auth`'s own messages ("parent Codex
    is not authenticated with ChatGPT", the symlink refusals) are status text,
    never `auth.json` content, so they too are safe to surface verbatim.
    """
    try:
        return prepare_codex_child_environment(
            codex_home_override, inherit_auth=inherit_auth
        )
    except OSError as exc:
        if inherit_auth and _home_preparation_succeeds_without_auth(
            codex_home_override
        ):
            return ProbeReport(
                stage=StageOutcome.AUTHENTICATION,
                success=False,
                diagnostic=str(exc),
                auth_usable=False,
            )
        return ProbeReport(
            stage=StageOutcome.ENVIRONMENT_PREPARATION,
            success=False,
            diagnostic=str(exc),
        )


def _home_preparation_succeeds_without_auth(codex_home_override: str | None) -> bool:
    """Re-run only the child-home half of environment preparation, to attribute
    an `OSError` raised by `prepare_codex_child_environment` to the right stage.

    That helper does two separable things behind one call: resolve and create a
    writable child `CODEX_HOME`, then (when asked) inherit the parent's ChatGPT
    session into it. Both raise plain `OSError`, and the probe must report
    `authentication` rather than `environment_preparation` when it was the
    auth-inheritance half that failed. Matching on the message text would
    couple the probe to `skill_dispatch`'s exact wording, and reimplementing
    home selection here would break the path parity this module exists to
    prove -- so instead the same helper is re-run with `inherit_auth=False`.
    If the home half alone succeeds, the original raise can only have come
    from auth inheritance.

    Safe to repeat: home selection is deterministic and `ensure_codex_home` is
    idempotent, and `inherit_auth=False` skips the credential copy entirely, so
    this never reads or forwards `auth.json`.
    """
    try:
        prepare_codex_child_environment(codex_home_override, inherit_auth=False)
    except OSError:
        return False
    return True


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


# Normalized, non-secret labels for the authentication failures the probe
# recognizes in the nested process's own output, each paired with the patterns
# that identify it in a documented top-level `error` event's `message` (see
# https://developers.openai.com/codex/noninteractive), matched against the
# stripped, lowercased message. Only the label is ever stored or reported: the
# nested process's message text is matched and discarded, never copied onto a
# `ProbeReport`, because an authentication error can quote an account id,
# email, or token fragment.
#
# Every pattern must be specific to authentication. A phrase that also occurs
# in ordinary non-auth error text would make the probe report a confidently
# wrong stage -- sending an operator to `codex login` for a 500 or a sandbox
# denial is the exact misdiagnosis this module exists to prevent, and it would
# be silent, since the diagnostic deliberately withholds the nested message to
# reconcile it against. So `401` and `unauthorized`, which are not specific on
# their own ("retry after 401 ms", "sandbox denied: unauthorized to write
# /etc/hosts", a `req_4013abcd` request id), are only matched when they carry
# a status-code, adjacency, or credential qualifier -- never as bare
# substrings.
_AUTH_FAILURE_MARKERS: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    (
        "not_logged_in",
        tuple(
            re.compile(pattern)
            for pattern in (
                r"not logged in",
                r"codex login",
                r"login required",
                r"no credentials",
            )
        ),
    ),
    (
        "unauthorized",
        tuple(
            re.compile(pattern)
            for pattern in (
                # Names authentication outright: specific enough to stand alone.
                r"invalid api key",
                r"invalid_api_key",
                r"authentication failed",
                # `401` next to the status text it belongs to, either order.
                r"\b401\b\W{0,3}unauthorized",
                r"\bunauthorized\b\W{0,3}\(?401\b",
                # `401` as an HTTP/status code rather than an arbitrary number.
                (
                    r"\b(?:http|https|status|response|error|code)\b"
                    r"\W{0,3}(?:code\b\W{0,3})?401\b"
                ),
                # The bare status text as the whole message -- an error whose
                # entire content is "unauthorized" is the provider's refusal,
                # unlike "unauthorized" as a word inside a longer sentence
                # about something else.
                r"\Aunauthorized\W*\Z",
                # `unauthorized` qualified by nearby credential wording.
                (
                    r"\bunauthorized\b.{0,40}?"
                    r"\b(?:api[ _-]?key|token|credential|bearer|login)\b"
                ),
            )
        ),
    ),
)


def extract_auth_failure_marker(stdout: str) -> str | None:
    """Return a normalized `_AUTH_FAILURE_MARKERS` label if the nested process
    reported an authentication problem in its own output, else `None`.

    This is the post-spawn half of the `authentication` stage: `auth.json` is
    never read or forwarded to decide it (that file's readability proves only
    that a credential was copied, not that the provider accepted it). The only
    evidence used is the nested process's documented `codex exec --json`
    top-level `error` event -- and from it, only whether the message matches a
    known auth-failure pattern. The matched message itself is discarded; the
    caller gets a fixed label from this module's own vocabulary.

    A pattern only fires on wording that is specific to authentication: a
    false positive here reports a wrong stage for an ordinary infrastructure
    or sandbox error, and the operator cannot catch it, because the
    diagnostic withholds the message the label was derived from. See
    `_AUTH_FAILURE_MARKERS` for why `401` and `unauthorized` are qualified
    rather than matched as bare substrings.

    Deliberately conservative in both directions. Absence of a marker is not
    an authentication failure -- task 4.3's signal is "if available", and
    codex has no documented positive "authenticated" event to require -- so a
    silent stream yields `None` and lets a later stage classify the run.
    Malformed lines (partial output from a killed or timed-out process) are
    skipped rather than raising, matching `extract_session_started_marker`.
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
        if event.get("type") != "error":
            continue
        message = event.get("message")
        if not isinstance(message, str):
            continue
        lowered = message.strip().lower()
        for label, patterns in _AUTH_FAILURE_MARKERS:
            if any(pattern.search(lowered) for pattern in patterns):
                return label
    return None


def extract_authenticated_marker(stdout: str) -> bool | None:
    """Return `True` if the nested process's own output shows the provider
    served it a turn -- i.e. accepted the inherited credential -- else `None`.

    This is task 4.3's "(if available) non-secret `authenticated` signal", the
    positive counterpart to `extract_auth_failure_marker`. `codex exec --json`
    publishes no dedicated "authenticated" event (that is what the AC's "if
    available" hedge anticipates), but its documented stream does carry
    `turn.completed`, which is only emitted once the provider has actually
    served a turn -- something it cannot do for a credential it rejected. Its
    presence is therefore real, non-secret, positive evidence that auth is
    usable, inferred from a documented event rather than from `auth.json`,
    which is never read: that file's readability would prove only that a
    credential was copied, not that the provider accepted it.

    FLAGGED INFERENCE (not silently accepted as a fix): this is evidence of a
    served turn, not an authentication assertion codex makes itself. It cannot
    distinguish "authenticated" from "the provider did not require auth for
    this turn". Only the event *type* is inspected -- no field of it is read,
    stored, or reported.

    Never returns `False`: a stream with no served turn is not evidence that
    authentication failed, since a timeout, a sandbox denial and a 500 all
    produce the same silence. Deciding a *failure* is
    `extract_auth_failure_marker`'s job, and the two are consulted separately
    so an absent positive signal can never by itself fail the auth stage.
    Malformed lines (partial output from a killed or timed-out process) are
    skipped rather than raising, matching the other extractors here.
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
        if event.get("type") == "turn.completed":
            return True
    return None


def extract_final_reply(stdout: str) -> str | None:
    """Return the nested process's final agent reply text, or `None` if the
    documented `item.completed`/`agent_message` event never appears.

    This is task 4.4's report_back signal: `codex exec --json`'s documented
    stream emits the agent's reply as an `item.completed` event whose `item`
    carries `type: "agent_message"` and a `text` field. Only that field is
    read -- matching the other extractors here, the raw event stream is never
    stored on a `ProbeReport`. Malformed lines are skipped rather than
    raising, matching `extract_session_started_marker`.
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
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "agent_message":
            continue
        text = item.get("text")
        if isinstance(text, str):
            return text
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

    An authentication refusal in the nested process's own output is classified
    ahead of the `is_infra_failure` startup override rather than after it: such
    a run exits non-zero, so checking it later would leave the whole
    `authentication` post-spawn stage unreachable on its one realistic failure
    path. See that branch for why this preserves the ordered classification.

    A run no earlier stage (or the no-op-scope check) has already classified
    is always converted to a `REPORT_BACK` outcome before returning, never a
    raw `subprocess.CompletedProcess`: `extract_final_reply` looks for the
    documented `item.completed`/`agent_message` event and compares its text
    against `EXPECTED_REPLY`, the last check in design.md's ordering.
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
    if isinstance(result, subprocess.CompletedProcess):
        auth_failure_marker = extract_auth_failure_marker(result.stdout)
        if auth_failure_marker is not None:
            # Checked *before* the `is_infra_failure` startup override and the
            # session-started check below, deliberately. A codex run the
            # provider refuses for authentication exits non-zero, and
            # `spawnlib.is_infra_failure` reads any non-zero exit as "never got
            # off the ground" before it looks at stdout at all -- so without
            # this ordering an expired parent ChatGPT session is reported as
            # `startup` and sends the operator to debug `PATH` and spawn
            # plumbing. That is the same class of misdiagnosis
            # `_AUTH_FAILURE_MARKERS` exists to prevent, pointing the other way.
            #
            # This does not break design.md's "first failing stage" ordering: a
            # parseable, documented top-level `error` event on stdout is proof
            # the nested process *did* start and *did* reach the provider, so
            # neither `startup` nor `provider_selection` is a stage that failed
            # -- `authentication` is the earliest one that did.
            #
            # The diagnostic carries only this module's own label -- never the
            # nested process's error message, which can quote account or token
            # detail -- and no part of this check reads `auth.json`.
            result = ProbeReport(
                stage=StageOutcome.AUTHENTICATION,
                success=False,
                diagnostic=(
                    "codex probe reported an authentication failure "
                    f"({auth_failure_marker}); nested process message withheld"
                ),
                auth_usable=False,
            )
    if isinstance(result, subprocess.CompletedProcess) and is_infra_failure(
        result.returncode, result.stdout
    ):
        # `is_infra_failure` classifies a nested spawn that never got off the
        # ground (non-zero exit, no usable output) as `STARTUP`, distinct from
        # a task-level failure the nested process reported after starting. An
        # auth refusal -- which also exits non-zero -- was already claimed by
        # the branch above, so anything reaching here is a genuine startup
        # failure rather than a provider rejection.
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
        # An auth refusal and a startup failure were both already claimed by
        # the branches above, so a missing `thread.started` signal here
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
    # The positive half of the authentication stage, for a run that got far
    # enough to be eligible for one: a served turn (see
    # `extract_authenticated_marker`) means the provider accepted the
    # inherited credential. It never fails the run on its own -- absence is
    # `None`, not `False` -- it only stamps `auth_usable` onto whatever report
    # this function ends up returning for an otherwise-completed process.
    post_spawn_auth_usable: bool | None = None
    if isinstance(result, subprocess.CompletedProcess):
        post_spawn_auth_usable = extract_authenticated_marker(result.stdout)
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
                result = replace(violation, auth_usable=post_spawn_auth_usable)
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
    # report_back: reached only by a run no earlier stage (or the no-op-scope
    # check above) already classified. Whether the parsed final reply matches
    # the expected sentinel is the last thing design.md's ordering checks.
    if isinstance(result, subprocess.CompletedProcess):
        final_reply = extract_final_reply(result.stdout)
        matched = (
            final_reply is not None and final_reply.strip().lower() == EXPECTED_REPLY
        )
        result = ProbeReport(
            stage=StageOutcome.REPORT_BACK,
            success=matched,
            diagnostic=(
                "codex probe replied with the expected sentinel"
                if matched
                else (
                    "codex probe's final reply did not match the expected "
                    f"sentinel {EXPECTED_REPLY!r}"
                )
            ),
            auth_usable=post_spawn_auth_usable,
        )
    return result


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: run one probe against the real `codex` CLI and print
    the structured report as JSON, mirroring `check_agent_contract.py`'s
    `main()`.

    Exits non-zero on any outcome other than a successful `report_back`, so
    the exit code alone tells a scripted caller (e.g. a shell health check)
    whether the probe passed.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--codex-home",
        default=None,
        help=(
            "Explicit parent CODEX_HOME to probe (read-only or writable). "
            "Omit to use the ambient CODEX_HOME/default worktrail codex home."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        required=True,
        help="Wall-clock bound in seconds for the nested codex run.",
    )
    args = parser.parse_args(argv)

    prepared = prepare_environment(args.codex_home, inherit_auth=True)
    if isinstance(prepared, ProbeReport):
        report = prepared
    else:
        child_env, codex_home, automatic_home = prepared
        cmd, scratch_dir = build_probe_command()
        try:
            outcome = run_probe_command(
                cmd, scratch_dir, child_env, args.timeout, repo_dir=os.getcwd()
            )
        finally:
            shutil.rmtree(scratch_dir, ignore_errors=True)
        report = replace(outcome, codex_home=codex_home, automatic_home=automatic_home)

    print(json.dumps(dataclasses.asdict(report)))
    return 0 if report.stage == StageOutcome.REPORT_BACK and report.success else 1


if __name__ == "__main__":
    sys.exit(main())
