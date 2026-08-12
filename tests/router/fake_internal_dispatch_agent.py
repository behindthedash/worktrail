#!/usr/bin/env python3
"""Provider stand-in for the internal-executor dispatch lifecycle test."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


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
    if "FAKE_INTERNAL_DISPATCH_EXPECTED" in os.environ:
        proof.write_text(f"executed:{agent}:{expected}\n")
    else:
        proof.write_text(f"executed:{agent}:handoff:20260812-083302:route:F\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
