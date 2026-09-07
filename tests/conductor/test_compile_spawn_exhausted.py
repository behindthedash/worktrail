"""A capacity-blocked compile is reported as capacity, not as a bad answer.

An exhausted `SpawnResult`'s `text` is the provider's "no capacity" notice, not
a worker answer. Before task 2.1 `_default_spawn` read `.text` off it anyway and
`compile_run_plan` degraded with `compile returned no JSON object` -- a note that
blames the model for a call it never received, and a `main()` exit code
indistinguishable from a genuine scope gap.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from worktrail.conductor import compile as conductor_compile
from worktrail.conductor import runplan
from worktrail.orchestrator import spawnlib

TASKS_MD = textwrap.dedent(
    """\
    ## 1. Core

    - [ ] 1.1 Add the parser
    - [ ] 1.2 Wire the endpoint
    """
)

# What the provider prints when the account's usage window is gone. The whole
# point of the fix: this must never be handed to `_extract_json`.
PROVIDER_NOTICE = "Claude usage limit reached. Your limit will reset at 5pm."


@pytest.fixture()
def change(tmp_path: Path) -> Path:
    """A real `git init`-ed repo -- `main()`'s `_git_repo_root()` shells out."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-parser"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text(TASKS_MD)
    return d


def _load(change: Path):
    from worktrail.taskformats import resolve

    return resolve.load_spec(str(change))


def _good_reply(tasks) -> str:
    rows = [{"id": t["id"], "files": [f"src/{t['id']}.py"], "deps": []} for t in tasks]
    return "```json\n" + json.dumps({"tasks": rows}) + "\n```\n"


def _exhausted(failure_class: str = "billing") -> spawnlib.SpawnResult:
    return spawnlib.SpawnResult(
        text=PROVIDER_NOTICE,
        usage={},
        exhausted=True,
        failure_class=failure_class,
    )


def _ok(text: str) -> spawnlib.SpawnResult:
    return spawnlib.SpawnResult(text=text, usage={})


def _clear_ambient_agent_env():
    """Keep a stray host env var out of agent detection (mirrors test_compile)."""
    return patch.dict(
        "os.environ",
        {
            "GO_AGENT_CLI": "",
            "ORCH_AGENT": "",
            "OPENCODE_PARENT": "",
            "CODEX_CI": "",
            "CODEX_THREAD_ID": "",
        },
        clear=False,
    )


# --------------------------------------------------------------------------- #
# compile_run_plan
# --------------------------------------------------------------------------- #
def test_an_exhausted_compile_spawn_degrades_with_a_capacity_note(change, tmp_path):
    spec_id, tasks = _load(change)
    cache_dir = tmp_path / "plans"

    with (
        _clear_ambient_agent_env(),
        patch("worktrail.orchestrator.spawnlib.spawn_agent") as spawn_agent,
        patch.object(
            conductor_compile, "_extract_json", wraps=conductor_compile._extract_json
        ) as extract,
    ):
        spawn_agent.return_value = _exhausted()
        plan = conductor_compile.compile_run_plan(
            change,
            tasks,
            spec_id=spec_id,
            repo=change.parents[2],
            cache_dir=cache_dir,
        )

    assert plan.source == runplan.SOURCE_BASELINE
    note = plan.notes[0]
    assert note.startswith(conductor_compile.CAPACITY_NOTE_PREFIX), note
    assert "billing" in note
    assert "returned no JSON object" not in note

    # The payload was never extracted from the provider's error stream.
    assert extract.call_count == 0
    for call in extract.call_args_list:
        assert PROVIDER_NOTICE not in str(call)


def test_a_capacity_blocked_compile_writes_nothing_to_the_plan_cache(change, tmp_path):
    """Uncached, same as every other `give_up()`: the next attempt must get a
    fresh try rather than inheriting a capacity block for this content version."""
    spec_id, tasks = _load(change)
    cache_dir = tmp_path / "plans"

    with (
        _clear_ambient_agent_env(),
        patch("worktrail.orchestrator.spawnlib.spawn_agent") as spawn_agent,
    ):
        spawn_agent.return_value = _exhausted("rate_limit")
        conductor_compile.compile_run_plan(
            change, tasks, spec_id=spec_id, repo=change.parents[2], cache_dir=cache_dir
        )

    assert not cache_dir.exists() or not list(cache_dir.rglob("*.json"))


