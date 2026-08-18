#!/usr/bin/env python3
"""Provider stand-in for the internal-executor dispatch lifecycle test."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path


_RUN_PATH = re.compile(r"^Run record path: (.+)$", re.MULTILINE)


def _finish_lifecycle_run(prompt: str, status: str, result: str) -> None:
    match = _RUN_PATH.search(prompt)
    if match is None:
        raise RuntimeError("seeded prompt has no run-record path")
    run = match.group(1).strip()
    subprocess.run(
        ["worktrail-run-record", "append", run, "subagents_called", "fake-child"],
        check=True,
    )
    subprocess.run(
        [
            "worktrail-run-record", "finish", run, "--status", status,
            "--merge-result", result,
        ],
        check=True,
    )


def main(argv: list[str]) -> int:
    agent, provider_args = argv[0], argv[1:]
    prompt = next((arg for arg in provider_args if "worktrail-sdd-workflow" in arg), "")
    proof = Path(os.environ["FAKE_INTERNAL_DISPATCH_PROOF"])
    argv_proof = os.environ.get("FAKE_INTERNAL_DISPATCH_ARGV_PROOF")
    if argv_proof:
        Path(argv_proof).write_text(json.dumps(provider_args))
    if "[WORKTRAIL INTERNAL DISPATCH]" not in prompt:
        proof.write_text(f"redirected:{agent}\n")
        return 3
    expected = os.environ.get(
        "FAKE_INTERNAL_DISPATCH_EXPECTED",
        "handoff:20260812-083302 route:F",
    )
    if expected not in prompt:
        proof.write_text(f"lost-context:{agent}\n")
        return 4
    if "route:F" not in prompt and "Route: F" not in prompt:
        proof.write_text(f"unseeded:{agent}\n")
        return 6
    if os.environ.get("WORKTRAIL_SKILL_DISPATCH_DEPTH") != "1":
        proof.write_text(f"unbounded:{agent}\n")
        return 5
    lifecycle = os.environ.get("FAKE_INTERNAL_DISPATCH_LIFECYCLE")
    if lifecycle:
        ownership = os.environ["FAKE_INTERNAL_DISPATCH_OWNERSHIP"]
        if ownership not in Path(_RUN_PATH.search(prompt).group(1).strip()).read_text():
            proof.write_text(f"lost-ownership:{agent}\n")
            return 7
        if lifecycle == "interrupted":
            ready = Path(os.environ["FAKE_INTERNAL_DISPATCH_READY"])

            def interrupted(_signum, _frame):
                _finish_lifecycle_run(
                    prompt, "failed_recoverable", "seeded child interrupted",
                )
                raise SystemExit(130)

            signal.signal(signal.SIGTERM, interrupted)
            ready.write_text(str(os.getpid()))
            while True:
                time.sleep(0.05)
        if lifecycle == "nonzero":
            _finish_lifecycle_run(
                prompt, "failed_recoverable", "seeded child exited nonzero",
            )
            return 9
        _finish_lifecycle_run(
            prompt, "investigation_complete", "seeded child completed",
        )
    if "FAKE_INTERNAL_DISPATCH_EXPECTED" in os.environ:
        proof.write_text(f"executed:{agent}:{expected}\n")
    else:
        proof.write_text(f"executed:{agent}:handoff:20260812-083302:route:F\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
