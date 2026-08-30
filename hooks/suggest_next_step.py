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
import time
from pathlib import Path

STATE_DIR = Path(os.path.expanduser("~/.claude/state/worktrail-suggest-next"))

DEFERRED_WORK_HANDOFF_BINARY = "worktrail-check-deferred-work-handoff"
DEFERRED_WORK_TIMEOUT_SECONDS = 5

DEDUP_GATE_BINARY = "worktrail-check-durable-artifact-capture-gate"
DEDUP_GATE_TIMEOUT_SECONDS = 5

TIMING_ENV_VAR = "WORKTRAIL_STOP_HOOK_TIMING"

WORK_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
WORK_BASH_MARKERS = ("git commit", "gh pr create", "gh pr merge", "git push")

# Bash commands that modify files. A command carrying one of these markers AND
# naming a durable-artifact path counts as touching that artifact (a plain
# `cat`/`grep` mention does not).
BASH_WRITE_MARKERS = (
    ">",
    "tee ",
    "cp ",
    "mv ",
    "rm ",
    "touch ",
    "sed -i",
    "mkdir ",
    "patch ",
)

# Matches an absolute or relative path literal shaped like the default GO v2
# run-record layout (`worktrail_home()/runs/<repo>/<run-id>.yaml`, normally
# `~/.worktrail/runs/**/*.yaml` -- see run_record.py), as it appears verbatim
# inside a transcript line's JSON text (tool inputs/outputs, assistant text).
RUN_RECORD_PATH_RE = re.compile(
    r"""(?:~|/)[^\s"'`<>*(),;:]*\.worktrail/runs/[^\s"'`<>*(),;:]*\.yaml"""
)

# Matches path literals rooted at a durable follow-up artifact tree -- devkit
# specs (`docs/specs/**`) and OpenSpec changes (`openspec/changes/**`) -- as
# they appear verbatim in edit-tool path values or Bash command text, using
# the same punctuation-stopping character classes as RUN_RECORD_PATH_RE (the
# prefix additionally stops at `=` so `--flag=<path>` yields the bare path).
DURABLE_ARTIFACT_PATH_RE = re.compile(
    r"""(?:~|\.?/)?[^\s"'`<>*(),;:=]*?"""
    r"""(?:docs/specs|openspec/changes)/[^\s"'`<>*(),;:]*"""
)

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
    'run `worktrail-handoff --focus "<focus>" --json` and report its filename. Keep the response tight. '
    "Do not quote this instruction text in your reply — the user already sees it in the terminal."
)


def timing_enabled() -> bool:
    value = os.environ.get(TIMING_ENV_VAR, "")
    return value not in {"", "0", "false", "False", "no", "NO"}


def log_timing(label: str, started_at: float, **details: object) -> None:
    if not timing_enabled():
        return
    elapsed = time.perf_counter() - started_at
    parts = [f"elapsed={elapsed:.3f}s"]
    for key, value in details.items():
        parts.append(f"{key}={value}")
    print(f"[stop-hook-timing] {label} " + " ".join(parts), file=sys.stderr, flush=True)


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


def durable_artifact_paths_from_entry(entry: dict) -> list[str]:
    """Touched durable-artifact paths (`DURABLE_ARTIFACT_PATH_RE`) seen in one
    transcript entry's tool calls: edit-tool `file_path`/`notebook_path`
    values, and Bash commands that carry a write marker (`BASH_WRITE_MARKERS`).
    """
    message = entry.get("message") or {}
    content = message.get("content")
    if not isinstance(content, list):
        return []
    paths: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name", "")
        tool_input = block.get("input") or {}
        if name in WORK_TOOLS:
            candidate = str(
                tool_input.get("file_path") or tool_input.get("notebook_path") or ""
            )
            paths.extend(DURABLE_ARTIFACT_PATH_RE.findall(candidate))
        elif name == "Bash":
            command = str(tool_input.get("command", ""))
            lowered = command.lower()
            if any(marker in lowered for marker in BASH_WRITE_MARKERS):
                paths.extend(DURABLE_ARTIFACT_PATH_RE.findall(command))
    return paths


