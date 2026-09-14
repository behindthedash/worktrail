import fcntl
import json
import subprocess

import pytest

from worktrail.learning import retro
from worktrail.learning.paths import RETRO_AGENT_NAME, learning_dir, retro_memory_path
from worktrail.orchestrator.spawnlib import SpawnExhausted
from worktrail.runtime.selection import Cell

GOOD_MEMORY = (
    "# worktrail-retro memory: repo\n\n## Notes for workers\n"
    "- Read the design first (evidence: spec-x g1 2026-09-14)\n\n## Observations\n"
)


def _cell(harness="claude"):
    return Cell(target=harness, harness=harness, model="m", effort=None, pool="sub")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKTRAIL_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    (repo / ".worktrail").mkdir(parents=True)
    (repo / ".worktrail" / "policy.yaml").write_text("agent_learning: true\n")
    journal = tmp_path / "run-x.json"
    journal.write_text(
        json.dumps(
            {
                "spec_id": "spec-x",
                "run_id": "r1",
                "entries": [],
                "groups": {"g1": {"state": "QUARANTINED", "quarantine_reason": "boom"}},
            }
        )
    )
    return repo, journal


class FakeSpawn:
    def __init__(self, memory=GOOD_MEMORY, raises=None):
        self.calls = []
        self.memory = memory
        self.raises = raises

    def __call__(self, prompt, cwd, **kw):
        self.calls.append({"prompt": prompt, "cwd": cwd, **kw})
        if self.raises:
            raise self.raises
        if self.memory is not None:
            path = retro_memory_path(self.repo)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.memory)


def _run(repo, journal, spawn=None, harness="claude"):
    spawn = spawn or FakeSpawn()
    spawn.repo = repo
    result = retro.run_retro(
        repo,
        journal,
        spawn=spawn,
        select=lambda *a, **k: _cell(harness),
        log=lambda *_: None,
    )
    return result, spawn


def _markers(journal):
    data = json.loads(journal.read_text())
    return [e for e in data.get("safety_net_events", []) if e.get("event") == "retro"]


def test_disabled_skips_without_spawn(env):
    repo, journal = env
    (repo / ".worktrail" / "policy.yaml").write_text("")
    result, spawn = _run(repo, journal)
    assert result == {"status": "skipped", "reason": "disabled"}
    assert spawn.calls == []


def test_no_signal_skips_without_spawn(env):
    repo, journal = env
    journal.write_text(
        json.dumps({"spec_id": "s", "groups": {"g": {"state": "MERGED"}}})
    )
    result, spawn = _run(repo, journal)
    assert result["reason"] == "no_signal"
    assert spawn.calls == []


def test_codex_cell_skips(env):
    repo, journal = env
    result, spawn = _run(repo, journal, harness="codex")
    assert result == {"status": "skipped", "reason": "claude_harness_unavailable"}
    assert spawn.calls == []


def test_spawn_arguments(env):
    repo, journal = env
    result, spawn = _run(repo, journal)
    assert result["status"] == "completed"
    assert "contract_violations" not in result
    assert len(result["memory_sha256"]) == 64
    (call,) = spawn.calls
    assert call["cwd"] == learning_dir(repo)
    args = call["extra_args"]
    assert args[0] == "--agents" and args[2:] == ["--agent", RETRO_AGENT_NAME]
    agent = json.loads(args[1])[RETRO_AGENT_NAME]
    assert agent["memory"] == "project"
    assert agent["tools"] == ["Read", "Write", "Edit"]
    assert "## Notes for workers" in agent["prompt"]
    assert '"reason": "boom"' in call["prompt"]
    assert call["timeout"] == 900


def test_locked_skips(env):
    repo, journal = env
    ldir = learning_dir(repo)
    ldir.mkdir(parents=True)
    holder = subprocess.Popen(
        [
            "python3",
            "-c",
            (
                "import fcntl,sys,time;f=open(sys.argv[1],'a');"
                "fcntl.flock(f,fcntl.LOCK_EX);print('held',flush=True);time.sleep(30)"
            ),
            str(ldir / ".retro.lock"),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        result, spawn = _run(repo, journal)
    finally:
        holder.kill()
        holder.wait()
    assert result == {"status": "skipped", "reason": "locked"}
    assert spawn.calls == []


@pytest.mark.parametrize(
    "exc, reason",
    [
        (RuntimeError("x"), "RuntimeError"),
        (SpawnExhausted("x"), "SpawnExhausted"),
        (subprocess.TimeoutExpired("claude", 900), "timeout"),
    ],
)
def test_spawn_failures_map_to_failed(env, exc, reason):
    repo, journal = env
    result, _ = _run(repo, journal, spawn=FakeSpawn(raises=exc))
    assert result == {"status": "failed", "reason": reason}
    assert _markers(journal) == [{"event": "retro", **result}]
    # lock released after failure
    with open(learning_dir(repo) / ".retro.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize(
    "memory, violation",
    [
        (
            "## Notes for workers\n"
            + "".join(f"- n{i} (evidence: s g 2026-09-14)\n" for i in range(21)),
            "too_many_notes",
        ),
        ("## Notes for workers\n- no citation\n", "note_missing_evidence"),
        ("# memory\n## Observations\n", "missing_notes_section"),
        (GOOD_MEMORY + "x" * 26 * 1024, "oversize"),
        (None, "missing_file"),
    ],
)
def test_contract_violations_recorded_not_repaired(env, memory, violation):
    repo, journal = env
    result, _ = _run(repo, journal, spawn=FakeSpawn(memory=memory))
    assert result["status"] == "completed"
    assert violation in result["contract_violations"]
    if memory is not None:
        assert retro_memory_path(repo).read_text() == memory
    assert _markers(journal)[0]["contract_violations"] == result["contract_violations"]


def test_one_marker_per_call(env):
    repo, journal = env
    _run(repo, journal)
    _run(repo, journal)
    assert len(_markers(journal)) == 2


def test_dry_run_json(env, capsys, monkeypatch):
    repo, journal = env
    monkeypatch.setattr(retro, "select_cell", lambda *a, **k: _cell())
    monkeypatch.setattr(retro, "_curate", lambda *a, **k: pytest.fail("spawned"))
    code = retro.main(
        ["--repo", str(repo), "--journal", str(journal), "--dry-run", "--json"]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == {"status": "ready", "reason": "spawn"}
    assert out["digest"]["spec_id"] == "spec-x"
    assert not learning_dir(repo).exists()
    assert _markers(journal) == []


def test_exit_codes(env, monkeypatch, tmp_path):
    repo, journal = env
    monkeypatch.setattr(
        retro, "run_retro", lambda *a, **k: {"status": "skipped", "reason": "x"}
    )
    assert retro.main(["--repo", str(repo), "--journal", str(journal)]) == 0
    monkeypatch.setattr(
        retro, "run_retro", lambda *a, **k: {"status": "failed", "reason": "x"}
    )
    assert retro.main(["--repo", str(repo), "--journal", str(journal)]) == 1
    assert retro.main(["--repo", str(repo)]) == 2
    assert (
        retro.main(["--repo", str(repo), "--journal", str(tmp_path / "nope.json")]) == 2
    )
