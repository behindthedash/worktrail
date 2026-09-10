"""A required status check must never be job-level ``if:``-skipped.

GitHub posts no check-run at all for a job skipped by a job-level ``if:``, so a
context listed in ``required_status_checks`` whose job can skip leaves branch
protection stuck on "Expected -- Waiting for status to be reported" forever, and
the PR can never merge (worktrail PR #653, where a bookkeeping-bypass job did
exactly this). The fix that incident established -- drop the job-level ``if:``
and gate each step individually -- was only ever a prose comment in ci.yml, so
nothing stopped the next job from being promoted to required while still
carrying a job-level ``if:``. This encodes it.
"""

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
RULESET = REPO_ROOT / ".github" / "rulesets" / "protect-main.json"


def _required_contexts() -> list[str]:
    ruleset = json.loads(RULESET.read_text(encoding="utf-8"))
    contexts: list[str] = []
    for rule in ruleset["rules"]:
        if rule.get("type") != "required_status_checks":
            continue
        for check in rule.get("parameters", {}).get("required_status_checks", []):
            context = check.get("context")
            if context:
                contexts.append(context)
    return contexts


def _workflow_jobs() -> dict[str, tuple[Path, dict]]:
    """Map every workflow job's display name to (workflow path, job body).

    A matrix job's check-run context is its display name plus the interpolated
    matrix suffix ("Lint, Test & Build (3.14)"), so callers match on prefix as
    well as equality -- see ``_job_for_context``.
    """
    jobs: dict[str, tuple[Path, dict]] = {}
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        wf = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_id, body in (wf.get("jobs") or {}).items():
            if not isinstance(body, dict):
                continue
            jobs[body.get("name") or job_id] = (path, body)
    return jobs


def _job_for_context(context: str) -> tuple[str, Path, dict] | None:
    jobs = _workflow_jobs()
    if context in jobs:
        path, body = jobs[context]
        return context, path, body
    # Matrix job: "<name> (<matrix values>)".
    for name, (path, body) in jobs.items():
        if context.startswith(f"{name} ("):
            return name, path, body
    return None


def test_every_required_context_resolves_to_a_committed_job():
    """A required context with no job to post it is unsatisfiable in exactly
    the same way a skipped job is -- nothing ever reports it."""
    unresolved = [c for c in _required_contexts() if _job_for_context(c) is None]
    assert not unresolved, (
        "required_status_checks contexts with no matching workflow job name: "
        f"{unresolved}"
    )


def test_no_required_check_job_is_job_level_if_skipped():
    offenders = []
    for context in _required_contexts():
        resolved = _job_for_context(context)
        if resolved is None:
            continue  # covered by the test above
        name, path, body = resolved
        if "if" in body:
            offenders.append(
                f"{path.name}:{name} (context {context!r}) if: {body['if']!r}"
            )
    assert not offenders, (
        "required status check jobs must not carry a job-level `if:` -- a "
        "skipped job posts no check-run and the required check waits forever "
        "(PR #653). Gate the individual steps instead:\n  " + "\n  ".join(offenders)
    )
