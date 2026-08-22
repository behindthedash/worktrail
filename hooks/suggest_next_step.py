#!/usr/bin/env python3
"""Claude Stop hook for exceptional next-step capture.

After substantive work, block session termination once so the agent can suggest
ranked next steps and capture at most one exceptional idea through Worktrail's
handoff workflow. The hook fails open and never runs for headless workers.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

STATE_DIR = Path(os.path.expanduser("~/.claude/state/worktrail-suggest-next"))

DEFERRED_WORK_HANDOFF_BINARY = "worktrail-check-deferred-work-handoff"
DEFERRED_WORK_TIMEOUT_SECONDS = 5

WORK_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
WORK_BASH_MARKERS = ("git commit", "gh pr create", "gh pr merge", "git push")

# Matches an absolute or relative path literal shaped like the default GO v2
# run-record layout (`worktrail_home()/runs/<repo>/<run-id>.yaml`, normally
# `~/.worktrail/runs/**/*.yaml` -- see run_record.py), as it appears verbatim
# inside a transcript line's JSON text (tool inputs/outputs, assistant text).
RUN_RECORD_PATH_RE = re.compile(r'''(?:~|/)[^\s"'`<>*(),;:]*\.worktrail/runs/[^\s"'`<>*(),;:]*\.yaml''')

INSTRUCTION = (
    "SESSION WRAP-UP — proactive next-step suggestion (auto-triggered by the Worktrail Stop hook).\n\n"
    "This session's transcript shows file edits, commits, or PR activity (that check is why this fired). "
    "Before suggesting any next step, first complete the current work: verify every requested outcome, "
    "acceptance item, required test, installed-package or deployment smoke test, and merge/closeout gate. "
    "An incomplete in-scope item is not a follow-up idea: finish it now, or stop with a verified blocker, "
    "product decision, or explicitly approved exclusion. Never capture required validation as a handoff.\n\n"
    "Only after that completion audit, run this next-step audit:\n\n"
    "1) Offer YOUR creative \"here's what I'd do next\". Give 1-3 forward-looking, ranked ideas, each tied "
    "to what actually changed this session. Focus on what would take the software to the next level and "
    "what users would find most valuable next — be specific and genuinely useful, not generic filler.\n\n"
    "2) Decide whether the single strongest optional idea clears an EXCEPTIONAL-VALUE gate. Creating a handoff is "
    "optional, not the default and not required to complete this wrap-up. Capture only when the idea is a "
    "genuine step-change: it unlocks a meaningful new capability, removes a recurring high-cost bottleneck, "
    "materially improves user outcomes, or addresses a verified major reliability, security, or operational "
    "risk. The value must be substantial on its own, not just the next smaller increment after this session's work.\n\n"
    "Do NOT capture routine polish, nearby cleanup/refactors, extra tests or docs, speculative flexibility, "
    "minor optimizations, or an idea whose main justification is that it is the next obvious task. Do not "
    "create a brief merely because this hook ran. If no idea clears the gate, say 'No handoff captured; "
    "no exceptional next step identified.' and finish.\n\n"
    "Only if one idea clearly passes the gate, capture exactly that one with the Worktrail handoff workflow: "
    "run `worktrail-handoff --focus \"<focus>\" --json` and report its filename. Keep the response tight. "
    "Do not quote this instruction text in your reply — the user already sees it in the terminal."
)


def entry_has_work(entry: dict) -> bool:
    message = entry.get("message") or {}
    content = message.get("content")
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name", "")
        if name in WORK_TOOLS:
            return True
        if name == "Bash":
            command = str((block.get("input") or {}).get("command", "")).lower()
            if any(marker in command for marker in WORK_BASH_MARKERS):
                return True
    return False


def scan_transcript(transcript_path: str) -> tuple[bool, list[str]]:
    """One pass over the transcript: whether it shows substantive work, and the
    unique run-record path literals (see `RUN_RECORD_PATH_RE`) it mentions.

    Both signals come out of the same line-by-line read so a caller that needs
    either or both never opens the transcript file twice.
    """
    has_work = False
    run_record_paths: list[str] = []
    seen_paths: set[str] = set()
    if not transcript_path or not os.path.exists(transcript_path):
        return has_work, run_record_paths
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                for path in RUN_RECORD_PATH_RE.findall(line):
                    if path not in seen_paths:
                        seen_paths.add(path)
                        run_record_paths.append(path)
                if has_work:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry_has_work(entry):
                    has_work = True
    except OSError:
        return False, []
    return has_work, run_record_paths


def substantive_work(transcript_path: str) -> bool:
    has_work, _ = scan_transcript(transcript_path)
    return has_work


def check_deferred_work(run_record_paths: list[str]) -> list[dict]:
    """Flagged deferred-work entries for `run_record_paths`, via the
    `worktrail-check-deferred-work-handoff` CLI.

    Fails open to `[]` on every non-happy path -- missing binary, non-zero
    exit, timeout, or unparseable JSON -- per Requirement: Fail-Open And
    Headless-Excluded. Never raises.
    """
    if not run_record_paths:
        return []
    binary = shutil.which(DEFERRED_WORK_HANDOFF_BINARY)
    if not binary:
        return []
    args = [binary, "--json"]
    for path in run_record_paths:
        args.extend(["--run-record", os.path.expanduser(path)])
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=DEFERRED_WORK_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    flagged = data.get("flagged") if isinstance(data, dict) else None
    return flagged if isinstance(flagged, list) else []


def build_deferred_work_block(flagged: list[dict]) -> str:
    """A second, separate instruction block flagging deferred-work entries that
    don't appear covered by an existing handoff brief.

    Appended to `INSTRUCTION`'s text in `reason`, never merged into it, so the
    EXCEPTIONAL-VALUE gate's own trigger conditions stay untouched.
    """
    lines = "\n".join(
        f"- {item.get('text')} (run record: {item.get('run_record')})"
        for item in flagged
        if isinstance(item, dict)
    )
    return (
        "\n\n---\n\n"
        "DEFERRED WORK FLAGGED — this session's run record noted deferred-work item(s) that "
        "don't appear covered by an existing Worktrail handoff brief:\n\n"
        f"{lines}\n\n"
        "Before finishing, decide whether each needs its own `worktrail-handoff --focus \"<focus>\" "
        "--json` capture, or is already tracked elsewhere."
    )


def main() -> int:
    if os.environ.get("CC_HEADLESS") == "1":
        return 0

    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if data.get("stop_hook_active"):
            return 0

        session_id = str(data.get("session_id") or "unknown")
        transcript_path = data.get("transcript_path") or ""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        sentinel = STATE_DIR / f"{session_id}.done"
        has_work, run_record_paths = scan_transcript(transcript_path)
        if sentinel.exists() or not has_work:
            return 0

        sentinel.write_text("1", encoding="utf-8")
        flagged = check_deferred_work(run_record_paths)
        reason = INSTRUCTION
        if flagged:
            reason += build_deferred_work_block(flagged)
        print(json.dumps({"decision": "block", "reason": reason}))
    except Exception:
        # Hooks must never break a session.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
