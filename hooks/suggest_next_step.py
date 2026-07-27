#!/usr/bin/env python3
"""Claude Stop hook for exceptional next-step capture.

After substantive work, block session termination once so the agent can suggest
ranked next steps and capture at most one exceptional idea through Worktrail's
handoff workflow. The hook fails open and never runs for headless workers.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

STATE_DIR = Path(os.path.expanduser("~/.claude/state/worktrail-suggest-next"))

WORK_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
WORK_BASH_MARKERS = ("git commit", "gh pr create", "gh pr merge", "git push")

INSTRUCTION = (
    "SESSION WRAP-UP — proactive next-step suggestion (auto-triggered by the Worktrail Stop hook).\n\n"
    "This session's transcript shows file edits, commits, or PR activity (that check is why this fired). "
    "Before you finish, run this audit:\n\n"
    "1) Offer YOUR creative \"here's what I'd do next\". Give 1-3 forward-looking, ranked ideas, each tied "
    "to what actually changed this session. Focus on what would take the software to the next level and "
    "what users would find most valuable next — be specific and genuinely useful, not generic filler.\n\n"
    "2) Decide whether the single strongest idea clears an EXCEPTIONAL-VALUE gate. Creating a handoff is "
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


def substantive_work(transcript_path: str) -> bool:
    if not transcript_path or not os.path.exists(transcript_path):
        return False
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry_has_work(entry):
                    return True
    except OSError:
        return False
    return False


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
        if sentinel.exists() or not substantive_work(transcript_path):
            return 0

        sentinel.write_text("1", encoding="utf-8")
        print(json.dumps({"decision": "block", "reason": INSTRUCTION}))
    except Exception:
        # Hooks must never break a session.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
