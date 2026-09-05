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
def test_compiling_the_same_change_twice_spawns_nothing_the_second_time(
    change, tmp_path
):
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
    kwargs = {
        "spec_id": spec_id,
        "repo": change.parents[2],
        "cache_dir": tmp_path / "plans",
        "spawn": spawn,
    }

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
    kwargs = {
        "spec_id": spec_id,
        "repo": change.parents[2],
        "cache_dir": tmp_path / "plans",
        "spawn": spawn,
    }

    conductor_compile.compile_run_plan(change, tasks, **kwargs)
    (change / "proposal.md").write_text("## Why\nBecause, revised.\n")
    conductor_compile.compile_run_plan(change, tasks, **kwargs)
    assert spawn.calls == 2


def test_force_recompiles_over_a_cache_hit(change, tmp_path):
    spec_id, tasks = _load(change)
    spawn = RecordingSpawn(
        _reply(**{t["id"]: {"files": ["a.py"], "deps": []} for t in tasks})
    )
    kwargs = {
        "spec_id": spec_id,
        "repo": change.parents[2],
        "cache_dir": tmp_path / "plans",
        "spawn": spawn,
    }

    conductor_compile.compile_run_plan(change, tasks, **kwargs)
    conductor_compile.compile_run_plan(change, tasks, force=True, **kwargs)
    assert spawn.calls == 2


# --------------------------------------------------------------------------- #
# `force` refuses to clobber a plan that active task worktrees were already
# fanned out under (go-20260812-165021 / 20260812-171538-fix-worktrail-compile-s-non:
# two `--force` recompiles of byte-identical tasks.md produced two different,
# each individually valid, dependency graphs -- non-determinism inherent to the
# model call. Safe before any worktree exists; unsafe once one does, since a
# resumed run would then disagree with the plan its own worktree was built
# under. See `compile_run_plan`'s `allow_force_over_active_worktrees`.)
# --------------------------------------------------------------------------- #
def test_force_is_refused_when_task_worktrees_already_exist_for_the_spec(
    change, tmp_path
):
    from worktrail.orchestrator import worktree as wt

    spec_id, tasks = _load(change)
    repo = change.parents[2]
    kwargs = {"spec_id": spec_id, "repo": repo, "cache_dir": tmp_path / "plans"}

    first_spawn = RecordingSpawn(
        _reply(**{t["id"]: {"files": ["a.py"], "deps": []} for t in tasks})
    )
    first = conductor_compile.compile_run_plan(
        change, tasks, spawn=first_spawn, **kwargs
    )
    assert first_spawn.calls == 1

    wt_base = wt.default_worktree_base(repo)
    (wt_base / f"{spec_id}-{tasks[0]['id'].lower()}").mkdir(parents=True)

    second_spawn = RecordingSpawn(
        _reply(**{t["id"]: {"files": ["b.py"], "deps": []} for t in tasks})
    )
    logs: list[str] = []
    second = conductor_compile.compile_run_plan(
        change, tasks, force=True, spawn=second_spawn, log=logs.append, **kwargs
    )
    assert second_spawn.calls == 0, (
        "an active-worktree spec must not reach the model on --force"
    )
    assert second.to_dict() == first.to_dict(), (
        "the plan already backing the worktree must be kept"
    )
    assert any("refused" in line for line in logs)


def test_allow_force_over_active_worktrees_overrides_the_guard(change, tmp_path):
    from worktrail.orchestrator import worktree as wt

    spec_id, tasks = _load(change)
    repo = change.parents[2]
    kwargs = {"spec_id": spec_id, "repo": repo, "cache_dir": tmp_path / "plans"}

    conductor_compile.compile_run_plan(
        change,
        tasks,
        spawn=RecordingSpawn(
            _reply(**{t["id"]: {"files": ["a.py"], "deps": []} for t in tasks})
        ),
        **kwargs,
    )

    wt_base = wt.default_worktree_base(repo)
    (wt_base / f"{spec_id}-{tasks[0]['id'].lower()}").mkdir(parents=True)

    second_spawn = RecordingSpawn(
        _reply(**{t["id"]: {"files": ["b.py"], "deps": []} for t in tasks})
    )
    second = conductor_compile.compile_run_plan(
        change,
        tasks,
        force=True,
        allow_force_over_active_worktrees=True,
        spawn=second_spawn,
        **kwargs,
    )
    assert second_spawn.calls == 1
    assert second.by_id()[tasks[0]["id"]].files == ("b.py",)


def test_force_still_recompiles_when_no_task_worktrees_exist(change, tmp_path):
    """The common case (no run has started yet, or none is in progress) must
    keep working exactly as before this guard: a bare `--force` still reaches
    the model without needing the override."""
    spec_id, tasks = _load(change)
    kwargs = {
        "spec_id": spec_id,
        "repo": change.parents[2],
        "cache_dir": tmp_path / "plans",
    }

    conductor_compile.compile_run_plan(
        change,
        tasks,
        spawn=RecordingSpawn(
            _reply(**{t["id"]: {"files": ["a.py"], "deps": []} for t in tasks})
        ),
        **kwargs,
    )
    second_spawn = RecordingSpawn(
        _reply(**{t["id"]: {"files": ["b.py"], "deps": []} for t in tasks})
    )
    second = conductor_compile.compile_run_plan(
        change, tasks, force=True, spawn=second_spawn, **kwargs
    )
    assert second_spawn.calls == 1
    assert second.by_id()[tasks[0]["id"]].files == ("b.py",)


def test_the_default_spawn_patch_site_still_receives_the_same_call_shape(
    change, tmp_path
):
    """`_default_spawn` remains the fallback seam for callers that patch it
    directly, so the positional call contract must stay intact."""
    from unittest.mock import patch

    spec_id, tasks = _load(change)
    reply = _reply(
        **{t["id"]: {"files": [f"src/{t['id']}.py"], "deps": []} for t in tasks}
    )
    kwargs = {
        "spec_id": spec_id,
        "repo": change.parents[2],
        "cache_dir": tmp_path / "plans",
    }

    with patch(
        "worktrail.conductor.compile._default_spawn", return_value=reply
    ) as default_spawn:
        plan = conductor_compile.compile_run_plan(change, tasks, **kwargs)

    assert plan.source == runplan.SOURCE_COMPILED
    assert default_spawn.call_count == 1
    prompt, cwd, timeout, log = default_spawn.call_args.args
    assert cwd == change.parents[2]
    assert timeout == conductor_compile.COMPILE_TIMEOUT_DEFAULT
    assert isinstance(prompt, str) and prompt
    assert callable(log)


