import importlib.util
import io
import json
import os
from pathlib import Path


HOOK_PATH = Path(__file__).with_name("suggest_next_step.py")
SPEC = importlib.util.spec_from_file_location("suggest_next_step", HOOK_PATH)
assert SPEC and SPEC.loader
hook = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hook)


def _write_transcript(path: Path, tool_name: str, tool_input=None) -> None:
    entry = {
        "message": {
            "content": [{"type": "tool_use", "name": tool_name, "input": tool_input or {}}]
        }
    }
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")


def test_substantive_work_detects_edits_and_commits(tmp_path):
    edit_transcript = tmp_path / "edit.jsonl"
    _write_transcript(edit_transcript, "Edit")
    assert hook.substantive_work(str(edit_transcript))

    bash_transcript = tmp_path / "bash.jsonl"
    _write_transcript(bash_transcript, "Bash", {"command": "git push origin HEAD"})
    assert hook.substantive_work(str(bash_transcript))


def test_substantive_work_ignores_read_only_tools(tmp_path):
    transcript = tmp_path / "read.jsonl"
    _write_transcript(transcript, "Read")
    assert not hook.substantive_work(str(transcript))


def test_instruction_is_worktrail_native_and_value_gated():
    assert "worktrail-handoff" in hook.INSTRUCTION
    assert "complete the current work" in hook.INSTRUCTION
    assert "Never capture required validation as a handoff" in hook.INSTRUCTION
    assert "verified blocker" in hook.INSTRUCTION
    assert "Creating a handoff is optional, not the default" in hook.INSTRUCTION
    assert "Do NOT capture routine polish" in hook.INSTRUCTION
    assert "No handoff captured; no exceptional next step identified." in hook.INSTRUCTION
    assert "developer-kit" not in hook.INSTRUCTION


