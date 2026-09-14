import copy
import json
import subprocess

from worktrail.learning.digest import build_outcome_digest
from worktrail.learning.paths import RETRO_AGENT_NAME, learning_dir, retro_memory_path


def _clean_journal():
    return {
        "spec_id": "spec-x",
        "run_id": "run-1",
        "entries": [
            {
                "task": "1.1",
                "role": "implement",
                "agent": "claude",
                "report": {
                    "context_quality": "sufficient",
                    "critical_issues": 0,
                    "notes": "ok",
                    "usage": {"input_tokens": 5},
                    "tools_used": ["Read"],
                    "head_sha": "abc",
                },
            }
        ],
        "groups": {"g1": {"state": "MERGED"}},
        "unreconciled_tail_evidence": [],
    }


def _entry(role="implement", **kw):
    report = kw.pop("report", {})
    return {"task": "1.1", "role": role, "agent": "claude", "report": report, **kw}


def test_clean_journal_has_no_signal():
    d = build_outcome_digest(_clean_journal())
    assert d["has_signal"] is False
    assert d["worker_signals"] == [] and d["events"] == {}


def test_quarantined_group_reported():
    j = _clean_journal()
    j["groups"]["g2"] = {"state": "QUARANTINED", "quarantine_reason": "ci_red"}
    d = build_outcome_digest(j)
    assert d["quarantined_groups"] == [{"group": "g2", "reason": "ci_red"}]
    assert d["has_signal"]


def test_each_signal_kind_extracted():
    j = _clean_journal()
    j["entries"] = [
        _entry(report={"context_quality": "insufficient"}),
        _entry(report={"critical_issues": 2}),
        _entry("review", report={"major_issues": 1}),
        _entry("implement", report={"major_issues": 1}),
        _entry(scope_escalated=True),
        _entry(blocked_by=["1.0"]),
        _entry("fix"),
    ]
    sigs = [w["signals"] for w in build_outcome_digest(j)["worker_signals"]]
    assert sigs == [
        ["insufficient_context"],
        ["critical_issues"],
        ["major_issues"],
        ["scope_escalated"],
        ["blocked"],
        ["fix_round"],
    ]


def test_cap_and_truncated_flag():
    j = _clean_journal()
    j["entries"] = [_entry("fix") for _ in range(51)]
    d = build_outcome_digest(j)
    assert len(d["worker_signals"]) == 50 and d["truncated"] is True
    j["entries"] = [_entry("fix") for _ in range(50)]
    assert build_outcome_digest(j)["truncated"] is False


def test_text_truncation_and_excluded_fields():
    j = _clean_journal()
    j["entries"] = [
        _entry(
            "fix",
            report={
                "notes": "n" * 900,
                "missing_context": ["m" * 900],
                "usage": {"x": 1},
                "tools_used": ["Bash"],
                "head_sha": "deadbeef",
            },
        )
    ]
    item = build_outcome_digest(j)["worker_signals"][0]
    assert len(item["notes"]) == 500 and len(item["missing_context"][0]) == 500
    text = json.dumps(build_outcome_digest(j))
    for key in ("usage", "tools_used", "head_sha", "deadbeef"):
        assert key not in text


def test_events_exclude_retro_and_learned_notes_and_tail():
    j = _clean_journal()
    j["entries"] += [{"event": "dependency_file_drift"}, {"event": "retro"}]
    j["safety_net_events"] = [
        {"event": "learned_notes"},
        {"event": "dependency_file_drift"},
    ]
    d = build_outcome_digest(j)
    assert d["events"] == {"dependency_file_drift": 2}
    j2 = _clean_journal()
    j2["entries"].append({"event": "retro"})
    assert build_outcome_digest(j2)["has_signal"] is False
    j2["unreconciled_tail_evidence"] = [{"sha": "x"}]
    d2 = build_outcome_digest(j2)
    assert d2["unreconciled_tail"] is True and d2["has_signal"] is True


def test_deterministic_and_pure():
    j = _clean_journal()
    j["entries"].append(_entry("fix"))
    j["groups"]["q"] = {"state": "QUARANTINED", "quarantine_reason": "r"}
    before = copy.deepcopy(j)
    assert build_outcome_digest(j) == build_outcome_digest(j)
    assert j == before


def test_memory_path_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKTRAIL_HOME", str(tmp_path / "home"))
    repo = tmp_path / "myrepo"
    repo.mkdir()
    assert RETRO_AGENT_NAME == "worktrail-retro"
    assert learning_dir(repo) == tmp_path / "home" / "learning" / "myrepo"
    assert retro_memory_path(repo) == (
        learning_dir(repo)
        / ".claude"
        / "agent-memory"
        / "worktrail-retro"
        / "MEMORY.md"
    )


def test_worktree_and_canonical_share_learning_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKTRAIL_HOME", str(tmp_path / "home"))
    canon = tmp_path / "canon"
    canon.mkdir()

    def git(*args, cwd=canon):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    git("init", "-q")
    git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "i",
    )
    wt = tmp_path / "wt-other-name"
    git("worktree", "add", "-q", str(wt), "-b", "b")
    assert learning_dir(wt) == learning_dir(canon)
    assert learning_dir(canon).name == "canon"