def test_the_default_spawn_policy_for_an_unconfigured_repo_keeps_pre_change_argv_inputs(
    change, tmp_path
):
    """An empty repo-local policy/routing setup must still resolve to the
    operator's configured `default_tier` (conftest.py's seeded `t2-build`),
    with no preferred target and no fallback hops -- `spawn_agent()` itself
    handles model/target selection from there."""
    from unittest.mock import patch

    spec_id, tasks = _load(change)
    reply = _reply(
        **{t["id"]: {"files": [f"src/{t['id']}.py"], "deps": []} for t in tasks}
    )

    with (
        patch.dict(
            "os.environ",
            {
                "GO_AGENT_CLI": "",
                "ORCH_AGENT": "",
                "OPENCODE_PARENT": "",
                "CODEX_CI": "",
                "CODEX_THREAD_ID": "",
            },
            clear=False,
        ),
        patch("worktrail.orchestrator.spawnlib.spawn_agent") as spawn_agent,
    ):
        spawn_agent.return_value = type("SpawnResult", (), {"text": reply})()
        plan = conductor_compile.compile_run_plan(
            change,
            tasks,
            spec_id=spec_id,
            repo=change.parents[2],
            cache_dir=tmp_path / "plans",
        )

    assert plan.source == runplan.SOURCE_COMPILED
    assert spawn_agent.call_count == 1

    prompt, cwd = spawn_agent.call_args.args
    kwargs = spawn_agent.call_args.kwargs
    assert cwd == change.parents[2]
    assert kwargs["tier"] == "t2-build"
    assert kwargs["prefer"] is None
    assert isinstance(prompt, str) and prompt


def _clear_ambient_agent_env(tmp_path):
    """Same ambient-env guard as the unconfigured-repo test above -- prevents
    a stray host env var from leaking into agent detection and making these
    tier-resolution assertions flaky. Does not touch WORKTRAIL_ROUTING_FILE
    (conftest.py's own per-test seed stays in effect for these tests)."""
    from unittest.mock import patch

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


def test_repo_policy_roles_compile_tier_wins_over_default_tier(change, tmp_path):
    """`docs/specs/worktrail-go-policy.yaml`'s `routing.roles.compile.tier`
    must beat `routing.default_tier` -- `_resolve_compile_tier()`'s documented
    precedence (task 5.2 AC)."""
    from unittest.mock import patch

    repo = change.parents[2]
    policy_dir = repo / ".worktrail"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "policy.yaml").write_text(
        "routing:\n"
        "  targets:\n"
        "    codex-sub:\n"
        "      harness: codex\n"
        "      pool: subscription\n"
        "  tiers:\n"
        "    t2-build:\n"
        "      codex-sub:\n"
        "        model: gpt-5.4-mini\n"
        "  default_tier: t2-build\n"
        "  roles:\n"
        "    compile:\n"
        "      tier: t2-build\n"
        "      prefer: codex-sub\n"
    )

    spec_id, tasks = _load(change)
    reply = _reply(
        **{t["id"]: {"files": [f"src/{t['id']}.py"], "deps": []} for t in tasks}
    )

    with (
        _clear_ambient_agent_env(tmp_path),
        patch("worktrail.orchestrator.spawnlib.spawn_agent") as spawn_agent,
    ):
        spawn_agent.return_value = type("SpawnResult", (), {"text": reply})()
        plan = conductor_compile.compile_run_plan(
            change,
            tasks,
            spec_id=spec_id,
            repo=repo,
            cache_dir=tmp_path / "plans",
        )

    assert plan.source == runplan.SOURCE_COMPILED
    assert spawn_agent.call_count == 1
    kwargs = spawn_agent.call_args.kwargs
    assert kwargs["tier"] == "t2-build"
    assert kwargs["prefer"] == "codex-sub"


def test_explicit_agent_and_model_override_a_declared_target(change, tmp_path):
    """`--agent`/`--model`, threaded through as `_default_spawn`'s explicit
    kwargs, override a declared target's configured model for one spawn --
    the "explicit-cell override" task 5.2's AC describes -- without touching
    the operator's real routing file."""
    import os
    from unittest.mock import patch

    from worktrail.router.policy import ROUTING_FILE_ENV, resolved_routing_file_path

    repo = change.parents[2]
    spec_id, tasks = _load(change)
    reply = _reply(
        **{t["id"]: {"files": [f"src/{t['id']}.py"], "deps": []} for t in tasks}
    )
    seeded_routing_file = resolved_routing_file_path()
    seeded_routing_text = seeded_routing_file.read_text(encoding="utf-8")

    with (
        _clear_ambient_agent_env(tmp_path),
        patch("worktrail.orchestrator.spawnlib.spawn_agent") as spawn_agent,
    ):
        spawn_agent.return_value = type("SpawnResult", (), {"text": reply})()
        plan = conductor_compile.compile_run_plan(
            change,
            tasks,
            spec_id=spec_id,
            repo=repo,
            cache_dir=tmp_path / "plans",
            spawn=lambda prompt, cwd, timeout, log: conductor_compile._default_spawn(
                prompt,
                cwd,
                timeout,
                log,
                agent="claude-sub",
                model="override-model",
            ),
        )

    assert plan.source == runplan.SOURCE_COMPILED
    assert spawn_agent.call_count == 1
    kwargs = spawn_agent.call_args.kwargs
    assert kwargs["tier"] == "explicit"
    # The env var override must be restored to its pre-call (unset) state, not
    # left pointed at the throwaway file -- resolved_routing_file_path() then
    # falls back to conftest.py's own GO_ROUTING_FILE-pointed seed again.
    assert os.environ.get(ROUTING_FILE_ENV) is None
    assert resolved_routing_file_path() == seeded_routing_file
    # The real routing file itself must be untouched by the override.
    assert seeded_routing_file.read_text(encoding="utf-8") == seeded_routing_text


