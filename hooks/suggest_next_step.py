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
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

STATE_DIR = Path(os.path.expanduser("~/.claude/state/worktrail-suggest-next"))

DEFERRED_WORK_HANDOFF_BINARY = "worktrail-check-deferred-work-handoff"
DEFERRED_WORK_TIMEOUT_SECONDS = 5

DEDUP_GATE_BINARY = "worktrail-check-durable-artifact-capture-gate"
DEDUP_GATE_TIMEOUT_SECONDS = 5

PR_LEDGER_BINARY = "worktrail-pr-ledger"
PR_LEDGER_TIMEOUT_SECONDS = 5

WORK_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
WORK_BASH_MARKERS = ("git commit", "gh pr create", "gh pr merge", "git push")

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
    "1) DEFECTS — mandatory, not value-gated. For every verified defect, bug, or regression you "
    "discovered this session and did not fix (reproduced or directly evidenced by code, logs, or test "
    "output — not a hypothesis), capture one brief per distinct verified defect: run "
    '`worktrail-handoff --focus "<defect>" --json` for each and report every filename. The '
    "EXCEPTIONAL-VALUE gate below does not apply to defects. A defect inside the current request is "
    "not a handoff: fix it now per the completion audit above.\n\n"
    "2) Offer YOUR creative \"here's what I'd do next\". Give 1-3 forward-looking, ranked ideas, each tied "
    "to what actually changed this session. Focus on what would take the software to the next level and "
    "what users would find most valuable next — be specific and genuinely useful, not generic filler.\n\n"
    "3) Decide whether the single strongest optional idea clears an EXCEPTIONAL-VALUE gate. This gate, "
    "including the exclusions below, applies only to forward-looking ideas. Creating a handoff is "
    "optional, not the default and not required to complete this wrap-up. Capture only when the idea is a "
    "genuine step-change: it unlocks a meaningful new capability, removes a recurring high-cost bottleneck, "
    "materially improves user outcomes, or addresses a verified major reliability, security, or operational "
    "risk. The value must be substantial on its own, not just the next smaller increment after this session's work.\n\n"
    "Do NOT capture routine polish, nearby cleanup/refactors, extra tests or docs, speculative flexibility, "
    "minor optimizations, or an idea whose main justification is that it is the next obvious task. Do not "
    "create a brief merely because this hook ran. If no idea clears the gate and no defect brief was "
    "captured, say 'No handoff captured; no exceptional next step identified.' and finish.\n\n"
    "Only if one idea clearly passes the gate, capture exactly that one idea with the Worktrail handoff workflow: "
    'run `worktrail-handoff --focus "<focus>" --json` and report its filename. Keep the response tight. '
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


HEREDOC_RE = re.compile(r"""(?<!<)<<(-?)\s*(['"]?)([A-Za-z_][\w.-]*)\2""")
ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
SHELL_PUNCTUATION = set("();<>|&")


def _strip_heredoc_bodies(command: str) -> str:
    kept: list[str] = []
    pending: list[tuple[bool, str]] = []
    for line in command.split("\n"):
        if pending:
            strip_tabs, delimiter = pending[0]
            if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                pending.pop(0)
            continue
        kept.append(line)
        pending = [(dash == "-", word) for dash, _, word in HEREDOC_RE.findall(line)]
    return "\n".join(kept)


def _simple_command_writes(words: list[str]) -> list[str]:
    while words and ASSIGNMENT_RE.match(words[0]):
        words = words[1:]
    if not words:
        return []
    verb, args = words[0], words[1:]
    if verb == "git" and args[:1] in (["mv"], ["rm"]):
        verb, args = args[0], args[1:]
    operands: list[str] = []
    in_place = has_script = end_of_options = skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
        elif end_of_options or not arg.startswith("-"):
            operands.append(arg)
        elif arg == "--":
            end_of_options = True
        elif verb == "sed" and arg.startswith(("-i", "--in-place")):
            in_place = True
        elif verb == "sed" and arg in ("-e", "--expression", "-f", "--file"):
            has_script = skip_next = True
        elif verb == "sed" and arg.startswith(("-e", "-f", "--expression=", "--file=")):
            has_script = True
    if verb in ("tee", "mv", "rm", "touch", "mkdir"):
        return operands
    if verb == "cp":
        return operands[-1:]
    if verb == "patch":
        return operands[:1]
    if verb == "sed" and in_place:
        return operands if has_script else operands[1:]
    return []


