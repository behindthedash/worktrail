"""Tests for the conductor's compile phase.

The headline assertion is design P3's own acceptance criterion: *the same change
compiled twice produces zero LLM calls on the second run*. The rest cover the
two ways compile avoids the model entirely, and the ways a bad model response
must degrade rather than propagate.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from worktrail.conductor import compile as conductor_compile
from worktrail.conductor import runplan

TASKS_MD = textwrap.dedent(
    """\
    ## 1. Core

    - [ ] 1.1 Add the parser
    - [ ] 1.2 Wire the endpoint

    ## 2. Verification

    - [ ] 2.1 [e2e] End-to-end check
    """
)


@pytest.fixture()
def change(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    d = repo / "openspec" / "changes" / "add-parser"
    d.mkdir(parents=True)
    (repo / ".git").mkdir()
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text(TASKS_MD)
    return d


def _load(change: Path):
    from worktrail.taskformats import resolve

    return resolve.load_spec(str(change))


class RecordingSpawn:
    """Stands in for `spawn_agent`, counting calls and replaying a fixed answer."""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls = 0
        self.prompts: list[str] = []

    def __call__(self, prompt, cwd, timeout, log):
        self.calls += 1
        self.prompts.append(prompt)
        return self.reply


def _reply(**per_task) -> str:
    rows = [{"id": k, **v} for k, v in per_task.items()]
    return "Here is the plan.\n\n```json\n" + json.dumps({"tasks": rows}) + "\n```\n"


# --------------------------------------------------------------------------- #
# The P3 acceptance criterion
# --------------------------------------------------------------------------- #
def test_compiling_the_same_change_twice_spawns_nothing_the_second_time(change, tmp_path):
    spec_id, tasks = _load(change)
    spawn = RecordingSpawn(
        _reply(
            **{
                "1.1": {"files": ["src/parser.py"], "deps": []},
                "1.2": {"files": ["src/api.py"], "deps": ["1.1"]},
                "2.1": {"files": ["tests/e2e.py"], "deps": ["1.2"]},
            }
        )
    )
    kwargs = dict(
        spec_id=spec_id,
        repo=change.parents[2],
        cache_dir=tmp_path / "plans",
        spawn=spawn,
    )

    first = conductor_compile.compile_run_plan(change, tasks, **kwargs)
    assert spawn.calls == 1
    assert first.source == runplan.SOURCE_COMPILED

    second = conductor_compile.compile_run_plan(change, tasks, **kwargs)
    assert spawn.calls == 1, "a cache hit must not reach the model"
    assert second.fingerprint == first.fingerprint
    assert second.to_dict() == first.to_dict()


def test_editing_the_change_invalidates_the_cache(change, tmp_path):
    spec_id, tasks = _load(change)
    spawn = RecordingSpawn(
        _reply(**{t["id"]: {"files": [f"src/{t['id']}.py"], "deps": []} for t in tasks})
    )
    kwargs = dict(spec_id=spec_id, repo=change.parents[2], cache_dir=tmp_path / "plans", spawn=spawn)

    conductor_compile.compile_run_plan(change, tasks, **kwargs)
    (change / "proposal.md").write_text("## Why\nBecause, revised.\n")
    conductor_compile.compile_run_plan(change, tasks, **kwargs)
    assert spawn.calls == 2


def test_force_recompiles_over_a_cache_hit(change, tmp_path):
    spec_id, tasks = _load(change)
    spawn = RecordingSpawn(_reply(**{t["id"]: {"files": ["a.py"], "deps": []} for t in tasks}))
    kwargs = dict(spec_id=spec_id, repo=change.parents[2], cache_dir=tmp_path / "plans", spawn=spawn)

    conductor_compile.compile_run_plan(change, tasks, **kwargs)
    conductor_compile.compile_run_plan(change, tasks, force=True, **kwargs)
    assert spawn.calls == 2


# --------------------------------------------------------------------------- #
# Paths that never reach a model
# --------------------------------------------------------------------------- #
def test_a_format_that_already_declares_file_scope_is_seeded_not_compiled(tmp_path):
    """Decision D1: devkit frontmatter is ground truth, so every devkit spec
    skips the model entirely rather than paying to re-infer what it already says."""
    d = tmp_path / "openspec" / "changes" / "seeded"
    d.mkdir(parents=True)
    (d / "tasks.md").write_text("## 1. G\n\n- [ ] 1.1 a\n")
    tasks = [
        {"id": "1.1", "title": "a", "kind": "impl", "deps": [], "files": ["src/a.py"], "path": "tasks.md"}
    ]
    spawn = RecordingSpawn("should never be called")
    plan = conductor_compile.compile_run_plan(
        d, tasks, spec_id="seeded", repo=tmp_path, cache_dir=tmp_path / "plans", spawn=spawn
    )
    assert spawn.calls == 0
    assert plan.source == runplan.SOURCE_SEED
    assert plan.by_id()["1.1"].files == ("src/a.py",)


def test_tail_tasks_do_not_by_themselves_trigger_a_compile(tmp_path):
    """A tail is held out of the fan-out on `kind`, so it never reaches the
    file-collision check and inferring a scope for it would buy nothing."""
    tasks = [
        {"id": "1.1", "kind": "impl", "files": ["src/a.py"], "deps": [], "path": "t.md"},
        {"id": "2.1", "kind": "e2e", "files": [], "deps": ["1.1"], "path": "t.md"},
    ]
    assert conductor_compile.needs_compile(tasks) == []


def test_no_llm_returns_the_baseline_without_spawning(change, tmp_path):
    spec_id, tasks = _load(change)
    spawn = RecordingSpawn("should never be called")
    plan = conductor_compile.compile_run_plan(
        change,
        tasks,
        spec_id=spec_id,
        repo=change.parents[2],
        cache_dir=tmp_path / "plans",
        allow_llm=False,
        spawn=spawn,
    )
    assert spawn.calls == 0 and plan.source == runplan.SOURCE_BASELINE
    merged, _ = runplan.apply_to_tasks(tasks, plan)
    assert merged == tasks, "the baseline plan must change nothing"


# --------------------------------------------------------------------------- #
# Degrading on a bad response
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "reply, why",
    [
        ("I could not work it out, sorry.", "no JSON at all"),
        ('```json\n{"tasks": [{"id": "9.9", "files": []}]}\n```', "an id that does not exist"),
        ('```json\n{"tasks": [{"id": "1.1", "files": []}]}\n```', "an incomplete task set"),
        ('```json\n{"tasks": "nope"}\n```', "the wrong shape"),
    ],
)
def test_an_unusable_response_degrades_to_the_baseline(change, tmp_path, reply, why):
    spec_id, tasks = _load(change)
    plan = conductor_compile.compile_run_plan(
        change,
        tasks,
        spec_id=spec_id,
        repo=change.parents[2],
        cache_dir=tmp_path / "plans",
        spawn=RecordingSpawn(reply),
    )
    assert plan.source == runplan.SOURCE_BASELINE, why
    merged, notes = runplan.apply_to_tasks(tasks, plan)
    assert merged == tasks
    assert notes, "the reason must reach the run journal"


def test_a_failed_spawn_does_not_raise(change, tmp_path):
    def boom(*_a, **_k):
        raise RuntimeError("provider unavailable")

    spec_id, tasks = _load(change)
    plan = conductor_compile.compile_run_plan(
        change, tasks, spec_id=spec_id, repo=change.parents[2], cache_dir=tmp_path / "plans", spawn=boom
    )
    assert plan.source == runplan.SOURCE_BASELINE
    assert "provider unavailable" in plan.notes[0]


def test_a_rejected_response_is_not_cached(change, tmp_path):
    """Otherwise one bad answer would be pinned for the life of the change
    version, and the next run would silently inherit it."""
    spec_id, tasks = _load(change)
    bad = RecordingSpawn("no json here")
    kwargs = dict(spec_id=spec_id, repo=change.parents[2], cache_dir=tmp_path / "plans")

    conductor_compile.compile_run_plan(change, tasks, spawn=bad, **kwargs)
    good = RecordingSpawn(_reply(**{t["id"]: {"files": [f"src/{t['id']}.py"], "deps": []} for t in tasks}))
    plan = conductor_compile.compile_run_plan(change, tasks, spawn=good, **kwargs)
    assert good.calls == 1 and plan.source == runplan.SOURCE_COMPILED


@pytest.mark.parametrize("bad", ["../../etc/passwd", "/etc/passwd", "src/../../out.py", "..\\..\\win.py"])
def test_paths_escaping_the_repo_reject_the_plan(change, tmp_path, bad):
    """A file scope becomes a worker's declared write surface, so a traversal
    here is not a formatting nit. `lstrip("./")` looked like the obvious
    normalisation and is exactly wrong: it takes a character set, so it eats the
    whole `../../` and hands back a plausible-looking repo-relative path."""
    spec_id, tasks = _load(change)
    reply = _reply(
        **{
            "1.1": {"files": [bad], "deps": []},
            "1.2": {"files": ["src/api.py"], "deps": []},
            "2.1": {"files": ["tests/e2e.py"], "deps": []},
        }
    )
    plan = conductor_compile.compile_run_plan(
        change, tasks, spec_id=spec_id, repo=change.parents[2], cache_dir=tmp_path / "plans", spawn=RecordingSpawn(reply)
    )
    assert plan.source == runplan.SOURCE_BASELINE


def test_a_leading_dot_slash_is_normalised_not_rejected(change, tmp_path):
    spec_id, tasks = _load(change)
    reply = _reply(
        **{
            "1.1": {"files": ["./src/parser.py"], "deps": []},
            "1.2": {"files": ["src/api.py"], "deps": []},
            "2.1": {"files": ["tests/e2e.py"], "deps": []},
        }
    )
    plan = conductor_compile.compile_run_plan(
        change, tasks, spec_id=spec_id, repo=change.parents[2], cache_dir=tmp_path / "plans", spawn=RecordingSpawn(reply)
    )
    assert plan.by_id()["1.1"].files == ("src/parser.py",)


def test_kind_comes_from_the_artifact_not_the_model(change, tmp_path):
    """The `[e2e]` tag is authored. A model that omits or contradicts it must
    not be able to pull 2.1 back into the fan-out."""
    spec_id, tasks = _load(change)
    reply = _reply(
        **{
            "1.1": {"files": ["src/parser.py"], "deps": []},
            "1.2": {"files": ["src/api.py"], "deps": []},
            "2.1": {"files": ["tests/e2e.py"], "deps": [], "kind": "impl"},
        }
    )
    plan = conductor_compile.compile_run_plan(
        change, tasks, spec_id=spec_id, repo=change.parents[2], cache_dir=tmp_path / "plans", spawn=RecordingSpawn(reply)
    )
    assert plan.by_id()["2.1"].kind == "e2e"


# --------------------------------------------------------------------------- #
# Cross-task overlap re-scan (go-20260730-133115: compile can under-report a
# file shared by two sibling tasks when it decides each task's `files` in
# isolation, leaving the file-collision check with nothing to catch)
# --------------------------------------------------------------------------- #
def test_the_prompt_instructs_a_final_cross_task_overlap_rescan():
    """Pins the anti-omission instruction in place. Deciding each task's
    `files` independently is exactly how a shared file ends up recorded on
    only one of two tasks that both touch it -- the prompt must explicitly
    tell the model to re-check for that before it answers, not just ask for
    `files` per task and hope."""
    prompt = conductor_compile.PROMPT
    assert "Final pass" in prompt
    assert "shared file declared by only one of them" in prompt
    assert "in isolation" in prompt


# --------------------------------------------------------------------------- #
# Cross-task dependency re-scan (runplan.unordered_file_collisions() catches an
# unordered pair of same-file writers after the fact -- the prompt must also
# tell the model to close that gap itself, not just declare `files` correctly)
# --------------------------------------------------------------------------- #
def test_the_prompt_instructs_a_final_cross_task_deps_rescan():
    """Declaring a shared file on both co-writers is not enough on its own --
    `unordered_file_collisions()` fails loud unless one is a `deps` ancestor of
    the other. The prompt must tell the model to check for that missing edge,
    not just for the missing file declaration."""
    prompt = conductor_compile.PROMPT
    assert "re-check `deps`" in prompt
    assert "nothing ordering them" in prompt


def test_the_rescan_instruction_reaches_the_formatted_prompt(change, tmp_path):
    """The final-pass instruction is static text in `PROMPT`, but this proves
    `.format()` doesn't accidentally consume or truncate it via a stray `{`/`}`
    collision with the task list or spec path."""
    spec_id, tasks = _load(change)
    spawn = RecordingSpawn(
        _reply(**{t["id"]: {"files": [f"src/{t['id']}.py"], "deps": []} for t in tasks})
    )
    conductor_compile.compile_run_plan(
        change, tasks, spec_id=spec_id, repo=change.parents[2], cache_dir=tmp_path / "plans", spawn=spawn
    )
    assert "Final pass" in spawn.prompts[0]


# --------------------------------------------------------------------------- #
# The CLI must not exit 0 on a plan that leaves impl tasks scope-less
# --------------------------------------------------------------------------- #
def test_the_cli_fails_loudly_when_impl_tasks_stay_scope_less(tmp_path, capsys):
    """`worktrail-compile` used to always exit 0, even when the resulting plan
    left non-tail-kind tasks with no file scope -- a live run would later
    refuse to fan those out (`validate_task_metadata`), but the CLI itself
    gave no signal. `--no-llm` degrades every task to the baseline (empty
    files) deterministically, without spawning anything, so this reproduces
    the gap without needing a fake model reply."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text("## 1. Core\n\n- [ ] 1.1 Add the parser\n- [ ] 1.2 Wire the endpoint\n")

    rc = conductor_compile.main([str(d), "--no-llm"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "1.1" in err and "1.2" in err
    assert "scope" in err.lower()


def test_the_cli_json_mode_also_fails_loudly_when_impl_tasks_stay_scope_less(tmp_path, capsys):
    """`--json` used to return 0 right after printing the plan, never reaching
    the scope-gap check below -- so a caller that only checks the exit code
    (rather than parsing stdout for empty `files`) saw a silent success even
    though a live run would immediately refuse to fan these tasks out."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text("## 1. Core\n\n- [ ] 1.1 Add the parser\n- [ ] 1.2 Wire the endpoint\n")

    rc = conductor_compile.main([str(d), "--no-llm", "--json"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert "1.1" in err and "1.2" in err
    assert "scope" in err.lower()
    # stdout must still be the plain compiled plan -- a caller piping it into
    # `json.loads` must not see the error text mixed into the payload.
    json.loads(out)


# --------------------------------------------------------------------------- #
# The CLI auto-repairs a plan that leaves same-file tasks unordered instead of
# failing on it (go-20260805-172326: a real compile left 2.1/2.2 both
# declaring one file with no dep between them). `runplan.apply_to_tasks()`
# closes the gap itself now, so `unordered_file_collisions()` on the merged
# tasks the CLI actually schedules against is empty and the collision is no
# longer reachable through this path -- see `apply_to_tasks_closes_an_...`
# coverage in `test_runplan.py` for the repair logic itself.
# --------------------------------------------------------------------------- #
def test_the_cli_auto_repairs_an_unordered_file_collision(tmp_path, capsys):
    import subprocess
    from unittest.mock import patch

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 Add the parser\n\n"
        "## 2. Verify\n\n- [ ] 2.1 Check a\n- [ ] 2.2 Check b\n"
    )

    reply = _reply(
        **{
            "1.1": {"files": ["src/parser.py"], "deps": []},
            "2.1": {"files": ["tests/check.py"], "deps": ["1.1"]},
            "2.2": {"files": ["tests/check.py"], "deps": ["1.1"]},  # siblings, unordered
        }
    )
    with patch("worktrail.conductor.compile._default_spawn", return_value=reply):
        rc = conductor_compile.main([str(d)])
    out, err = capsys.readouterr()
    assert rc == 0
    assert "tests/check.py" not in err and "ERROR" not in err
    assert "auto-repaired 1 ordering edge" in out
    # 2.2 was authored after 2.1, so it is the one that grew the repair edge.
    assert "2.2        deps=1.1,2.1" in out


def test_the_cli_json_mode_auto_repairs_an_unordered_file_collision(tmp_path, capsys):
    from unittest.mock import patch
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 Add the parser\n\n"
        "## 2. Verify\n\n- [ ] 2.1 Check a\n- [ ] 2.2 Check b\n"
    )

    reply = _reply(
        **{
            "1.1": {"files": ["src/parser.py"], "deps": []},
            "2.1": {"files": ["tests/check.py"], "deps": ["1.1"]},
            "2.2": {"files": ["tests/check.py"], "deps": ["1.1"]},
        }
    )
    with patch("worktrail.conductor.compile._default_spawn", return_value=reply):
        rc = conductor_compile.main([str(d), "--json"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert "tests/check.py" not in err and "ERROR" not in err
    payload = json.loads(out)  # stdout must stay a clean, parseable plan

    # `--json` prints the compiled RunPlan itself, not the merged/repaired
    # task list -- the repair only exists once a caller runs the plan through
    # `apply_to_tasks()`, same as every other consumer (live runs included).
    # Round-trip the printed plan the way a real caller would and confirm the
    # repaired edge lands on the merged tasks it would actually schedule.
    from worktrail.taskformats import resolve

    _, tasks = resolve.load_spec(str(d))
    merged, notes = runplan.apply_to_tasks(tasks, runplan.RunPlan.from_dict(payload))
    by_id = {t["id"]: t for t in merged}
    assert by_id["2.2"]["deps"] == ["1.1", "2.1"]
    assert runplan.unordered_file_collisions(merged) == []
    assert any("auto-repaired" in n for n in notes)


def test_the_cli_json_mode_exits_zero_when_every_task_gets_scope(tmp_path, capsys):
    """The counterpart: full scope in `--json` mode must not trip the gap check."""
    import json as json_module
    import subprocess
    from unittest.mock import patch

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text("## 1. Core\n\n- [ ] 1.1 Add the parser\n- [ ] 1.2 Wire the endpoint\n")

    reply = (
        "```json\n"
        + json_module.dumps(
            {
                "tasks": [
                    {"id": "1.1", "files": ["src/parser.py"], "deps": []},
                    {"id": "1.2", "files": ["src/api.py"], "deps": ["1.1"]},
                ]
            }
        )
        + "\n```\n"
    )
    with patch("worktrail.conductor.compile._default_spawn", return_value=reply):
        rc = conductor_compile.main([str(d), "--json"])
    assert rc == 0
    assert "ERROR" not in capsys.readouterr().err


def test_the_cli_exits_zero_when_every_task_gets_scope(tmp_path, capsys):
    """The counterpart: a plan with real scope for every task must not trip
    the new gap check."""
    import json
    import subprocess
    from unittest.mock import patch

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text("## 1. Core\n\n- [ ] 1.1 Add the parser\n- [ ] 1.2 Wire the endpoint\n")

    reply = (
        "```json\n"
        + json.dumps(
            {
                "tasks": [
                    {"id": "1.1", "files": ["src/parser.py"], "deps": []},
                    {"id": "1.2", "files": ["src/api.py"], "deps": ["1.1"]},
                ]
            }
        )
        + "\n```\n"
    )
    with patch("worktrail.conductor.compile._default_spawn", return_value=reply):
        rc = conductor_compile.main([str(d)])
    assert rc == 0
    assert "ERROR" not in capsys.readouterr().err


# JSON extraction
# --------------------------------------------------------------------------- #
def test_the_cli_refuses_a_spec_outside_a_git_repo(tmp_path, capsys):
    """The repo walk would otherwise land on `/` and put the plan cache in
    `/-worktrees/runplans`."""
    d = tmp_path / "openspec" / "changes" / "stray"
    d.mkdir(parents=True)
    (d / "tasks.md").write_text("## 1. G\n\n- [ ] 1.1 a\n")
    assert conductor_compile.main([str(d)]) == 1
    assert "not inside a git repository" in capsys.readouterr().err


def test_extract_prefers_the_last_fenced_block():
    """Models narrate a draft before committing; the trailing object is the answer."""
    text = '```json\n{"tasks": [{"id": "draft"}]}\n```\nActually:\n```json\n{"tasks": [{"id": "final"}]}\n```'
    assert conductor_compile._extract_json(text)["tasks"][0]["id"] == "final"


def test_extract_falls_back_to_an_unfenced_object():
    assert conductor_compile._extract_json('sure: {"tasks": []} done')["tasks"] == []


def test_extract_returns_none_when_there_is_nothing_to_parse():
    assert conductor_compile._extract_json("no object here") is None


# --------------------------------------------------------------------------- #
# TaskPlan.purpose: constrained to the repo's configured vocabulary
# --------------------------------------------------------------------------- #
def _with_purpose_tiers(repo: Path, tiers: str) -> None:
    policy_dir = repo / "docs" / "specs"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "go-policy.yaml").write_text(f"routing:\n  purpose_tiers:\n{tiers}")


def test_a_purpose_within_the_configured_vocabulary_is_kept(change, tmp_path):
    repo = change.parents[2]
    _with_purpose_tiers(repo, "    scaffolding: t3\n")
    spec_id, tasks = _load(change)
    reply = _reply(
        **{
            "1.1": {"files": ["src/parser.py"], "deps": [], "purpose": "scaffolding"},
            "1.2": {"files": ["src/api.py"], "deps": []},
            "2.1": {"files": ["tests/e2e.py"], "deps": []},
        }
    )
    spawn = RecordingSpawn(reply)
    plan = conductor_compile.compile_run_plan(
        change, tasks, spec_id=spec_id, repo=repo, cache_dir=tmp_path / "plans", spawn=spawn
    )
    assert '"purpose"' in spawn.prompts[0] and "scaffolding" in spawn.prompts[0], (
        "a repo with routing.purpose_tiers configured must get a purpose-requesting prompt"
    )
    assert plan.source == runplan.SOURCE_COMPILED
    by_id = {t.id: t for t in plan.tasks}
    assert by_id["1.1"].purpose == "scaffolding"
    assert by_id["1.2"].purpose == ""


def test_a_purpose_outside_the_vocabulary_is_dropped_not_rejected(change, tmp_path):
    """Mirrors `_validate_agent_entry()`'s handling of a malformed
    `agent_model`: the one field is dropped and warned about, the rest of the
    row -- and the whole payload -- is still trusted."""
    repo = change.parents[2]
    _with_purpose_tiers(repo, "    scaffolding: t3\n")
    spec_id, tasks = _load(change)
    reply = _reply(
        **{
            "1.1": {"files": ["src/parser.py"], "deps": [], "purpose": "not-a-real-tier"},
            "1.2": {"files": ["src/api.py"], "deps": []},
            "2.1": {"files": ["tests/e2e.py"], "deps": []},
        }
    )
    logs: list[str] = []
    plan = conductor_compile.compile_run_plan(
        change,
        tasks,
        spec_id=spec_id,
        repo=repo,
        cache_dir=tmp_path / "plans",
        spawn=RecordingSpawn(reply),
        log=logs.append,
    )
    assert plan.source == runplan.SOURCE_COMPILED
    by_id = {t.id: t for t in plan.tasks}
    assert by_id["1.1"].purpose == ""
    assert any("not-a-real-tier" in line for line in logs)


def test_no_purpose_tiers_configured_leaves_every_task_unset(change, tmp_path):
    spec_id, tasks = _load(change)
    reply = _reply(
        **{
            "1.1": {"files": ["src/parser.py"], "deps": [], "purpose": "scaffolding"},
            "1.2": {"files": ["src/api.py"], "deps": []},
            "2.1": {"files": ["tests/e2e.py"], "deps": []},
        }
    )
    spawn = RecordingSpawn(reply)
    plan = conductor_compile.compile_run_plan(
        change,
        tasks,
        spec_id=spec_id,
        repo=change.parents[2],
        cache_dir=tmp_path / "plans",
        spawn=spawn,
    )
    assert '"purpose"' not in spawn.prompts[0], (
        "a repo with no routing.purpose_tiers configured must not be asked to "
        "classify against a vocabulary it never declared"
    )
    assert plan.source == runplan.SOURCE_COMPILED
    assert all(t.purpose == "" for t in plan.tasks)


# --------------------------------------------------------------------------- #
# End to end: the parallelism a compile actually unlocks
# --------------------------------------------------------------------------- #
def test_a_compiled_plan_unlocks_parallelism_the_format_could_not_express(change, tmp_path):
    """`OpenSpecTaskSource` serialises within a section because it has no file
    scope to reason with. This is the payoff: with scope, the frontier widens."""
    from worktrail.orchestrator import coordinator

    spec_id, tasks = _load(change)
    assert [t["deps"] for t in tasks][:2] == [[], ["1.1"]], "baseline is sequential"
    assert len(coordinator.runnable_frontier(tasks, max_workers=4)) == 1

    plan = conductor_compile.compile_run_plan(
        change,
        tasks,
        spec_id=spec_id,
        repo=change.parents[2],
        cache_dir=tmp_path / "plans",
        spawn=RecordingSpawn(
            _reply(
                **{
                    "1.1": {"files": ["src/parser.py"], "deps": []},
                    "1.2": {"files": ["src/api.py"], "deps": []},
                    "2.1": {"files": ["tests/e2e.py"], "deps": ["1.1", "1.2"]},
                }
            )
        ),
    )
    merged, notes = runplan.apply_to_tasks(tasks, plan)
    frontier = coordinator.runnable_frontier(merged, max_workers=4)
    assert {t["id"] for t in frontier} == {"1.1", "1.2"}
    assert "2.1" not in {t["id"] for t in frontier}, "the tail is still held out"
    assert any("applied" in n for n in notes)
