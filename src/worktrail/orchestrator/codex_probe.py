"""Live contract probe: verify the direct orchestrator codex spawn path
(`skill_dispatch.prepare_codex_child_environment` + `spawnlib.build_cmd`)
still works end-to-end, without doing any repository work.

On-demand only. See `openspec/changes/managed-codex-probe-contract/design.md`
for the full contract this module implements.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from worktrail.router.skill_dispatch import prepare_codex_child_environment

PROBE_PROMPT = "Reply with exactly the single word: ok"
EXPECTED_REPLY = "ok"


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
