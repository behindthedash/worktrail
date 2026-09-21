#!/usr/bin/env python3
"""Hand-run smoke harness: does a live evaluator produce a usable `refuted_span`?

`queue_triage`'s Step 2b asks a `work-directly` verdict to quote, verbatim, the
span of the brief's own focus it refutes -- `apply_verdicts()` then rewrites
exactly that span. A span the evaluator paraphrased instead of copied is a
silent no-op at apply time, and no offline test can tell us whether a real
model obeys the rule: only a real run can.

So this module is **run by hand**, exactly like
`scripts/regenerate_classifier_corpus.py`::

    python3 -m worktrail.workqueue.triage_smoke

It builds one fixture brief -- a focus carrying a single refutable claim about
this repo plus otherwise-valid directly-actionable work -- under a throwaway
queue root (never the operator's real ``$WORK_QUEUE_DIR``), spawns one real
evaluator over it via `queue_triage.evaluate_group()`, writes the recording to
``tests/fixtures/triage_evaluator_answers.json``, and exits non-zero naming
every Step 2b condition the run missed. That default path is resolved from the
*installed* package root -- the canonical checkout, not a branch worktree -- so
pass ``--out`` when the recording has to land on a branch. The committed
recording is what ``tests/workqueue/test_queue_triage_live_replay.py`` replays offline; it is
recorded data, never hand-authored.

**Never run this from pytest.** `record_run()` spawns a real agent, costs
tokens, and is non-deterministic. `tests/workqueue/test_triage_smoke.py` covers
this module offline by injecting a fake `evaluate_group`; nothing in the test
suite may call the real one.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import queue_triage as qt

FIXTURE_REPO = "behindthedash/worktrail"

#: The refutable half of the fixture focus: there is no such console script
#: (AGENTS.md: the harness is run as `python3 -m ...`, deliberately unregistered).
REFUTABLE_CLAIM = (
    "`worktrail-triage-smoke` is already registered as a console script in "
    "`pyproject.toml`, so the smoke harness runs without `python3 -m`."
)

#: The otherwise-valid, directly-actionable half -- still worth doing after the
#: claim above is refuted, so a correct run is `work-directly`, not `keep`.
#: It deliberately names a long-stable file that exists on every branch (never
#: one this change introduces), so the work reads as outstanding no matter which
#: checkout `record_run()`'s `cwd` resolves to.
ACTIONABLE_WORK = (
    "Separately, expand `src/worktrail/workqueue/slug.py`'s module docstring to "
    "state the two truncation limits `fallback_slugify()` applies: at most 5 "
    "words and at most 60 characters."
)

FIXTURE_FOCUS = f"{REFUTABLE_CLAIM} {ACTIONABLE_WORK}"

FAIL_VERDICT = "verdict-not-work-directly"
FAIL_SPAN_MISSING = "refuted-span-missing"
FAIL_SPAN_NOT_VERBATIM = "refuted-span-not-verbatim"

DEFAULT_OUT = (
    qt._worktrail_repo_root() / "tests" / "fixtures" / "triage_evaluator_answers.json"
)

_RECORDING_DESCRIPTION = (
    "One real evaluator run over the triage_smoke fixture brief, recorded by "
    "`python3 -m worktrail.workqueue.triage_smoke`. Replayed offline by "
    "tests/workqueue/test_queue_triage_live_replay.py. Recorded data -- "
    "re-record by re-running the harness; hand-editing `raw_text` would make "
    "the replay assert behavior no model produced."
)


def build_fixture_brief(queue_root: str | Path) -> Path:
    """Write the single fixture intake brief under `queue_root`/queue/.

    `queue_root` is a throwaway directory created by the caller -- never the
    operator's real ``$WORK_QUEUE_DIR``, whose briefs are live work.
    """
    queue = Path(queue_root) / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    created = datetime.date.today().isoformat()  # noqa: DTZ011
    path = queue / f"{created.replace('-', '')}-000000-triage-smoke-fixture.md"
    path.write_text(
        "---\n"
        # JSON-quoted: the focus opens with a backtick, a reserved YAML indicator.
        f"focus: {json.dumps(FIXTURE_FOCUS)}\n"
        "status: queued\n"
        f"repo: {FIXTURE_REPO}\n"
        f"created: {created}\n"
        "---\n\n"
        "Fixture brief for the Step 2b span smoke harness.\n",
        encoding="utf-8",
    )
    return path


def record_run(
    queue_root: str | Path,
    *,
    cwd: str | Path | None = None,
    agent: str = "claude",
    evaluate: Callable[..., list[dict]] | None = None,
) -> dict[str, Any]:
    """Build the fixture brief, run one evaluator over it, return the recording.

    `evaluate` is injectable purely so the offline tests can exercise this
    function without a live call; it defaults to the real
    `queue_triage.evaluate_group()`.
    """
    evaluate = evaluate or qt.evaluate_group
    cwd = Path(cwd) if cwd is not None else qt._worktrail_repo_root()
    path = build_fixture_brief(queue_root)
    prior = os.environ.get("WORK_QUEUE_DIR")
    os.environ["WORK_QUEUE_DIR"] = str(queue_root)
    try:
        results = evaluate(FIXTURE_REPO, [path], agent=agent, cwd=cwd)
        focus = qt._brief_focus(path)
    finally:
        if prior is None:
            os.environ.pop("WORK_QUEUE_DIR", None)
        else:
            os.environ["WORK_QUEUE_DIR"] = prior
    return {
        "_meta": {
            "description": _RECORDING_DESCRIPTION,
            "agent": agent,
            "captured": datetime.date.today().isoformat(),  # noqa: DTZ011
        },
        "brief_id": path.stem,
        "focus": focus,
        "raw_text": results[0]["raw_text"],
    }


def adjudicate(recording: dict[str, Any]) -> list[str]:
    """Return the Step 2b conditions this recording failed, `[]` when it passed.

    The three conditions, in order: the verdict is `work-directly`; it carries a
    `refuted_span`; and that span is a verbatim substring of the focus the
    evaluator was actually shown. `FAIL_SPAN_NOT_VERBATIM` is reported only when
    a span was present to check.
    """
    brief_id = recording["brief_id"]
    verdict = qt.parse_verdicts(recording["raw_text"], [brief_id])[0]
    failures = []
    if verdict.verdict != "work-directly":
        failures.append(FAIL_VERDICT)
    span = verdict.refuted_span
    if not span:
        failures.append(FAIL_SPAN_MISSING)
    elif span not in recording["focus"]:
        failures.append(FAIL_SPAN_NOT_VERBATIM)
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m worktrail.workqueue.triage_smoke",
        description=__doc__.splitlines()[0],
    )
    parser.add_argument("--agent", default="claude")
    parser.add_argument(
        "--cwd", default=None, help="repo checkout the evaluator runs in"
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="triage-smoke-") as tmp:
        recording = record_run(tmp, cwd=args.cwd, agent=args.agent)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(recording, indent=1) + "\n", encoding="utf-8")
    print(f"recording written to {out}")

    failures = adjudicate(recording)
    if failures:
        print("Step 2b conditions failed: " + ", ".join(failures), file=sys.stderr)
        return 1
    print("Step 2b contract satisfied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
