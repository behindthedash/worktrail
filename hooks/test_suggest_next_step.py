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


def _write_entries(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def _tool_entry(tool_name: str, tool_input=None) -> dict:
    return {
        "message": {
            "content": [{"type": "tool_use", "name": tool_name, "input": tool_input or {}}]
        }
    }


def test_scan_transcript_collects_touched_durable_paths_from_edit_tools(tmp_path):
    """Edit-tool `file_path`s under `docs/specs/**` or `openspec/changes/**`
    are collected as touched durable artifacts; non-durable edit targets and
    read-only mentions (Read tool) are not."""
    transcript = tmp_path / "edits.jsonl"
    spec_path = "/repo/docs/specs/001-task/design.md"
    _write_entries(
        transcript,
        [
            _tool_entry("Write", {"file_path": spec_path}),
            _tool_entry("Edit", {"file_path": "/repo/openspec/changes/my-change/tasks.md"}),
            _tool_entry("MultiEdit", {"file_path": "/repo/src/main.py"}),
            _tool_entry("Read", {"file_path": spec_path}),
            _tool_entry("NotebookEdit", {"notebook_path": "/repo/openspec/changes/my-change/nb.ipynb"}),
        ],
    )
    has_work, run_records, durable_paths = hook.scan_transcript(str(transcript))
    assert has_work
    assert run_records == []
    assert durable_paths == [
        spec_path,
        "/repo/openspec/changes/my-change/tasks.md",
        "/repo/openspec/changes/my-change/nb.ipynb",
    ]


def test_scan_transcript_collects_touched_durable_paths_from_bash_write_markers(tmp_path):
    """Bash commands carrying a write marker AND naming a durable-artifact
    path contribute that path; the same mention in a command without a write
    marker (plain `cat | grep`) does not."""
    transcript = tmp_path / "bash.jsonl"
    _write_entries(
        transcript,
        [
            _tool_entry(
                "Bash",
                {"command": "mkdir -p openspec/changes/new-idea && cat > openspec/changes/new-idea/proposal.md <<'EOF'\nbody\nEOF"},
            ),
            _tool_entry("Bash", {"command": "sed -i 's/old/new/' docs/specs/001-task/design.md"}),
            _tool_entry("Bash", {"command": "cat docs/specs/001-task/design.md | grep todo"}),
            _tool_entry("Bash", {"command": "pytest -q > /tmp/out.txt"}),
        ],
    )
    _, _, durable_paths = hook.scan_transcript(str(transcript))
    assert durable_paths == [
        "openspec/changes/new-idea",
        "openspec/changes/new-idea/proposal.md",
        "docs/specs/001-task/design.md",
    ]


def test_scan_transcript_collects_all_signals_in_one_pass_and_dedupes(tmp_path):
    """Run-record literals and touched durable paths come out of the same
    single line-by-line read -- including entries that appear AFTER the first
    work signal -- and repeated identical paths are reported once."""
    record = tmp_path / ".worktrail" / "runs" / "some-repo" / "run-x.yaml"
    transcript = tmp_path / "mixed.jsonl"
    _write_entries(
        transcript,
        [
            _tool_entry("Bash", {"command": "git push origin HEAD"}),
            {
                "message": {
                    "content": [{"type": "text", "text": f"See run record {record} for details."}]
                }
            },
            _tool_entry("Edit", {"file_path": "/repo/docs/specs/late-entry.md"}),
            _tool_entry("Edit", {"file_path": "/repo/docs/specs/late-entry.md"}),
        ],
    )
    has_work, run_records, durable_paths = hook.scan_transcript(str(transcript))
    assert has_work
    assert run_records == [str(record)]
    assert durable_paths == ["/repo/docs/specs/late-entry.md"]

    missing = hook.scan_transcript(str(tmp_path / "nope.jsonl"))
    assert missing == (False, [], [])


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


def _write_run_record(
    path: Path,
    deferred_work: list[str] | None = None,
    final_status: str | None = None,
) -> None:
    """A minimal, real run-record YAML in run_record.py's own on-disk format
    (`_load` in run_record.py: `key: value` lines plus `  - "item"` list
    entries) -- just the fields `check_deferred_work_handoff.py` and
    `check_durable_artifact_capture_gate.py` read. Written directly rather
    than via `worktrail-run-record start` since no other field is read
    anywhere on this path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["deferred_work:\n"]
    for text in deferred_work or []:
        lines.append(f"  - {json.dumps(text)}\n")
    if final_status is not None:
        lines.append(f"final_status: {final_status}\n")
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


def _install_check_durable_artifact_capture_gate_shim(tmp_path: Path, monkeypatch) -> None:
    """A real `worktrail-check-durable-artifact-capture-gate` on `PATH`, backed
    by this worktree's own `src/` (never the machine's separately
    `pip install -e`'d `worktrail`) -- the same subprocess-boundary pattern as
    `_install_check_deferred_work_handoff_shim`, so `check_dedup_gate`
    exercises the actual binary lookup, argv construction, JSON round-trip,
    and `hits` extraction instead of a stub of the hook's own function.
    """
    repo_src = HOOK_PATH.parent.parent / "src"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    shim = bin_dir / "worktrail-check-durable-artifact-capture-gate"
    shim.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.path.insert(0, {str(repo_src)!r})\n"
        "from worktrail.router.check_durable_artifact_capture_gate import main\n"
        "sys.exit(main())\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")


def test_main_no_hit_output_byte_identical_to_pre_gate_instruction(tmp_path, monkeypatch, capsys):
    """A transcript that feeds the dedup gate a real input yielding zero hits
    (a run-record literal whose record finished a non-planned status) must emit
    byte-for-byte the pre-gate instruction -- identical stdout to a session
    with nothing for the gate to look at (Requirement: Additive And
    Non-Interfering / Silent When Nothing Unmatched). Both checkers run for
    real against their shims; hit classification itself is covered by
    test_check_durable_artifact_capture_gate.py (task 2.3).
    """
    monkeypatch.delenv("CC_HEADLESS", raising=False)
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path / "state")
    _install_check_deferred_work_handoff_shim(tmp_path, monkeypatch)
    _install_check_durable_artifact_capture_gate_shim(tmp_path, monkeypatch)
    assert hook.shutil.which(hook.DEDUP_GATE_BINARY) is not None

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

    implemented_record = tmp_path / ".worktrail" / "runs" / "some-repo" / "run-implemented.yaml"
    _write_run_record(implemented_record, deferred_work=[], final_status="implemented")

    with_path_transcript = tmp_path / "with_path.jsonl"
    entry = {
        "message": {
            "content": [
                {"type": "tool_use", "name": "Write", "input": {}},
                {
                    "type": "text",
                    "text": f"See run record {implemented_record} for details.",
                },
            ]
        }
    }
    with_path_transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    monkeypatch.setattr(
        hook.sys,
        "stdin",
        io.StringIO(json.dumps({"session_id": "no-hit", "transcript_path": str(with_path_transcript)})),
    )
    assert hook.main() == 0
    assert capsys.readouterr().out == baseline_output


def test_main_hit_appends_dedup_gate_block_naming_artifact(tmp_path, monkeypatch, capsys):
    """An Edit touching a `docs/specs/**` path must produce output that starts
    with the unmodified pre-gate instruction and appends exactly the DEDUP GATE
    block naming the touched artifact -- forbidding auto-capture, requiring a
    suggestion-only line with the resume command, and stating the explicit-
    justification escape hatch (Requirement: Downgrade-To-Suggestion On Dedup
    Hit). The gate runs for real against the shim installed by
    `_install_check_durable_artifact_capture_gate_shim`, not a stub.
    """
    monkeypatch.delenv("CC_HEADLESS", raising=False)
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path / "state")
    _install_check_durable_artifact_capture_gate_shim(tmp_path, monkeypatch)

    spec_path = "/repo/docs/specs/001-task/design.md"
    transcript = tmp_path / "dedup_hit.jsonl"
    _write_entries(
        transcript,
        [
            _tool_entry("Write", {"file_path": "/repo/src/main.py"}),
            _tool_entry("Edit", {"file_path": spec_path}),
        ],
    )

    expected_hits = hook.check_dedup_gate([spec_path], [])
    assert expected_hits == [{"kind": "session_touched_durable_artifact", "path": spec_path}]

    monkeypatch.setattr(
        hook.sys,
        "stdin",
        io.StringIO(json.dumps({"session_id": "dedup-hit", "transcript_path": str(transcript)})),
    )
    assert hook.main() == 0
    reason = json.loads(capsys.readouterr().out)["reason"]

    assert reason.startswith(hook.INSTRUCTION)
    assert reason == hook.INSTRUCTION + hook.build_dedup_gate_block(expected_hits)
    assert "DEDUP GATE" in reason
    assert f"- durable artifact touched this session: {spec_path}" in reason
    assert "Do NOT auto-capture" in reason
    assert "suggestion-only line naming the resume command" in reason
    assert "`worktrail-go <brief-id>`" in reason
    assert "## Dedup justification" in reason


def test_build_dedup_gate_block_renders_every_hit_kind():
    """Each checker hit kind renders its own artifact-naming line inside the
    gate block, including an unrecognized shape degrading to a visible dump
    rather than vanishing."""
    block = hook.build_dedup_gate_block(
        [
            {"kind": "session_touched_durable_artifact", "path": "/repo/docs/specs/x/spec.md"},
            {
                "kind": "planned_run_record",
                "run_record": "/repo/run.yaml",
                "final_status": "planned_ready_for_implementation",
            },
            {
                "kind": "merged_docs_only_spec_pr",
                "spec_paths": ["/repo/docs/specs/y.md"],
                "merge_markers": ["gh pr merge"],
            },
            {"unexpected": "shape"},
        ]
    )
    assert "- durable artifact touched this session: /repo/docs/specs/x/spec.md" in block
    assert "- run record finished planned_ready_for_implementation: /repo/run.yaml" in block
    assert (
        "- merged docs-only spec PR (merge marker(s): gh pr merge): /repo/docs/specs/y.md" in block
    )
    assert "- unrecognized dedup hit:" in block


def test_main_fail_open_when_dedup_gate_binary_missing(tmp_path, monkeypatch, capsys):
    """A transcript that would otherwise hit the gate, but with
    `worktrail-check-durable-artifact-capture-gate` missing from `PATH`, must
    fail open: byte-for-byte the same output as the pre-gate instruction,
    never an error or a downgraded block (Requirement: Fail-Open And
    Headless-Excluded). Distinct `session_id`s so the once-per-session
    sentinel never masks the comparison.
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

    monkeypatch.setenv("PATH", "")
    assert hook.shutil.which(hook.DEDUP_GATE_BINARY) is None

    would_hit_transcript = tmp_path / "would_hit.jsonl"
    _write_entries(
        would_hit_transcript,
        [_tool_entry("Edit", {"file_path": "/repo/openspec/changes/my-change/tasks.md"})],
    )
    monkeypatch.setattr(
        hook.sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {"session_id": "missing-dedup-binary", "transcript_path": str(would_hit_transcript)}
            )
        ),
    )
    assert hook.main() == 0
    assert capsys.readouterr().out == baseline_output


def test_main_headless_skips_even_when_dedup_gate_would_hit(tmp_path, monkeypatch, capsys):
    """`CC_HEADLESS=1` skips the hook entirely, even when the transcript would
    otherwise produce a dedup-gate hit -- headless workers stay unaffected,
    same as before the gate existed (Requirement: Fail-Open And
    Headless-Excluded)."""
    monkeypatch.setenv("CC_HEADLESS", "1")
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path / "state")

    transcript = tmp_path / "would_hit.jsonl"
    _write_entries(transcript, [_tool_entry("Edit", {"file_path": "/repo/docs/specs/x/spec.md"})])
    monkeypatch.setattr(
        hook.sys,
        "stdin",
        io.StringIO(json.dumps({"session_id": "headless-hit", "transcript_path": str(transcript)})),
    )
    assert hook.main() == 0
    assert capsys.readouterr().out == ""