def _bash_write_targets(command: str) -> list[str]:
    """Paths a Bash command writes: redirect targets plus the written operands
    of `tee`/`cp`/`mv`/`rm`/`touch`/`mkdir`/`sed -i`/`patch` (and `git mv`/
    `git rm`). Fails open to `[]` on anything it cannot parse; never raises.
    """
    try:
        lexer = shlex.shlex(
            _strip_heredoc_bodies(command), posix=True, punctuation_chars=True
        )
        lexer.whitespace = " \t\r"
        lexer.wordchars += ":@%+,"
        tokens = list(lexer)
        targets: list[str] = []
        words: list[str] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            index += 1
            if token == "\n" or (token and set(token) <= SHELL_PUNCTUATION):
                if ">" in token or "<" in token:
                    target = tokens[index] if index < len(tokens) else ""
                    index += 1
                    if ">" in token:
                        if words and words[-1].isdigit():
                            words.pop()
                        duplication = token.endswith("&") and (
                            target.isdigit() or target == "-"
                        )
                        if not duplication and target != "/dev/null":
                            targets.append(target)
                    continue
                targets.extend(_simple_command_writes(words))
                words = []
            else:
                words.append(token)
        targets.extend(_simple_command_writes(words))
        return targets
    except Exception:  # noqa: BLE001
        return []


