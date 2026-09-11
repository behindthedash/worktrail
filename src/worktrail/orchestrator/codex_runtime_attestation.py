"""Managed Codex runtime attestation: run the safe `codex_probe` under a
deliberately read-only parent `CODEX_HOME`, verify the direct Codex path's
child-home isolation, readiness, identity preservation, usable inherited
authentication, and no-op report-back together, and record exactly one
sanitized entry in the owning Worktrail run record.

On-demand and advisory only. See
`openspec/changes/managed-codex-runtime-attestation/design.md`.

The probe's own spawn path is invoked, never rebuilt: environment preparation
goes through `codex_probe.prepare_environment`, auth inheritance through the
same `skill_dispatch.inherit_codex_chatgpt_auth` the direct path uses, the
command through `codex_probe.build_probe_command`, and the bounded run and
no-op-scope check through `codex_probe.run_probe_command`.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.metadata
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path

from worktrail.orchestrator import codex_probe
from worktrail.orchestrator.codex_probe import ProbeReport, StageOutcome
from worktrail.router import run_record
from worktrail.router.skill_dispatch import (
    inherit_codex_chatgpt_auth,
    resolve_parent_codex_home,
)

UNAVAILABLE = "unavailable"
_GIT_TIMEOUT_SECONDS = 15
_READONLY_MODE = 0o555
_ABS_PATH_RE = re.compile(r"(?<![\w-])(?:~|/)[^\s'\"()]*")
_CREDENTIAL_FILE_RE = re.compile(
    r"\bauth\.json(?:\.save)?\b|\.netrc\b|\bcredentials(?:\.json)?\b", re.IGNORECASE
)


@dataclass(frozen=True)
class AttestationResult:
    """The attestation's classified outcome plus the direct-runtime signals
    it attests. Every field is redaction-safe by construction; the entry
    persisted to the run record is derived from it by `build_entry`."""

    report: ProbeReport
    child_home_isolated_writable: bool
    runtime_ready: bool
    auth_usable: bool
    report_back_success: bool

    @property
    def success(self) -> bool:
        return self.report.stage == StageOutcome.REPORT_BACK and self.report.success


def sanitize_diagnostic(text: str) -> str:
    """Reduce a probe diagnostic to one short, path-free, credential-free
    line. Absolute/home paths become `<path>` and credential file names
    become `<credential-file>` so an environment or auth failure stays
    actionable (which stage, what kind of problem) without recording where a
    credential lives."""
    text = _CREDENTIAL_FILE_RE.sub("<credential-file>", text)
    text = _ABS_PATH_RE.sub("<path>", text)
    text = " ".join(text.split())
    limit = run_record.MANAGED_CODEX_ATTESTATION_DIAGNOSTIC_MAX
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text or "no diagnostic"


def worktrail_identity() -> tuple[str, str]:
    """Return `(version, commit)` for the Worktrail running this command --
    the installed distribution version and, when the package lives in a git
    checkout, its HEAD commit -- each `UNAVAILABLE` when it cannot be read."""
    try:
        version = importlib.metadata.version("worktrail")
    except importlib.metadata.PackageNotFoundError:
        version = UNAVAILABLE
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        commit = head.stdout.strip() if head.returncode == 0 else UNAVAILABLE
    except (OSError, subprocess.TimeoutExpired):
        commit = UNAVAILABLE
    if not re.fullmatch(r"[0-9a-f]{7,64}", commit):
        commit = UNAVAILABLE
    return version, commit


def create_readonly_parent_home(scratch_root: str) -> str | ProbeReport:
    """Create the deliberate read-only parent-home fixture under
    `scratch_root`. It holds no files -- never a credential -- and exists
    only so `select_codex_home` sees an inherited, non-writable
    `CODEX_HOME`. Returns a failing `ENVIRONMENT_PREPARATION` report when the
    platform cannot present the read-only condition (e.g. the mode is not
    honoured), rather than silently attesting against a writable home."""
    fixture = os.path.join(scratch_root, "readonly-parent-codex-home")
    try:
        os.mkdir(fixture, mode=0o755)
        os.chmod(fixture, _READONLY_MODE)
    except OSError as exc:
        return ProbeReport(
            stage=StageOutcome.ENVIRONMENT_PREPARATION,
            success=False,
            diagnostic=f"could not construct read-only parent home fixture: {exc}",
        )
    if os.access(fixture, os.W_OK):
        return ProbeReport(
            stage=StageOutcome.ENVIRONMENT_PREPARATION,
            success=False,
            diagnostic=(
                "read-only parent home fixture is still writable; the managed "
                "platform does not present the read-only parent condition"
            ),
        )
    return fixture


def verify_child_home(child_home: str, parent_home: str) -> str | None:
    """Return a diagnostic if `child_home` is not a distinct, writable,
    existing directory relative to the read-only `parent_home`, else None."""
    child = Path(child_home).expanduser()
    parent = Path(parent_home)
    if not child.is_dir():
        return "child CODEX_HOME does not exist as a directory"
    if (
        child.resolve() == parent.resolve()
        or parent.resolve() in child.resolve().parents
    ):
        return "child CODEX_HOME is not distinct from the read-only parent home"
    if not os.access(child, os.W_OK):
        return "child CODEX_HOME is not writable"
    return None


def check_identity(report: ProbeReport) -> ProbeReport:
    """Apply the provider/model equality rule to a run that stood up a session.

    Success requires the effective provider to equal the selected provider,
    and -- when a model was selected -- the effective model to equal it. An
    absent or mismatched effective identity is a `PROVIDER_SELECTION`
    failure: the session marker is never accepted as identity in its place.
    Runs that never reached a session, and runs the probe already classified
    as failing (authentication refusal, no-op scope violation, ...), keep
    their earlier stage and diagnostic: the identity rule only applies to a
    report that is still passing, so it never masks earlier evidence.
    """
    if report.session_started_marker is None:
        return report
    if not report.success:
        return report
    problems = []
    if report.effective_provider is None:
        problems.append("effective provider identity not reported")
    elif report.effective_provider != report.selected_provider:
        problems.append(
            f"effective provider {report.effective_provider!r} != selected "
            f"{report.selected_provider!r}"
        )
    if report.selected_model is not None:
        if report.effective_model is None:
            problems.append("effective model identity not reported")
        elif report.effective_model != report.selected_model:
            problems.append(
                f"effective model {report.effective_model!r} != selected "
                f"{report.selected_model!r}"
            )
    if not problems:
        return report
    return replace(
        report,
        stage=StageOutcome.PROVIDER_SELECTION,
        success=False,
        diagnostic=(
            "provider/model identity not attested: "
            + "; ".join(problems)
            + " (a session/thread id is not an identity substitute)"
        ),
    )


def run_attestation(timeout: float, repo_dir: str) -> AttestationResult:
    """Run one managed attestation: read-only parent fixture, probe
    environment preparation, child-home verification, auth inheritance from
    the real parent home, the bounded no-op probe run, then the identity
    rule. Never raises for a classified failure; always returns a result."""
    real_parent_home = resolve_parent_codex_home()
    scratch_root = tempfile.mkdtemp(prefix="codex-attestation-")
    fixture: str | None = None
    child_ok = False
    report: ProbeReport
    try:
        created = create_readonly_parent_home(scratch_root)
        if isinstance(created, ProbeReport):
            return _result(created, child_ok)
        fixture = created
        env_backup = {
            key: os.environ.get(key) for key in ("CODEX_HOME", "WORKTRAIL_CODEX_HOME")
        }
        os.environ["CODEX_HOME"] = fixture
        os.environ.pop("WORKTRAIL_CODEX_HOME", None)
        try:
            prepared = codex_probe.prepare_environment(None, inherit_auth=False)
        finally:
            for key, value in env_backup.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        if isinstance(prepared, ProbeReport):
            return _result(prepared, child_ok)
        child_env, codex_home, automatic_home = prepared
        problem = verify_child_home(codex_home, fixture)
        if problem is not None:
            return _result(
                ProbeReport(
                    stage=StageOutcome.ENVIRONMENT_PREPARATION,
                    success=False,
                    diagnostic=problem,
                    codex_home=codex_home,
                    automatic_home=automatic_home,
                ),
                child_ok,
            )
        child_ok = True
        # The fixture deliberately holds no credential, so inheritance comes
        # from the real parent home -- through the same helper the direct
        # Codex path uses. Its messages are status text, never auth content.
        try:
            inherit_codex_chatgpt_auth(real_parent_home, Path(codex_home).expanduser())
        except OSError as exc:
            return _result(
                ProbeReport(
                    stage=StageOutcome.AUTHENTICATION,
                    success=False,
                    diagnostic=str(exc),
                    codex_home=codex_home,
                    automatic_home=automatic_home,
                    auth_usable=False,
                ),
                child_ok,
            )
        cmd, scratch_dir = codex_probe.build_probe_command()
        try:
            outcome = codex_probe.run_probe_command(
                cmd, scratch_dir, child_env, timeout, repo_dir=repo_dir
            )
        finally:
            shutil.rmtree(scratch_dir, ignore_errors=True)
        report = replace(outcome, codex_home=codex_home, automatic_home=automatic_home)
        return _result(check_identity(report), child_ok)
    finally:
        if fixture is not None:
            try:
                os.chmod(fixture, 0o755)
            except OSError:
                pass
        shutil.rmtree(scratch_root, ignore_errors=True)


def _result(report: ProbeReport, child_ok: bool) -> AttestationResult:
    return AttestationResult(
        report=report,
        child_home_isolated_writable=child_ok,
        runtime_ready=report.session_started_marker is not None,
        auth_usable=report.auth_usable is True,
        report_back_success=(
            report.stage == StageOutcome.REPORT_BACK and report.success
        ),
    )


def build_entry(result: AttestationResult, nonce: str) -> dict:
    """Derive the run-record entry from a result: allowlisted fields only,
    sanitized diagnostic, Worktrail identity, and the supplied nonce."""
    version, commit = worktrail_identity()
    report = result.report
    return {
        "schema_version": run_record.MANAGED_CODEX_ATTESTATION_SCHEMA_VERSION,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "worktrail_version": version,
        "worktrail_commit": commit,
        "nonce": nonce,
        "selected_provider": report.selected_provider or codex_probe.PROBE_CELL.harness,
        "selected_model": report.selected_model,
        "effective_provider": report.effective_provider,
        "effective_model": report.effective_model,
        "child_home_isolated_writable": result.child_home_isolated_writable,
        "runtime_ready": result.runtime_ready,
        "auth_usable": result.auth_usable,
        "report_back_success": result.report_back_success,
        "stage": report.stage.value,
        "success": result.success,
        "diagnostic": sanitize_diagnostic(report.diagnostic),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (`worktrail-codex-runtime-attestation`): run one
    managed attestation, record exactly one entry in the owning run record,
    print it as JSON, and exit 0 only on full success. A failed attestation
    is recorded before the non-zero exit."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--run-record",
        required=True,
        help="path of the owning Worktrail run record the entry is written to",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        required=True,
        help="wall-clock bound in seconds for the nested codex run",
    )
    parser.add_argument(
        "--nonce",
        default=None,
        help="managed-session nonce distinguishing this execution (default: random)",
    )
    parser.add_argument(
        "--repo-dir",
        default=None,
        help="repository whose working tree the no-op scope check snapshots (default: cwd)",
    )
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be a positive number of seconds")
    run_path = Path(args.run_record)
    try:
        run_record._load(run_path)
    except (OSError, run_record.RunRecordFormatError) as exc:
        parser.error(f"--run-record is not a readable run record: {exc}")
    nonce = args.nonce or secrets.token_hex(8)

    result = run_attestation(args.timeout, repo_dir=args.repo_dir or os.getcwd())
    entry = build_entry(result, nonce)
    try:
        stored = run_record.record_managed_codex_attestation(run_path, entry)
    except run_record.ManagedCodexAttestationError as exc:
        print(json.dumps({"error": str(exc), "stage": entry["stage"]}))
        return 2
    print(json.dumps({"entry": stored, "report": dataclasses.asdict(result.report)}))
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