def test_main_blocks_once_per_session(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CC_HEADLESS", raising=False)
    transcript = tmp_path / "transcript.jsonl"
    _write_transcript(transcript, "Write")
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path / "state")
    payload = {"session_id": "session-1", "transcript_path": str(transcript)}

    monkeypatch.setattr(hook.sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hook.main() == 0
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["decision"] == "block"

    monkeypatch.setattr(hook.sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hook.main() == 0
    assert capsys.readouterr().out == ""


def _write_run_record(path: Path, deferred_work: list[str] | None = None) -> None:
    """A minimal, real run-record YAML in run_record.py's own on-disk format
    (`_load` in run_record.py: `key: value` lines plus `  - "item"` list
    entries) -- just the one field `check_deferred_work_handoff.py` reads.
    Written directly rather than via `worktrail-run-record start` since no
    other field is read anywhere on this path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["deferred_work:\n"]
    for text in deferred_work or []:
        lines.append(f"  - {json.dumps(text)}\n")
    path.write_text("".join(lines), encoding="utf-8")


def _install_check_deferred_work_handoff_shim(tmp_path: Path, monkeypatch) -> None:
    """A real `worktrail-check-deferred-work-handoff` on `PATH`, backed by
    this worktree's own `src/` (never the machine's separately
    `pip install -e`'d `worktrail`), so `check_deferred_work` exercises the
    actual subprocess boundary -- `shutil.which`, argv construction, the
    JSON round-trip, `flagged` extraction -- instead of a stub of the hook's
    own function. Also isolates `WORK_QUEUE_DIR` so the shim's
    `has_handoff_coverage` lookup never touches the operator's real queue.
    """
    repo_src = HOOK_PATH.parent.parent / "src"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    shim = bin_dir / "worktrail-check-deferred-work-handoff"
    shim.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.path.insert(0, {str(repo_src)!r})\n"
        "from worktrail.router.check_deferred_work_handoff import main\n"
        "sys.exit(main())\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("WORK_QUEUE_DIR", str(tmp_path / "work-queue"))


def test_main_output_unchanged_when_run_record_flags_nothing(tmp_path, monkeypatch, capsys):
    """Run-record path literals in the transcript, one with an empty
    `deferred_work` and one with a fully phrase-non-matching `deferred_work`,
    must not change the emitted `reason` at all (Requirement: Additive And
    Non-Interfering / Silent When Nothing Unmatched). `check_deferred_work`
    runs for real against the shim installed by
    `_install_check_deferred_work_handoff_shim`, not a stub; deferral-phrase
    matching and handoff-coverage lookup themselves are covered by
    test_check_deferred_work_handoff.py (tasks 3.1-3.3, sibling branch).
    """
    monkeypatch.delenv("CC_HEADLESS", raising=False)
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path / "state")
    _install_check_deferred_work_handoff_shim(tmp_path, monkeypatch)
    assert hook.shutil.which(hook.DEFERRED_WORK_HANDOFF_BINARY) is not None

    baseline_transcript = tmp_path / "baseline.jsonl"
    _write_transcript(baseline_transcript, "Write")
    monkeypatch.setattr(
        hook.sys,
        "stdin",
        io.StringIO(json.dumps({"session_id": "baseline", "transcript_path": str(baseline_transcript)})),
    )
    assert hook.main() == 0
    baseline_output = capsys.readouterr().out
    assert baseline_output == json.dumps({"decision": "block", "reason": hook.INSTRUCTION}) + "\n"

    empty_record = tmp_path / ".worktrail" / "runs" / "some-repo" / "run-empty.yaml"
    _write_run_record(empty_record, deferred_work=[])
    nonmatching_record = tmp_path / ".worktrail" / "runs" / "some-repo" / "run-nonmatching.yaml"
    _write_run_record(nonmatching_record, deferred_work=["ship the new onboarding flow next quarter"])

    assert hook.check_deferred_work([str(empty_record), str(nonmatching_record)]) == []

    with_path_transcript = tmp_path / "with_path.jsonl"
    entry = {
        "message": {
            "content": [
                {"type": "tool_use", "name": "Write", "input": {}},
                {
                    "type": "text",
                    "text": f"See run records {empty_record} and {nonmatching_record} for details.",
                },
            ]
        }
    }
    with_path_transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    monkeypatch.setattr(
        hook.sys,
        "stdin",
        io.StringIO(json.dumps({"session_id": "with-path", "transcript_path": str(with_path_transcript)})),
    )
    assert hook.main() == 0
    with_path_output = capsys.readouterr().out

    assert with_path_output == baseline_output


def test_main_appends_deferred_work_block_for_unmatched_flagged_entry(tmp_path, monkeypatch, capsys):
    """A run-record path literal whose `deferred_work` has an unmatched,
    phrase-matching entry must produce output containing both the unmodified
    EXCEPTIONAL-VALUE instruction and the new deferral-flag block
    (Requirement: Additive And Non-Interfering). `check_deferred_work` runs
    for real against the shim installed by
    `_install_check_deferred_work_handoff_shim`, not a stub; deferral-phrase
    matching and handoff-coverage lookup themselves are covered by
    test_check_deferred_work_handoff.py (tasks 3.1-3.3, sibling branch).
    """
    monkeypatch.delenv("CC_HEADLESS", raising=False)
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path / "state")
    _install_check_deferred_work_handoff_shim(tmp_path, monkeypatch)
    assert hook.shutil.which(hook.DEFERRED_WORK_HANDOFF_BINARY) is not None

    flagged_record = tmp_path / ".worktrail" / "runs" / "some-repo" / "run-flagged.yaml"
    _write_run_record(
        flagged_record,
        deferred_work=["revisit the retry backoff as a follow-up once calibrated"],
    )
    assert hook.check_deferred_work([str(flagged_record)]) == [
        {
            "text": "revisit the retry backoff as a follow-up once calibrated",
            "run_record": str(flagged_record),
        }
    ]

    with_path_transcript = tmp_path / "with_flagged_path.jsonl"
    entry = {
        "message": {
            "content": [
                {"type": "tool_use", "name": "Write", "input": {}},
                {
                    "type": "text",
                    "text": f"See run record {flagged_record} for details.",
                },
            ]
        }
    }
    with_path_transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    monkeypatch.setattr(
        hook.sys,
        "stdin",
        io.StringIO(
            json.dumps({"session_id": "with-flagged-path", "transcript_path": str(with_path_transcript)})
        ),
    )
    assert hook.main() == 0
    reason = json.loads(capsys.readouterr().out)["reason"]

    assert hook.INSTRUCTION in reason
    assert reason.startswith(hook.INSTRUCTION)
    assert "DEFERRED WORK FLAGGED" in reason
    assert "revisit the retry backoff as a follow-up once calibrated" in reason
    assert str(flagged_record) in reason


def test_main_fail_open_no_path_missing_binary_and_headless(tmp_path, monkeypatch, capsys):
    """Three independent fail-open paths (Requirement: Run-Record Discovery Via
    Transcript Grep / Requirement: Fail-Open And Headless-Excluded), each
    compared against the same baseline reason: a transcript with no
    run-record path literal at all; a run-record path literal that would
    otherwise flag but with `worktrail-check-deferred-work-handoff` missing
    from `PATH`; and `CC_HEADLESS=1`, which must skip the hook entirely, same
    as before this feature existed. Distinct `session_id`s per case so the
    once-per-session sentinel never masks the comparison.
    """
    monkeypatch.delenv("CC_HEADLESS", raising=False)
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path / "state")

    baseline_transcript = tmp_path / "baseline.jsonl"
    _write_transcript(baseline_transcript, "Write")
    monkeypatch.setattr(
        hook.sys,
        "stdin",
        io.StringIO(json.dumps({"session_id": "baseline", "transcript_path": str(baseline_transcript)})),
    )
    assert hook.main() == 0
    baseline_output = capsys.readouterr().out
    assert baseline_output == json.dumps({"decision": "block", "reason": hook.INSTRUCTION}) + "\n"

    # Case 1: no run-record path literal anywhere in the transcript.
    monkeypatch.setattr(
        hook.sys,
        "stdin",
        io.StringIO(json.dumps({"session_id": "no-path", "transcript_path": str(baseline_transcript)})),
    )
    assert hook.main() == 0
    assert capsys.readouterr().out == baseline_output

    # Case 2: a phrase-matching run-record path literal is present, but the
    # handoff-check binary is missing from PATH -- check_deferred_work must
    # fail open to [] rather than raise or block on a lookup failure.
    monkeypatch.setenv("PATH", "")
    assert hook.shutil.which(hook.DEFERRED_WORK_HANDOFF_BINARY) is None
    flagged_record = tmp_path / ".worktrail" / "runs" / "some-repo" / "run-flagged.yaml"
    _write_run_record(
        flagged_record,
        deferred_work=["revisit the retry backoff as a follow-up once calibrated"],
    )
    with_path_transcript = tmp_path / "with_path.jsonl"
    entry = {
        "message": {
            "content": [
                {"type": "tool_use", "name": "Write", "input": {}},
                {"type": "text", "text": f"See run record {flagged_record} for details."},
            ]
        }
    }
    with_path_transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        hook.sys,
        "stdin",
        io.StringIO(json.dumps({"session_id": "missing-binary", "transcript_path": str(with_path_transcript)})),
    )
    assert hook.main() == 0
    assert capsys.readouterr().out == baseline_output

    # Case 3: CC_HEADLESS=1 skips the hook before it even reads stdin.
    monkeypatch.setenv("CC_HEADLESS", "1")
    monkeypatch.setattr(
        hook.sys,
        "stdin",
        io.StringIO(json.dumps({"session_id": "headless", "transcript_path": str(with_path_transcript)})),
    )
    assert hook.main() == 0
    assert capsys.readouterr().out == ""


def test_main_skips_continuation_and_headless_worker(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO(json.dumps({"stop_hook_active": True})))
    assert hook.main() == 0
    assert capsys.readouterr().out == ""

    monkeypatch.setenv("CC_HEADLESS", "1")
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO("not json"))
    assert hook.main() == 0
    assert not (tmp_path / "state").exists()