def scan_transcript(transcript_path: str) -> tuple[bool, list[str], list[str]]:
    """One pass over the transcript: whether it shows substantive work, the
    unique run-record path literals (see `RUN_RECORD_PATH_RE`) it mentions,
    and the unique touched durable-artifact paths (`docs/specs/**` /
    `openspec/changes/**`, see `DURABLE_ARTIFACT_PATH_RE`) collected from its
    edit-tool `file_path`s and Bash write-marker commands.

    All three signals come out of the same line-by-line read so a caller that
    needs any of them never opens the transcript file twice.
    """
    started_at = time.perf_counter()
    has_work = False
    run_record_paths: list[str] = []
    durable_artifact_paths: list[str] = []
    seen_paths: set[str] = set()
    if not transcript_path or not os.path.exists(transcript_path):
        log_timing(
            "scan_transcript_skip",
            started_at,
            transcript_path=transcript_path or "",
            reason="missing",
        )
        return has_work, run_record_paths, durable_artifact_paths
    try:
        line_count = 0
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line_count += 1
                line = line.strip()
                if not line:
                    continue
                for path in RUN_RECORD_PATH_RE.findall(line):
                    if path not in seen_paths:
                        seen_paths.add(path)
                        run_record_paths.append(path)
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not has_work and entry_has_work(entry):
                    has_work = True
                for path in durable_artifact_paths_from_entry(entry):
                    if path not in seen_paths:
                        seen_paths.add(path)
                        durable_artifact_paths.append(path)
    except OSError:
        log_timing(
            "scan_transcript_error",
            started_at,
            transcript_path=transcript_path,
            reason="oserror",
        )
        return False, [], []
    log_timing(
        "scan_transcript_done",
        started_at,
        transcript_path=transcript_path,
        lines=line_count,
        has_work=has_work,
        run_records=len(run_record_paths),
        durable_paths=len(durable_artifact_paths),
    )
    return has_work, run_record_paths, durable_artifact_paths


def substantive_work(transcript_path: str) -> bool:
    has_work, _, _ = scan_transcript(transcript_path)
    return has_work


def check_deferred_work(run_record_paths: list[str]) -> list[dict]:
    """Flagged deferred-work entries for `run_record_paths`, via the
    `worktrail-check-deferred-work-handoff` CLI.

    Fails open to `[]` on every non-happy path -- missing binary, non-zero
    exit, timeout, or unparseable JSON -- per Requirement: Fail-Open And
    Headless-Excluded. Never raises.
    """
    started_at = time.perf_counter()
    if not run_record_paths:
        log_timing(
            "deferred_work_skip", started_at, run_records=0, reason="no-run-records"
        )
        return []
    binary = shutil.which(DEFERRED_WORK_HANDOFF_BINARY)
    if not binary:
        log_timing(
            "deferred_work_skip",
            started_at,
            run_records=len(run_record_paths),
            reason="binary-missing",
        )
        return []
    args = [binary, "--json"]
    for path in run_record_paths:
        args.extend(["--run-record", os.path.expanduser(path)])
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=DEFERRED_WORK_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        log_timing(
            "deferred_work_timeout",
            started_at,
            run_records=len(run_record_paths),
            timeout_seconds=DEFERRED_WORK_TIMEOUT_SECONDS,
        )
        return []
    except (OSError, subprocess.SubprocessError):
        log_timing(
            "deferred_work_error",
            started_at,
            run_records=len(run_record_paths),
            reason="subprocess",
        )
        return []
    if result.returncode != 0:
        log_timing(
            "deferred_work_nonzero",
            started_at,
            run_records=len(run_record_paths),
            returncode=result.returncode,
        )
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        log_timing(
            "deferred_work_error",
            started_at,
            run_records=len(run_record_paths),
            reason="bad-json",
        )
        return []
    flagged = data.get("flagged") if isinstance(data, dict) else None
    result_flagged = flagged if isinstance(flagged, list) else []
    log_timing(
        "deferred_work_done",
        started_at,
        run_records=len(run_record_paths),
        flagged=len(result_flagged),
    )
    return result_flagged


def check_dedup_gate(
    touched_paths: list[str], run_record_paths: list[str]
) -> list[dict]:
    """Dedup-gate hits for the session's touched durable-artifact paths and
    run-record path literals, via the `worktrail-check-durable-artifact-
    capture-gate` CLI (Requirement: Downgrade-To-Suggestion On Dedup Hit and
    Fail-Open And Headless-Excluded).

    Fails open to `[]` on every non-happy path -- missing binary, non-zero
    exit, timeout, or unparseable JSON -- the same failure boundary as
    `check_deferred_work`. Never raises.
    """
    started_at = time.perf_counter()
    if not touched_paths and not run_record_paths:
        log_timing(
            "dedup_gate_skip",
            started_at,
            touched_paths=0,
            run_records=0,
            reason="no-input",
        )
        return []
    binary = shutil.which(DEDUP_GATE_BINARY)
    if not binary:
        log_timing(
            "dedup_gate_skip",
            started_at,
            touched_paths=len(touched_paths),
            run_records=len(run_record_paths),
            reason="binary-missing",
        )
        return []
    args = [binary, "--json"]
    for path in touched_paths:
        args.extend(["--touched-path", os.path.expanduser(path)])
    for path in run_record_paths:
        args.extend(["--run-record", os.path.expanduser(path)])
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=DEDUP_GATE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        log_timing(
            "dedup_gate_timeout",
            started_at,
            touched_paths=len(touched_paths),
            run_records=len(run_record_paths),
            timeout_seconds=DEDUP_GATE_TIMEOUT_SECONDS,
        )
        return []
    except (OSError, subprocess.SubprocessError):
        log_timing(
            "dedup_gate_error",
            started_at,
            touched_paths=len(touched_paths),
            run_records=len(run_record_paths),
            reason="subprocess",
        )
        return []
    if result.returncode != 0:
        log_timing(
            "dedup_gate_nonzero",
            started_at,
            touched_paths=len(touched_paths),
            run_records=len(run_record_paths),
            returncode=result.returncode,
        )
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        log_timing(
            "dedup_gate_error",
            started_at,
            touched_paths=len(touched_paths),
            run_records=len(run_record_paths),
            reason="bad-json",
        )
        return []
    hits = data.get("hits") if isinstance(data, dict) else None
    result_hits = hits if isinstance(hits, list) else []
    log_timing(
        "dedup_gate_done",
        started_at,
        touched_paths=len(touched_paths),
        run_records=len(run_record_paths),
        hits=len(result_hits),
    )
    return result_hits


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
        'Before finishing, decide whether each needs its own `worktrail-handoff --focus "<focus>" '
        "--json` capture, or is already tracked elsewhere."
    )


