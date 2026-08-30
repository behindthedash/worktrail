#!/usr/bin/env python3
"""Live contract check: verify each configured agent CLI's actual output is
still recognized by spawnlib's parser (`_parse_stream_json`/`is_infra_failure`).

On-demand only -- not wired into CI or a schedule. Every invocation spends real
API tokens against each agent CLI, so run this manually when a vendor CLI has
been upgraded, not per-PR.

Background: PR #366 fixed spawnlib.py silently misparsing opencode's real
`opencode run --format json` event schema -- `_parse_stream_json`/
`is_infra_failure` were written only for claude's event vocabulary; opencode
support was added to `build_cmd` without ever extending the parser, so opencode
spawns fell through to the raw-string fallback and errors were misclassified as
clean successes (a real production incident: quarantined orchestrator groups on
`pullhook`). Nothing before this script verified that claude/codex/opencode's
actual CLI output still matches what the parser expects -- a future CLI upgrade
for any configured agent could silently reintroduce this exact failure class.

Usage:
    python3 check_agent_contract.py                  # check every supported agent
    python3 check_agent_contract.py --agent claude    # check one agent
    python3 check_agent_contract.py --agent claude --agent codex
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ..runtime.selection import Cell
from . import spawnlib

CONTRACT_PROMPT = "Reply with exactly the single word: ok"
EXPECTED_REPLY = "ok"

# Top-level JSONL "type" values each agent's real CLI is documented (in
# spawnlib._parse_stream_json's docstring) to emit today. A raw event whose
# "type" falls outside this set is not itself a failure -- it's a signal worth
# printing so a future investigation starts from "the CLI added type X" instead
# of a cold read of a raw transcript.
KNOWN_EVENT_TYPES = {
    "claude": {"system", "user", "assistant", "result"},
    "opencode": {"step_start", "text", "tool_use", "step_finish", "error"},
}


class ContractCheckResult:
    def __init__(
        self, agent: str, ok: bool, detail: str, unknown_types: list[str] | None = None
    ):
        self.agent = agent
        self.ok = ok
        self.detail = detail
        self.unknown_types = unknown_types or []


def _distinct_event_types(raw: str) -> list[str]:
    types: set = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and isinstance(event.get("type"), str):
            types.add(event["type"])
    return sorted(types)


def check_agent(
    agent: str,
    cwd: str | Path,
    *,
    model: str | None = None,
    timeout: int = 120,
    prompt: str = CONTRACT_PROMPT,
    runner=None,
) -> ContractCheckResult:
    """Run one trivial live prompt through *agent*'s real CLI and assert
    spawnlib's parser recognized the response -- not the raw-string fallback.
    """
    runner = runner or subprocess.run
    output_file = None
    if agent == "codex":
        fd, output_file = tempfile.mkstemp(prefix="contract-codex-last-", suffix=".txt")
        os.close(fd)

    cell = Cell(
        target=agent, harness=agent, model=model, effort=None, pool="subscription"
    )
    cmd = spawnlib.build_cmd(prompt, cell, output_last_message=output_file)
    try:
        proc = runner(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return ContractCheckResult(agent, False, f"{agent}: timed out after {timeout}s")
    except FileNotFoundError:
        return ContractCheckResult(agent, False, f"{agent}: CLI not found on PATH")

    raw = proc.stdout or ""

    if agent == "codex":
        # codex writes its final message straight to --output-last-message,
        # bypassing stream-json parsing entirely -- verify that file's content
        # directly rather than a raw JSONL "type" vocabulary that doesn't apply
        # to this transport.
        text = ""
        if output_file:
            try:
                text = Path(output_file).read_text()
            except OSError:
                text = ""
            finally:
                try:
                    os.unlink(output_file)
                except OSError:
                    pass
        if spawnlib.is_infra_failure(proc.returncode, raw):
            return ContractCheckResult(
                agent,
                False,
                f"codex: infra failure (exit {proc.returncode}); stderr: {(proc.stderr or '')[-300:]}",
            )
        if EXPECTED_REPLY not in text.strip().lower():
            return ContractCheckResult(
                agent,
                False,
                f"codex: --output-last-message did not contain the expected reply; got: {text[:200]!r}",
            )
        return ContractCheckResult(
            agent, True, "codex: --output-last-message parsed correctly"
        )

    if spawnlib.is_infra_failure(proc.returncode, raw):
        return ContractCheckResult(
            agent,
            False,
            f"{agent}: infra failure (exit {proc.returncode}); stderr: {(proc.stderr or '')[-300:]}",
        )

    text, _usage, _tools, _skills, _sid = spawnlib._parse_stream_json(raw)
    seen_types = set(_distinct_event_types(raw))
    known = KNOWN_EVENT_TYPES.get(agent, set())
    unknown = sorted(seen_types - known)

    if raw.strip() and text.strip() == raw.strip():
        # _parse_stream_json's default is `result_text = raw`; it only changes
        # on a recognized "result"/"text" event. Text staying equal to the full
        # raw transcript is exactly the raw-fallback symptom PR #366 fixed.
        return ContractCheckResult(
            agent,
            False,
            f"{agent}: parser fell back to raw output (schema not recognized); "
            f"saw types {sorted(seen_types)!r}, parser recognizes {sorted(known)!r}",
            unknown_types=unknown,
        )

    if EXPECTED_REPLY not in text.strip().lower():
        return ContractCheckResult(
            agent,
            False,
            f"{agent}: parsed text did not contain the expected reply; got: {text[:200]!r}",
            unknown_types=unknown,
        )

    detail = f"{agent}: parsed correctly (types seen: {sorted(seen_types)!r})"
    if unknown:
        detail += f"; note -- unrecognized types seen but non-fatal: {unknown!r}"
    return ContractCheckResult(agent, True, detail, unknown_types=unknown)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--agent",
        action="append",
        choices=sorted(spawnlib.SUPPORTED_AGENTS),
        help="agent(s) to check (default: every supported agent)",
    )
    parser.add_argument(
        "--cwd", default=".", help="working directory to spawn the check from"
    )
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)

    agents = args.agent or sorted(spawnlib.SUPPORTED_AGENTS)
    results = [check_agent(a, args.cwd, timeout=args.timeout) for a in agents]

    ok = True
    for r in results:
        marker = "OK" if r.ok else "FAIL"
        print(f"[{marker}] {r.detail}")
        if not r.ok:
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