def test_an_unusable_payload_still_takes_its_existing_path(change, tmp_path):
    """A worker that *answered*, badly, is unchanged by this task: same note,
    same degradation -- the capacity path must not swallow it."""
    spec_id, tasks = _load(change)

    with (
        _clear_ambient_agent_env(),
        patch("worktrail.orchestrator.spawnlib.spawn_agent") as spawn_agent,
    ):
        spawn_agent.return_value = _ok("I could not work out the file scope, sorry.")
        plan = conductor_compile.compile_run_plan(
            change,
            tasks,
            spec_id=spec_id,
            repo=change.parents[2],
            cache_dir=tmp_path / "plans",
        )

    assert plan.source == runplan.SOURCE_BASELINE
    assert plan.notes[0].startswith("compile returned no JSON object")
    assert not plan.notes[0].startswith(conductor_compile.CAPACITY_NOTE_PREFIX)


def test_a_successful_compile_is_unchanged(change, tmp_path):
    spec_id, tasks = _load(change)

    with (
        _clear_ambient_agent_env(),
        patch("worktrail.orchestrator.spawnlib.spawn_agent") as spawn_agent,
    ):
        spawn_agent.return_value = _ok(_good_reply(tasks))
        plan = conductor_compile.compile_run_plan(
            change,
            tasks,
            spec_id=spec_id,
            repo=change.parents[2],
            cache_dir=tmp_path / "plans",
        )

    assert plan.source == runplan.SOURCE_COMPILED
    assert plan.notes == ()
    assert plan.by_id()["1.1"].files == ("src/1.1.py",)


def test_the_explicit_cell_override_spawn_also_fails_closed(change, tmp_path):
    """`--agent`/`--model` goes through `_spawn_with_explicit_cell`, the second
    of compile's two spawn sites -- it must fail closed the same way."""
    spec_id, tasks = _load(change)

    with (
        _clear_ambient_agent_env(),
        patch("worktrail.orchestrator.spawnlib.spawn_agent") as spawn_agent,
    ):
        spawn_agent.return_value = _exhausted()
        plan = conductor_compile.compile_run_plan(
            change,
            tasks,
            spec_id=spec_id,
            repo=change.parents[2],
            cache_dir=tmp_path / "plans",
            spawn=lambda prompt, cwd, timeout, log: conductor_compile._default_spawn(
                prompt, cwd, timeout, log, agent="codex-sub", model="override-model"
            ),
        )

    assert spawn_agent.call_args.kwargs["tier"] == "explicit"
    assert plan.notes[0].startswith(conductor_compile.CAPACITY_NOTE_PREFIX)


# --------------------------------------------------------------------------- #
# main()
# --------------------------------------------------------------------------- #
def test_main_exits_2_with_blocked_no_capacity_on_stderr(change, tmp_path, capsys):
    with (
        _clear_ambient_agent_env(),
        patch("worktrail.orchestrator.spawnlib.spawn_agent") as spawn_agent,
    ):
        spawn_agent.return_value = _exhausted()
        rc = conductor_compile.main(
            [str(change), "--cache-dir", str(tmp_path / "plans")]
        )

    assert rc == 2
    err = capsys.readouterr().err
    line = next(
        (ln for ln in err.splitlines() if ln.startswith("blocked_no_capacity:")), None
    )
    assert line is not None, err
    assert conductor_compile.CAPACITY_NOTE_PREFIX in line
    assert "ERROR:" not in err, "a capacity block is not a scope/ordering gap"


def test_main_still_exits_1_for_a_scope_gap_after_an_unusable_answer(
    change, tmp_path, capsys
):
    """The existing return-1 paths are untouched: a worker that answered badly
    leaves 1.1/1.2 scopeless, which is still a scope-gap exit 1, not exit 2."""
    with (
        _clear_ambient_agent_env(),
        patch("worktrail.orchestrator.spawnlib.spawn_agent") as spawn_agent,
    ):
        spawn_agent.return_value = _ok("no json here")
        rc = conductor_compile.main(
            [str(change), "--cache-dir", str(tmp_path / "plans")]
        )

    assert rc == 1
    err = capsys.readouterr().err
    assert "blocked_no_capacity:" not in err