def durable_artifact_paths_from_entry(entry: dict) -> list[str]:
    """Touched durable-artifact paths (`DURABLE_ARTIFACT_PATH_RE`) seen in one
    transcript entry's tool calls: edit-tool `file_path`/`notebook_path`
    values, and the paths Bash commands actually write (`_bash_write_targets`).
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
            for target in _bash_write_targets(command):
                paths.extend(DURABLE_ARTIFACT_PATH_RE.findall(target))
    return paths


def bash_commands_from_entry(entry: dict) -> list[str]:
    """Raw Bash tool-call `command` text in one transcript entry, so the
    dedup check's merge-marker detection (Requirement: Merged Docs-Only Spec
    PR Detection Is Transcript-Local) can scan the same commands
    `durable_artifact_paths_from_entry` already inspects for write targets,
    without a second transcript read.
    """
    message = entry.get("message") or {}
    content = message.get("content")
    if not isinstance(content, list):
        return []
    commands: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        if block.get("name") != "Bash":
            continue
        command = (block.get("input") or {}).get("command")
        if command:
            commands.append(str(command))
    return commands


def scan_transcript(
    transcript_path: str,
) -> tuple[bool, list[str], list[str], list[str]]:
    """One pass over the transcript: whether it shows substantive work, the
    unique run-record path literals (see `RUN_RECORD_PATH_RE`) it mentions,
    the unique touched durable-artifact paths (`docs/specs/**` /
    `openspec/changes/**`, see `DURABLE_ARTIFACT_PATH_RE`) collected from its
    edit-tool `file_path`s and Bash write targets, and every Bash tool call's
    raw command text.

    All four signals come out of the same line-by-line read so a caller that
    needs any of them never opens the transcript file twice.
    """
    has_work = False
    run_record_paths: list[str] = []
    durable_artifact_paths: list[str] = []
    bash_commands: list[str] = []
    seen_paths: set[str] = set()
    if not transcript_path or not os.path.exists(transcript_path):
        return has_work, run_record_paths, durable_artifact_paths, bash_commands
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
                bash_commands.extend(bash_commands_from_entry(entry))
    except OSError:
        return False, [], [], []
    return has_work, run_record_paths, durable_artifact_paths, bash_commands


def substantive_work(transcript_path: str) -> bool:
    has_work, _, _, _ = scan_transcript(transcript_path)
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
            check=False,
            capture_output=True,
            text=True,
            timeout=DEFERRED_WORK_TIMEOUT_SECONDS,
        )
    except OSError, subprocess.TimeoutExpired, subprocess.SubprocessError:
        return []
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    flagged = data.get("flagged") if isinstance(data, dict) else None
    return flagged if isinstance(flagged, list) else []


def check_dedup_gate(
    touched_paths: list[str],
    run_record_paths: list[str],
    bash_commands: list[str] | None = None,
) -> list[dict]:
    """Dedup-gate hits for the session's touched durable-artifact paths,
    run-record path literals, and Bash command text, via the
    `worktrail-check-durable-artifact-capture-gate` CLI (Requirement:
    Downgrade-To-Suggestion On Dedup Hit, Merged Docs-Only Spec PR Detection
    Is Transcript-Local, and Fail-Open And Headless-Excluded).

    Fails open to `[]` on every non-happy path -- missing binary, non-zero
    exit, timeout, or unparseable JSON -- the same failure boundary as
    `check_deferred_work`. Never raises.
    """
    bash_commands = bash_commands or []
    if not touched_paths and not run_record_paths:
        return []
    binary = shutil.which(DEDUP_GATE_BINARY)
    if not binary:
        return []
    args = [binary, "--json"]
    for path in touched_paths:
        args.extend(["--touched-path", os.path.expanduser(path)])
    for path in run_record_paths:
        args.extend(["--run-record", os.path.expanduser(path)])
    for command in bash_commands:
        args.extend(["--bash-command", command])
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=DEDUP_GATE_TIMEOUT_SECONDS,
        )
    except OSError, subprocess.TimeoutExpired, subprocess.SubprocessError:
        return []
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    hits = data.get("hits") if isinstance(data, dict) else None
    return hits if isinstance(hits, list) else []


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
        "Do NOT auto-capture a handoff brief for that tracked work: this downgrade applies "
        "only for the follow-up those artifacts already track. It never suppresses capturing a "
        "distinct verified defect those artifacts do not track; capture each such defect as the "
        "base instruction requires. For the tracked work, instead emit a suggestion-only line naming "
        "the resume command for the tracked work (e.g. `worktrail-go <brief-id>` or the matching "
        "route command) and finish. Only with an explicit justification may you still create a "
        "brief, and that justification must be recorded inside the brief text itself as a "
        "`## Dedup justification` section naming the tracked artifact above and why a separate "
        "brief is still warranted."
    )


def query_open_prs(session_id: str) -> list[dict]:
    """Non-terminal PR ledger entries owned by `session_id`, via the
    read-only `worktrail-pr-ledger session --session-id <id>` CLI, which
    prints a JSON list of ledger entries (each carrying `url`, `session_id`,
    and `last_state`) and already excludes terminal (merged/closed) ones
    (Requirement: Interactive session end is guarded by its open PRs).

    Fails open to `[]` on every non-happy path -- missing binary, non-zero
    exit, timeout, or unparseable JSON -- the same failure boundary as
    `check_deferred_work`. Never raises.
    """
    binary = shutil.which(PR_LEDGER_BINARY)
    if not binary:
        return []
    try:
        result = subprocess.run(
            [binary, "session", "--session-id", session_id],
            check=False,
            capture_output=True,
            text=True,
            timeout=PR_LEDGER_TIMEOUT_SECONDS,
        )
    except OSError, subprocess.TimeoutExpired, subprocess.SubprocessError:
        return []
    if result.returncode != 0:
        return []
    try:
        entries = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(entries, list):
        return []
    return [
        item
        for item in entries
        if isinstance(item, dict)
        and item.get("url")
        and item.get("session_id") == session_id
    ]


def build_open_pr_block(entries: list[dict]) -> str:
    """The blocking instruction emitted instead of `INSTRUCTION` when this
    session still owns non-terminal PR(s): name each PR and tell the agent to
    resume its CI/recovery work rather than ending the session.
    """
    lines = "\n".join(
        f"- {item.get('url')}"
        + (f" ({item.get('last_state')})" if item.get("last_state") else "")
        for item in entries
    )
    return (
        "OPEN PR RECOVERY — this session opened pull request(s) that are not yet merged "
        "or closed (Worktrail PR ledger):\n\n"
        f"{lines}\n\n"
        "Do not end the session with them unresolved. Resume the landing pipeline for each: "
        "check CI and review state (`gh pr checks <url>` / `gh pr view <url>`), fix red checks "
        "or blocked review, and drive it to merge (or close it deliberately). Only once every PR "
        "above is terminal may the session wrap up. Do not quote this instruction text in your reply."
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
        has_work, run_record_paths, touched_durable_paths, bash_commands = (
            scan_transcript(transcript_path)
        )
        if data.get("session_id"):
            # Guard before the ordinary sentinel is checked or written so an
            # unresolved PR cannot be bypassed by consuming that sentinel
            # first. Fires on every stop while a PR stays non-terminal.
            open_prs = query_open_prs(session_id)
            if open_prs:
                print(
                    json.dumps(
                        {"decision": "block", "reason": build_open_pr_block(open_prs)}
                    )
                )
                return 0
        if sentinel.exists() or not has_work:
            return 0

        sentinel.write_text("1", encoding="utf-8")
        flagged = check_deferred_work(run_record_paths)
        reason = INSTRUCTION
        if flagged:
            reason += build_deferred_work_block(flagged)
        hits = check_dedup_gate(touched_durable_paths, run_record_paths, bash_commands)
        if hits:
            reason += build_dedup_gate_block(hits)
        print(json.dumps({"decision": "block", "reason": reason}))
    except Exception:  # noqa: BLE001
        # Hooks must never break a session.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