def test_explicit_model_without_agent_is_rejected(change, tmp_path):
    """`--model` with no `--agent` has no target to attach it to."""
    with (
        _clear_ambient_agent_env(tmp_path),
        pytest.raises(ValueError, match="--model requires --agent"),
    ):
        conductor_compile._default_spawn(
            "prompt", change.parents[2], 60, lambda *_: None, model="some-model"
        )


def _git_change_dir(tmp_path: Path) -> Path:
    """A real `git init`-ed repo (unlike the `change` fixture's fake `.git`
    mkdir), required for `main()`'s `_git_repo_root()` subprocess call."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-parser"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text(TASKS_MD)
    return d


def test_cli_agent_and_model_flags_override_a_declared_target(tmp_path):
    """`--agent`/`--model` at the CLI, not just the `_default_spawn` kwargs
    directly (task 2.4's AC) -- beat the resolved tier the same way as the
    kwarg-level `test_explicit_agent_and_model_override_a_declared_target`."""
    from unittest.mock import patch

    d = _git_change_dir(tmp_path)
    _spec_id, tasks = _load(d)
    reply = _reply(
        **{t["id"]: {"files": [f"src/{t['id']}.py"], "deps": []} for t in tasks}
    )

    with (
        _clear_ambient_agent_env(tmp_path),
        patch("worktrail.orchestrator.spawnlib.spawn_agent") as spawn_agent,
    ):
        spawn_agent.return_value = type("SpawnResult", (), {"text": reply})()
        rc = conductor_compile.main(
            [
                str(d),
                "--agent",
                "codex-sub",
                "--model",
                "override-model",
                "--cache-dir",
                str(tmp_path / "plans"),
            ]
        )

    assert rc == 0
    assert spawn_agent.call_count == 1
    kwargs = spawn_agent.call_args.kwargs
    assert kwargs["tier"] == "explicit"


def test_cli_agent_flag_alone_prefers_the_target_within_the_resolved_tier(tmp_path):
    """A CLI `--agent` with no `--model` keeps that target's own configured
    model -- the partial-override case task 2.4 also names."""
    from unittest.mock import patch

    d = _git_change_dir(tmp_path)
    _spec_id, tasks = _load(d)
    reply = _reply(
        **{t["id"]: {"files": [f"src/{t['id']}.py"], "deps": []} for t in tasks}
    )

    with (
        _clear_ambient_agent_env(tmp_path),
        patch("worktrail.orchestrator.spawnlib.spawn_agent") as spawn_agent,
    ):
        spawn_agent.return_value = type("SpawnResult", (), {"text": reply})()
        rc = conductor_compile.main(
            [str(d), "--agent", "codex-sub", "--cache-dir", str(tmp_path / "plans")]
        )

    assert rc == 0
    assert spawn_agent.call_count == 1
    kwargs = spawn_agent.call_args.kwargs
    assert kwargs["tier"] != "explicit"
    assert kwargs["prefer"] == "codex-sub"


def test_cli_fallback_chain_flag_is_accepted_but_does_not_reach_spawn_agent(tmp_path):
    """`--fallback-chain` is kept for backward CLI compatibility only:
    `spawn_agent()`'s own tier-row reselection now owns capacity-gate
    degradation (`tests/orchestrator/test_spawnlib.py`'s
    `test_walks_past_a_capacity_gated_cell_to_the_next_target`), so the parsed
    chain must reach `_default_spawn` without error and without changing the
    resolved tier/prefer (task 2.5, reclassified -- see tasks.md)."""
    from unittest.mock import patch

    d = _git_change_dir(tmp_path)
    _spec_id, tasks = _load(d)
    reply = _reply(
        **{t["id"]: {"files": [f"src/{t['id']}.py"], "deps": []} for t in tasks}
    )

    with (
        _clear_ambient_agent_env(tmp_path),
        patch("worktrail.orchestrator.spawnlib.spawn_agent") as spawn_agent,
    ):
        spawn_agent.return_value = type("SpawnResult", (), {"text": reply})()
        rc = conductor_compile.main(
            [
                str(d),
                "--fallback-chain",
                "codex-sub,opencode-free",
                "--cache-dir",
                str(tmp_path / "plans"),
            ]
        )

    assert rc == 0
    assert spawn_agent.call_count == 1
    kwargs = spawn_agent.call_args.kwargs
    assert "fallback_agent" not in kwargs
    assert kwargs["tier"] == "t2-build"
    assert kwargs["prefer"] is None


def test_ambient_orch_model_env_vars_do_not_influence_compile_spawn(change, tmp_path):
    """`ORCH_OPENCODE_MODEL`/`ORCH_CODEX_MODEL` are not read anywhere in
    `spawnlib.py` -- model selection stays config-file driven
    (`routing.tiers`), per the reconciliation with the merged sibling change
    `model-tier-routing-remove-env-model-overrides` (task 2.6)."""
    from unittest.mock import patch

    spec_id, tasks = _load(change)
    reply = _reply(
        **{t["id"]: {"files": [f"src/{t['id']}.py"], "deps": []} for t in tasks}
    )

    env = {
        "GO_AGENT_CLI": "",
        "ORCH_AGENT": "",
        "OPENCODE_PARENT": "",
        "CODEX_CI": "",
        "CODEX_THREAD_ID": "",
        "ORCH_OPENCODE_MODEL": "some-opencode-model",
        "ORCH_CODEX_MODEL": "some-codex-model",
    }
    with (
        patch.dict("os.environ", env, clear=False),
        patch("worktrail.orchestrator.spawnlib.spawn_agent") as spawn_agent,
    ):
        spawn_agent.return_value = type("SpawnResult", (), {"text": reply})()
        plan = conductor_compile.compile_run_plan(
            change,
            tasks,
            spec_id=spec_id,
            repo=change.parents[2],
            cache_dir=tmp_path / "plans",
        )

    assert plan.source == runplan.SOURCE_COMPILED
    assert spawn_agent.call_count == 1
    kwargs = spawn_agent.call_args.kwargs
    assert kwargs["tier"] == "t2-build"
    assert kwargs["prefer"] is None


def test_an_injected_spawn_callable_bypasses_the_policy_resolver(change, tmp_path):
    """A caller-provided `spawn=` callable must be used verbatim, without
    consulting the default policy resolver first."""
    from unittest.mock import patch

    spec_id, tasks = _load(change)
    reply = _reply(
        **{t["id"]: {"files": [f"src/{t['id']}.py"], "deps": []} for t in tasks}
    )
    spawn = RecordingSpawn(reply)

    with patch(
        "worktrail.router.invocation_context.resolve", side_effect=AssertionError
    ):
        plan = conductor_compile.compile_run_plan(
            change,
            tasks,
            spec_id=spec_id,
            repo=change.parents[2],
            cache_dir=tmp_path / "plans",
            spawn=spawn,
        )

    assert spawn.calls == 1
    assert plan.source == runplan.SOURCE_COMPILED
    assert plan.by_id()[tasks[0]["id"]].files == (f"src/{tasks[0]['id']}.py",)


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
        {
            "id": "1.1",
            "title": "a",
            "kind": "impl",
            "deps": [],
            "files": ["src/a.py"],
            "path": "tasks.md",
        }
    ]
    spawn = RecordingSpawn("should never be called")
    plan = conductor_compile.compile_run_plan(
        d,
        tasks,
        spec_id="seeded",
        repo=tmp_path,
        cache_dir=tmp_path / "plans",
        spawn=spawn,
    )
    assert spawn.calls == 0
    assert plan.source == runplan.SOURCE_SEED
    assert plan.by_id()["1.1"].files == ("src/a.py",)


def test_a_change_with_full_declared_scope_is_seeded_with_tail_tasks_exempt(tmp_path):
    """Requirement: Declared scope satisfies compilation without a model call.

    Every `impl` task below declares at least one file; the `[e2e]`/`[cleanup]`
    tail tasks declare none, and must not be penalized for it -- `needs_compile`
    excludes them on `kind` alone (`compile.py`'s tail-exemption), so a fully
    declared implementation set is enough to seed the whole plan, tails included.
    """
    d = tmp_path / "openspec" / "changes" / "fully-declared"
    d.mkdir(parents=True)
    (d / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 a\n- [ ] 1.2 b\n\n## 2. Verify\n\n- [ ] 2.1 [e2e] check\n- [ ] 2.2 [cleanup] tidy\n"
    )
    tasks = [
        {
            "id": "1.1",
            "title": "a",
            "kind": "impl",
            "deps": [],
            "files": ["src/a.py"],
            "path": "tasks.md",
        },
        {
            "id": "1.2",
            "title": "b",
            "kind": "impl",
            "deps": ["1.1"],
            "files": ["src/a.py", "src/b.py"],
            "path": "tasks.md",
        },
        {
            "id": "2.1",
            "title": "check",
            "kind": "e2e",
            "deps": ["1.1", "1.2"],
            "files": [],
            "path": "tasks.md",
        },
        {
            "id": "2.2",
            "title": "tidy",
            "kind": "cleanup",
            "deps": ["2.1"],
            "files": [],
            "path": "tasks.md",
        },
    ]
    assert conductor_compile.needs_compile(tasks) == []

    spawn = RecordingSpawn("should never be called")
    plan = conductor_compile.compile_run_plan(
        d,
        tasks,
        spec_id="fully-declared",
        repo=tmp_path,
        cache_dir=tmp_path / "plans",
        spawn=spawn,
    )

    assert spawn.calls == 0, (
        "declared scope on every implementation task must satisfy compilation with no model call"
    )
    assert plan.source == runplan.SOURCE_SEED
    by_id = plan.by_id()
    assert by_id["1.1"].files == ("src/a.py",)
    assert by_id["1.2"].files == ("src/a.py", "src/b.py")
    assert by_id["2.1"].files == ()
    assert by_id["2.2"].files == ()


def test_partial_declared_scope_is_kept_and_gaps_fall_back_when_spawning_fails(
    tmp_path,
):
    """Requirement: Declared scope satisfies compilation without a model call.

    A declared file scope on one task must survive a failed compile attempt
    made on its still-scopeless sibling's behalf: `give_up()`'s baseline is
    built from the tasks' own fields (`_plan_from_tasks`), so 1.1's declaration
    is preserved even though 1.2 (the gap `needs_compile` triggers the model
    call for) never got an answer.
    """
    d = tmp_path / "openspec" / "changes" / "partial"
    d.mkdir(parents=True)
    (d / "tasks.md").write_text("## 1. Core\n\n- [ ] 1.1 a\n- [ ] 1.2 b\n")
    tasks = [
        {
            "id": "1.1",
            "title": "a",
            "kind": "impl",
            "deps": [],
            "files": ["src/a.py"],
            "path": "tasks.md",
        },
        {
            "id": "1.2",
            "title": "b",
            "kind": "impl",
            "deps": ["1.1"],
            "files": [],
            "path": "tasks.md",
        },
    ]
    assert conductor_compile.needs_compile(tasks) == ["1.2"]

    def boom(*_a, **_k):
        raise RuntimeError("provider unavailable")

    plan = conductor_compile.compile_run_plan(
        d,
        tasks,
        spec_id="partial",
        repo=tmp_path,
        cache_dir=tmp_path / "plans",
        spawn=boom,
    )

    assert plan.source == runplan.SOURCE_BASELINE
    by_id = plan.by_id()
    assert by_id["1.1"].files == ("src/a.py",), (
        "the declared scope must survive a failed compile"
    )
    assert by_id["1.2"].files == (), (
        "an undeclared task stays scopeless once the model call fails"
    )
    assert "provider unavailable" in plan.notes[0]


def test_editing_a_declared_files_scope_invalidates_the_cached_plan(tmp_path):
    """Requirement: Declared scope satisfies compilation without a model call.

    `runplan.fingerprint` folds each task's own `files` list into its hash --
    the task tuples compile actually reads, not `tasks.md`'s bytes -- so
    changing one task's declared scope must produce a new fingerprint and a
    fresh compile rather than serving the plan cached under the old scope.
    """
    d = tmp_path / "openspec" / "changes" / "declared"
    d.mkdir(parents=True)
    (d / "tasks.md").write_text("## 1. Core\n\n- [ ] 1.1 a\n- [ ] 1.2 b\n")
    tasks = [
        {
            "id": "1.1",
            "title": "a",
            "kind": "impl",
            "deps": [],
            "files": ["src/a.py"],
            "path": "tasks.md",
        },
        {
            "id": "1.2",
            "title": "b",
            "kind": "impl",
            "deps": ["1.1"],
            "files": [],
            "path": "tasks.md",
        },
    ]
    spawn = RecordingSpawn(
        _reply(
            **{
                "1.1": {"files": ["src/a.py"], "deps": []},
                "1.2": {"files": ["src/b.py"], "deps": ["1.1"]},
            }
        )
    )
    kwargs = {
        "spec_id": "declared",
        "repo": tmp_path,
        "cache_dir": tmp_path / "plans",
        "spawn": spawn,
    }

    first = conductor_compile.compile_run_plan(d, tasks, **kwargs)
    assert spawn.calls == 1
    assert first.source == runplan.SOURCE_COMPILED

    tasks[0]["files"] = ["src/a.py", "src/a_helper.py"]
    spawn.reply = _reply(
        **{
            "1.1": {"files": ["src/a.py", "src/a_helper.py"], "deps": []},
            "1.2": {"files": ["src/b.py"], "deps": ["1.1"]},
        }
    )
    second = conductor_compile.compile_run_plan(d, tasks, **kwargs)
    assert second.fingerprint != first.fingerprint, (
        "editing a declaration must not serve a stale cached plan"
    )
    assert spawn.calls == 2, "a changed fingerprint must miss the cache and recompile"
    assert second.by_id()["1.1"].files == ("src/a.py", "src/a_helper.py")


def test_tail_tasks_do_not_by_themselves_trigger_a_compile(tmp_path):
    """A tail is held out of the fan-out on `kind`, so it never reaches the
    file-collision check and inferring a scope for it would buy nothing."""
    tasks = [
        {
            "id": "1.1",
            "kind": "impl",
            "files": ["src/a.py"],
            "deps": [],
            "path": "t.md",
        },
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
        (
            '```json\n{"tasks": [{"id": "9.9", "files": []}]}\n```',
            "an id that does not exist",
        ),
        (
            '```json\n{"tasks": [{"id": "1.1", "files": []}]}\n```',
            "an incomplete task set",
        ),
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
        change,
        tasks,
        spec_id=spec_id,
        repo=change.parents[2],
        cache_dir=tmp_path / "plans",
        spawn=boom,
    )
    assert plan.source == runplan.SOURCE_BASELINE
    assert "provider unavailable" in plan.notes[0]


def test_a_rejected_response_is_not_cached(change, tmp_path):
    """Otherwise one bad answer would be pinned for the life of the change
    version, and the next run would silently inherit it."""
    spec_id, tasks = _load(change)
    bad = RecordingSpawn("no json here")
    kwargs = {
        "spec_id": spec_id,
        "repo": change.parents[2],
        "cache_dir": tmp_path / "plans",
    }

    conductor_compile.compile_run_plan(change, tasks, spawn=bad, **kwargs)
    good = RecordingSpawn(
        _reply(**{t["id"]: {"files": [f"src/{t['id']}.py"], "deps": []} for t in tasks})
    )
    plan = conductor_compile.compile_run_plan(change, tasks, spawn=good, **kwargs)
    assert good.calls == 1 and plan.source == runplan.SOURCE_COMPILED


@pytest.mark.parametrize(
    "bad", ["../../etc/passwd", "/etc/passwd", "src/../../out.py", "..\\..\\win.py"]
)
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
        change,
        tasks,
        spec_id=spec_id,
        repo=change.parents[2],
        cache_dir=tmp_path / "plans",
        spawn=RecordingSpawn(reply),
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
        change,
        tasks,
        spec_id=spec_id,
        repo=change.parents[2],
        cache_dir=tmp_path / "plans",
        spawn=RecordingSpawn(reply),
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
        change,
        tasks,
        spec_id=spec_id,
        repo=change.parents[2],
        cache_dir=tmp_path / "plans",
        spawn=RecordingSpawn(reply),
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
        change,
        tasks,
        spec_id=spec_id,
        repo=change.parents[2],
        cache_dir=tmp_path / "plans",
        spawn=spawn,
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
    (d / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 Add the parser\n- [ ] 1.2 Wire the endpoint\n"
    )

    rc = conductor_compile.main([str(d), "--no-llm"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "1.1" in err and "1.2" in err
    assert "scope" in err.lower()


def test_the_scope_gap_guidance_distinguishes_tail_kinds(tmp_path, capsys):
    """The remediation text used to list `docs/e2e/cleanup` as interchangeable
    tail-kind choices with no distinction of what each executes -- the
    contributing cause behind an author picking `[cleanup]` for a task that
    needed `[e2e]` (brief 20260904-164604). It must now name the difference:
    `e2e` spawns a worker, `cleanup`/`docs` execute nothing."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text("## 1. Core\n\n- [ ] 1.1 Add the parser\n")

    rc = conductor_compile.main([str(d), "--no-llm"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "e2e" in err and "spawns a worker" in err
    assert "cleanup" in err and "execute nothing" in err


def test_the_cli_json_mode_also_fails_loudly_when_impl_tasks_stay_scope_less(
    tmp_path, capsys
):
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
    (d / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 Add the parser\n- [ ] 1.2 Wire the endpoint\n"
    )

    rc = conductor_compile.main([str(d), "--no-llm", "--json"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert "1.1" in err and "1.2" in err
    assert "scope" in err.lower()
    # stdout must still be the plain compiled plan -- a caller piping it into
    # `json.loads` must not see the error text mixed into the payload.
    json.loads(out)


# --------------------------------------------------------------------------- #
# CI's requirement-coverage gate (req_coverage.find_uncovered_requirements) --
# same shape as the scope-gap tests above, for the new `uncovered` term.
# --------------------------------------------------------------------------- #
def test_the_cli_fails_loudly_when_a_declared_requirement_has_no_task_coverage(
    tmp_path, capsys
):
    """The task itself is fully scoped (no scope gap, no collision), so this
    isolates the requirement-coverage gate: a requirement newly declared under
    `## ADDED Requirements` but never named in `tasks.md` must still fail the
    compile, the same way `_print_scope_gap_error` fails one with no `files`."""
    import subprocess
    from unittest.mock import patch

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text("## 1. Core\n\n- [ ] 1.1 Add the parser\n")
    spec_dir = d / "specs" / "cap-a"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text(
        "## ADDED Requirements\n\n"
        "### Requirement: Totally Unmentioned Thing\n"
        "The thing shall do the thing.\n"
    )

    reply = _reply(**{"1.1": {"files": ["src/parser.py"], "deps": []}})
    with patch("worktrail.conductor.compile._default_spawn", return_value=reply):
        rc = conductor_compile.main([str(d)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "Totally Unmentioned Thing" in err
    assert "coverage" in err.lower()
    assert "tasks.md" in err


def test_the_cli_json_mode_also_fails_loudly_when_a_declared_requirement_has_no_task_coverage(
    tmp_path, capsys
):
    """`--json` counterpart: stdout must stay a clean, parseable plan while the
    non-zero exit code and requirement name still surface on stderr."""
    import subprocess
    from unittest.mock import patch

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text("## 1. Core\n\n- [ ] 1.1 Add the parser\n")
    spec_dir = d / "specs" / "cap-a"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text(
        "## ADDED Requirements\n\n"
        "### Requirement: Totally Unmentioned Thing\n"
        "The thing shall do the thing.\n"
    )

    reply = _reply(**{"1.1": {"files": ["src/parser.py"], "deps": []}})
    with patch("worktrail.conductor.compile._default_spawn", return_value=reply):
        rc = conductor_compile.main([str(d), "--json"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert "Totally Unmentioned Thing" in err
    assert "coverage" in err.lower()
    json.loads(out)


def test_the_cli_exits_zero_when_the_declared_requirement_is_covered_in_tasks_md(
    tmp_path, capsys
):
    """The counterpart: naming the requirement anywhere in `tasks.md` (D1's
    case-insensitive name-presence match) must not trip the gate."""
    import subprocess
    from unittest.mock import patch

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 Add the parser, covering Totally Mentioned Thing\n"
    )
    spec_dir = d / "specs" / "cap-a"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text(
        "## ADDED Requirements\n\n"
        "### Requirement: Totally Mentioned Thing\n"
        "The thing shall do the thing.\n"
    )

    reply = _reply(**{"1.1": {"files": ["src/parser.py"], "deps": []}})
    with patch("worktrail.conductor.compile._default_spawn", return_value=reply):
        rc = conductor_compile.main([str(d)])
    err = capsys.readouterr().err
    assert rc == 0
    assert "ERROR" not in err


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
    # 3.1/3.2 are independent of the 1.1 -> 2.1 -> 2.2 chain and unrelated to
    # what this test is about (the same-file repair below); they exist only
    # to widen the plan so its critical path does not itself trip the
    # plan-shape gate's serial rule (D2).
    (d / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 Add the parser\n\n"
        "## 2. Verify\n\n- [ ] 2.1 Check a\n- [ ] 2.2 Check b\n\n"
        "## 3. Unrelated\n\n- [ ] 3.1 Do a\n- [ ] 3.2 Do b\n"
    )

    reply = _reply(
        **{
            "1.1": {"files": ["src/parser.py"], "deps": []},
            "2.1": {"files": ["tests/check.py"], "deps": ["1.1"]},
            "2.2": {
                "files": ["tests/check.py"],
                "deps": ["1.1"],
            },  # siblings, unordered
            "3.1": {"files": ["src/other.py"], "deps": []},
            "3.2": {"files": ["src/other2.py"], "deps": []},
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
    import subprocess
    from unittest.mock import patch

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    # 3.1/3.2 widen the plan so its critical path does not itself trip the
    # plan-shape gate's serial rule (D2); see the sibling text-mode test.
    (d / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 Add the parser\n\n"
        "## 2. Verify\n\n- [ ] 2.1 Check a\n- [ ] 2.2 Check b\n\n"
        "## 3. Unrelated\n\n- [ ] 3.1 Do a\n- [ ] 3.2 Do b\n"
    )

    reply = _reply(
        **{
            "1.1": {"files": ["src/parser.py"], "deps": []},
            "2.1": {"files": ["tests/check.py"], "deps": ["1.1"]},
            "2.2": {"files": ["tests/check.py"], "deps": ["1.1"]},
            "3.1": {"files": ["src/other.py"], "deps": []},
            "3.2": {"files": ["src/other2.py"], "deps": []},
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


# --------------------------------------------------------------------------- #
# CI's structural scope-check gate (worktrail.router.check_compile_markers)
# reads this marker instead of re-running compile with a live model -- it must
# exist exactly when the CLI reports success, and never on a failing run.
# --------------------------------------------------------------------------- #
def test_a_passing_cli_run_writes_the_compile_marker(tmp_path, capsys):
    import subprocess
    from unittest.mock import patch

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 Add the parser\n- [ ] 1.2 Wire the endpoint\n"
    )

    reply = _reply(
        **{
            "1.1": {"files": ["src/parser.py"], "deps": []},
            "1.2": {"files": ["src/api.py"], "deps": ["1.1"]},
        }
    )
    with patch("worktrail.conductor.compile._default_spawn", return_value=reply):
        rc = conductor_compile.main([str(d)])
    capsys.readouterr()
    assert rc == 0

    marker = d / conductor_compile.COMPILE_MARKER_NAME
    assert marker.is_file()

    from worktrail.taskformats import resolve

    _spec_id, tasks = resolve.load_spec(str(d))
    assert marker.read_text(encoding="utf-8").strip() == runplan.fingerprint(d, tasks)


def test_a_failing_cli_run_does_not_write_the_compile_marker(tmp_path, capsys):
    """`--no-llm` degrades every task to an empty-files baseline (see the
    scope-gap test above), so this reproduces a failing run without a fake
    model reply. A marker recorded here would be worse than no marker at all
    -- CI would read it as "this content passed" when it never did."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 Add the parser\n- [ ] 1.2 Wire the endpoint\n"
    )

    rc = conductor_compile.main([str(d), "--no-llm"])
    capsys.readouterr()
    assert rc == 1
    assert not (d / conductor_compile.COMPILE_MARKER_NAME).exists()


def test_an_uncovered_requirement_does_not_write_the_compile_marker(tmp_path, capsys):
    """Mirrors the case above for the `uncovered` term of the same guard
    (`compile.py`'s `not (gaps or collisions or uncovered)`). The spawn reply
    fully scopes the one task so this run fails on requirement coverage
    alone, not also on scope gaps -- pinning `uncovered` specifically rather
    than riding along on `gaps`."""
    import subprocess
    from unittest.mock import patch

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text("## 1. Core\n\n- [ ] 1.1 Add the parser\n")
    spec_dir = d / "specs" / "cap-a"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text(
        "## ADDED Requirements\n\n"
        "### Requirement: Totally Unmentioned Thing\n"
        "The thing shall do the thing.\n"
    )

    reply = _reply(**{"1.1": {"files": ["src/parser.py"], "deps": []}})
    with patch("worktrail.conductor.compile._default_spawn", return_value=reply):
        rc = conductor_compile.main([str(d)])
    capsys.readouterr()
    assert rc == 1
    assert not (d / conductor_compile.COMPILE_MARKER_NAME).exists()


def test_a_stale_marker_is_overwritten_by_the_next_passing_compile(tmp_path, capsys):
    import subprocess
    from unittest.mock import patch

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text("## 1. Core\n\n- [ ] 1.1 Add the parser\n")

    marker = d / conductor_compile.COMPILE_MARKER_NAME
    marker.write_text("stale-fingerprint-from-a-previous-content-version\n")

    reply = _reply(**{"1.1": {"files": ["src/parser.py"], "deps": []}})
    with patch("worktrail.conductor.compile._default_spawn", return_value=reply):
        rc = conductor_compile.main([str(d)])
    capsys.readouterr()
    assert rc == 0
    assert (
        marker.read_text(encoding="utf-8").strip()
        != "stale-fingerprint-from-a-previous-content-version"
    )


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
    (d / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 Add the parser\n- [ ] 1.2 Wire the endpoint\n"
    )

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
    (d / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 Add the parser\n- [ ] 1.2 Wire the endpoint\n"
    )

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
    policy_dir = repo / ".worktrail"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "policy.yaml").write_text(f"routing:\n  purposes:\n{tiers}")


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
        change,
        tasks,
        spec_id=spec_id,
        repo=repo,
        cache_dir=tmp_path / "plans",
        spawn=spawn,
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
            "1.1": {
                "files": ["src/parser.py"],
                "deps": [],
                "purpose": "not-a-real-tier",
            },
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
def test_a_compiled_plan_unlocks_parallelism_the_format_could_not_express(
    change, tmp_path
):
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


# --------------------------------------------------------------------------- #
# `_git_repo_root` must resolve to the canonical checkout, not a linked
# worktree's own toplevel -- otherwise a worktree pre-compile and the later
# canonical `full-real` run never share a cache entry (see AGENTS.md
# `conductor/`).
# --------------------------------------------------------------------------- #
@pytest.fixture()
def repo_with_worktree(tmp_path: Path):
    """A canonical checkout plus one linked worktree off it, both real git
    checkouts -- `--git-common-dir` only differs from `--show-toplevel` for an
    actual `git worktree add` checkout, not a bare `.git`-marker directory."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)

    worktree = tmp_path / "repo-worktrees" / "some-branch"
    worktree.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "some-branch", str(worktree)],
        check=True,
    )
    return repo, worktree


def test_git_repo_root_resolves_a_linked_worktree_to_the_canonical_checkout(
    repo_with_worktree,
):
    repo, worktree = repo_with_worktree
    spec_dir = worktree / "openspec" / "changes" / "add-thing"
    spec_dir.mkdir(parents=True)

    resolved_from_worktree = conductor_compile._git_repo_root(spec_dir)
    resolved_from_repo = conductor_compile._git_repo_root(repo)

    assert resolved_from_worktree == repo.resolve()
    assert resolved_from_worktree == resolved_from_repo


def test_cli_compiling_from_a_worktree_caches_under_the_canonical_repo(
    repo_with_worktree, capsys
):
    """Regression for the divergence: `worktrail-compile` invoked with a spec
    dir inside a linked worktree (exactly how the SDD skills run it) must
    still print a cache path under `<repo>-worktrees/runplans`, not
    `<worktree>-worktrees/runplans` -- otherwise this compile's result is
    never read back by the canonical `full-real` run."""
    repo, worktree = repo_with_worktree
    d = worktree / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text("## 1. Core\n\n- [ ] 1.1 Add the parser\n")

    rc = conductor_compile.main([str(d), "--no-llm"])
    out = capsys.readouterr().out
    assert (
        rc == 1
    )  # scope gap expected with --no-llm; the cache line still prints first
    cache_line = next(
        line for line in out.splitlines() if line.strip().startswith("cache:")
    )
    expected_cache_dir = conductor_compile.default_cache_dir(repo)
    assert str(expected_cache_dir) in cache_line
    assert str(worktree) not in cache_line


def test_the_cli_rejects_a_repaired_dag_that_collapses_to_a_serial_chain(
    tmp_path, capsys
):
    """The 2026-09-01 incident shape: every task declares the same file, so the
    same-file repair serialises all of them. That is now a plan-shape
    rejection (`PlanShapeError`, design D2), not just a stderr note."""
    import subprocess
    from unittest.mock import patch

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    n = 7
    (d / "tasks.md").write_text(
        "## 1. Core\n\n" + "".join(f"- [ ] 1.{i} Step {i}\n" for i in range(1, n + 1))
    )
    reply = _reply(
        **{f"1.{i}": {"files": ["src/triage.py"], "deps": []} for i in range(1, n + 1)}
    )
    with patch("worktrail.conductor.compile._default_spawn", return_value=reply):
        rc = conductor_compile.main([str(d)])
    out, err = capsys.readouterr()
    assert rc == 1
    assert "same-file chain" in err
    assert "src/triage.py" in err
    assert not (d / conductor_compile.COMPILE_MARKER_NAME).exists()

    with patch("worktrail.conductor.compile._default_spawn", return_value=reply):
        rc = conductor_compile.main([str(d), "--json"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""  # rejected before any plan payload is ever printed
    assert "same-file chain" in err
    assert not (d / conductor_compile.COMPILE_MARKER_NAME).exists()


def test_the_cli_prints_the_parallelism_summary_without_a_warning_for_a_fan_out(
    tmp_path, capsys
):
    import subprocess
    from unittest.mock import patch

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text("## 1. Core\n\n- [ ] 1.1 A\n- [ ] 1.2 B\n")
    reply = _reply(
        **{
            "1.1": {"files": ["src/a.py"], "deps": []},
            "1.2": {"files": ["src/b.py"], "deps": []},
        }
    )
    with patch("worktrail.conductor.compile._default_spawn", return_value=reply):
        rc = conductor_compile.main([str(d)])
    out, err = capsys.readouterr()
    assert rc == 0
    assert "parallelism: 2 task(s), critical path 1, width 2" in out
    assert "WARN" not in err


# --------------------------------------------------------------------------- #
# Plan-shape gate (design D2): PlanShapeError propagation
# --------------------------------------------------------------------------- #
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "plan_shape"


def _make_change(repo: Path, fixture_name: str) -> Path:
    import subprocess

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text((FIXTURES_DIR / fixture_name).read_text())
    return d


def test_the_serial_fixture_is_rejected_with_no_llm_and_no_marker(tmp_path, capsys):
    """The `full-1788369246` group 4 shape: three tasks chained on
    `queue_triage.py`, plus a task touching a module with an existing test
    file it never declares. All of the fixture's tasks declare their own
    `files:`, so `--no-llm` never has to spawn a model."""
    repo = tmp_path / "repo"
    repo.mkdir()
    d = _make_change(repo, "serial-group4.tasks.md")
    test_counterpart = repo / "tests" / "workqueue" / "test_create_handoff.py"
    test_counterpart.parent.mkdir(parents=True)
    test_counterpart.write_text("")

    rc = conductor_compile.main([str(d), "--no-llm"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert "same-file chain" in err
    assert "queue_triage.py" in err
    assert "missing test scope" in err
    assert "create_handoff.py" in err
    assert not (d / conductor_compile.COMPILE_MARKER_NAME).exists()

    rc = conductor_compile.main([str(d), "--no-llm", "--json"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""
    assert "same-file chain" in err
    assert not (d / conductor_compile.COMPILE_MARKER_NAME).exists()


def test_the_consolidated_fixture_passes(tmp_path, capsys):
    """One task per module, tests co-scoped, plus an `[e2e]` tail: no rule
    fires."""
    repo = tmp_path / "repo"
    repo.mkdir()
    d = _make_change(repo, "consolidated.tasks.md")

    rc = conductor_compile.main([str(d), "--no-llm"])
    _out, err = capsys.readouterr()
    assert rc == 0
    assert "same-file chain" not in err
    assert "serial" not in err
    assert "missing test scope" not in err
    assert (d / conductor_compile.COMPILE_MARKER_NAME).exists()


def test_the_cleanup_verification_mismatch_fixture_is_rejected(tmp_path, capsys):
    """A `[cleanup]`-tagged task whose title reads as a command to run
    (`live` incident go-20260904-153010's exact wording) fails compile,
    naming the task and pointing at `[e2e]` instead. Every task declares its
    own `files:`, so `--no-llm` never has to spawn a model."""
    repo = tmp_path / "repo"
    repo.mkdir()
    d = _make_change(repo, "cleanup-verification-mismatch.tasks.md")

    rc = conductor_compile.main([str(d), "--no-llm"])
    _out, err = capsys.readouterr()
    assert rc == 1
    assert "cleanup verification mismatch" in err
    assert "2.1" in err
    assert "[e2e]" in err
    assert not (d / conductor_compile.COMPILE_MARKER_NAME).exists()


def test_compile_run_plan_raises_plan_shape_error_on_a_cache_hit(tmp_path):
    """A plan cached by an earlier, laxer policy must still be rejected on a
    later cache-hit read, not only the first time it is compiled."""
    from worktrail.conductor.compile import PlanShapeError, compile_run_plan

    repo = tmp_path / "repo"
    repo.mkdir()
    d = _make_change(repo, "serial-group4.tasks.md")
    from worktrail.taskformats import resolve

    spec_id, tasks = resolve.load_spec(str(d))
    cache_dir = tmp_path / "cache"

    with pytest.raises(PlanShapeError):
        compile_run_plan(
            d, tasks, spec_id=spec_id, repo=repo, cache_dir=cache_dir, allow_llm=False
        )

    # Cached (the shape check runs *after* `runplan.store`), so a second call
    # -- the cache-hit path -- must raise again from the cached plan alone.
    with pytest.raises(PlanShapeError):
        compile_run_plan(
            d, tasks, spec_id=spec_id, repo=repo, cache_dir=cache_dir, allow_llm=False
        )
