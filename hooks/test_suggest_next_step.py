import importlib.util
import io
import json
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


def test_main_output_unchanged_when_run_record_flags_nothing(tmp_path, monkeypatch, capsys):
    """A run-record path in the transcript whose `deferred_work` is empty or fully
    phrase-non-matching (simulated here by `check_deferred_work` returning `[]`, per
    Requirement: Deferred-Work-Only Signal Source / Deferral-Phrase Matching, which
    `check_deferred_work` delegates to and which are covered separately by
    test_check_deferred_work_handoff.py) must not change the emitted `reason` at all.
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

    run_record_path = "~/.worktrail/runs/some-repo/run-1.yaml"
    with_path_transcript = tmp_path / "with_path.jsonl"
    entry = {
        "message": {
            "content": [
                {"type": "tool_use", "name": "Write", "input": {}},
                {"type": "text", "text": f"See run record {run_record_path} for details."},
            ]
        }
    }
    with_path_transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_check_deferred_work(paths):
        calls.append(paths)
        return []

    monkeypatch.setattr(hook, "check_deferred_work", fake_check_deferred_work)
    monkeypatch.setattr(
        hook.sys,
        "stdin",
        io.StringIO(json.dumps({"session_id": "with-path", "transcript_path": str(with_path_transcript)})),
    )
    assert hook.main() == 0
    with_path_output = capsys.readouterr().out

    assert with_path_output == baseline_output
    assert calls == [[run_record_path]]


def test_main_skips_continuation_and_headless_worker(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO(json.dumps({"stop_hook_active": True})))
    assert hook.main() == 0
    assert capsys.readouterr().out == ""

    monkeypatch.setenv("CC_HEADLESS", "1")
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO("not json"))
    assert hook.main() == 0
    assert not (tmp_path / "state").exists()