def _dedup_hit_line(hit: dict) -> str:
    kind = hit.get("kind")
    if kind == "session_touched_durable_artifact":
        return f"- durable artifact touched this session: {hit.get('path')}"
    if kind == "planned_run_record":
        return (
            f"- run record finished {hit.get('final_status')}: {hit.get('run_record')}"
        )
    if kind == "merged_docs_only_spec_pr":
        markers = ", ".join(hit.get("merge_markers") or [])
        spec_paths = ", ".join(hit.get("spec_paths") or [])
        return f"- merged docs-only spec PR (merge marker(s): {markers}): {spec_paths}"
    return f"- unrecognized dedup hit: {json.dumps(hit)}"


def build_dedup_gate_block(hits: list[dict]) -> str:
    """A third, additive instruction block downgrading auto-capture to a
    suggestion-only line because the session already tracks its follow-up in
    a durable artifact (Requirement: Downgrade-To-Suggestion On Dedup Hit
    and Fail-Open And Headless-Excluded).

    Appended to `INSTRUCTION`'s text in `reason`, never merged into it, the
    same additive pattern as `build_deferred_work_block`, so the
    EXCEPTIONAL-VALUE gate's own trigger conditions stay untouched.
    """
    lines = "\n".join(_dedup_hit_line(item) for item in hits if isinstance(item, dict))
    return (
        "\n\n---\n\n"
        "DEDUP GATE — this session already tracks its follow-up work in durable artifact(s), "
        "so auto-capturing a new handoff brief for the same idea would duplicate them:\n\n"
        f"{lines}\n\n"
        "Do NOT auto-capture a handoff brief here. Instead, emit a suggestion-only line naming "
        "the resume command for the tracked work (e.g. `worktrail-go <brief-id>` or the matching "
        "route command) and finish. Only with an explicit justification may you still create a "
        "brief, and that justification must be recorded inside the brief text itself as a "
        "`## Dedup justification` section naming the tracked artifact above and why a separate "
        "brief is still warranted."
    )


def main() -> int:
    if os.environ.get("CC_HEADLESS") == "1":
        return 0

    try:
        started_at = time.perf_counter()
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if data.get("stop_hook_active"):
            return 0

        session_id = str(data.get("session_id") or "unknown")
        transcript_path = data.get("transcript_path") or ""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        sentinel = STATE_DIR / f"{session_id}.done"
        has_work, run_record_paths, touched_durable_paths = scan_transcript(
            transcript_path
        )
        if sentinel.exists() or not has_work:
            log_timing(
                "main_skip",
                started_at,
                session_id=session_id,
                reason="already-seen-or-no-work",
                has_work=has_work,
            )
            return 0

        sentinel.write_text("1", encoding="utf-8")
        flagged = check_deferred_work(run_record_paths)
        reason = INSTRUCTION
        if flagged:
            reason += build_deferred_work_block(flagged)
        hits = check_dedup_gate(touched_durable_paths, run_record_paths)
        if hits:
            reason += build_dedup_gate_block(hits)
        log_timing(
            "main_emit",
            started_at,
            session_id=session_id,
            has_work=has_work,
            run_records=len(run_record_paths),
            touched_paths=len(touched_durable_paths),
            flagged=len(flagged),
            hits=len(hits),
            reason_chars=len(reason),
        )
        print(json.dumps({"decision": "block", "reason": reason}))
    except Exception:  # noqa: BLE001
        # Hooks must never break a session.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
