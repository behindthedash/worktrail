"""Tests for the DAG-shape signal (parallelism.py): profile()/estimate_minutes()
stay advisory, shape_problems() is design D2's compile-time enforcement.

The incident this guards against (docstring of the module): a correct plan
whose same-file repair serialised 19 tasks, with no signal until hours in.
"""

from __future__ import annotations

import json

from worktrail.conductor import parallelism


def _task(tid, *, deps=(), files=(), kind="impl", title=""):
    return {
        "id": tid,
        "deps": list(deps),
        "files": list(files),
        "kind": kind,
        "title": title,
    }


def _chain(n, file="src/hot.py"):
    return [
        _task(f"t{i}", deps=[f"t{i - 1}"] if i else [], files=[file]) for i in range(n)
    ]


def test_a_fully_serialised_chain_is_reported_as_width_one():
    prof = parallelism.profile(_chain(19))
    assert (prof.tasks, prof.critical_path, prof.width) == (19, 19, 1)
    assert prof.hot_files == ("src/hot.py",)


def test_a_fan_out_reports_its_width_and_its_longest_chain():
    tasks = [
        _task("a", files=["a.py"]),
        _task("b", deps=["a"], files=["b.py"]),
        _task("c", deps=["a"], files=["c.py"]),
        _task("d", deps=["a"], files=["d.py"]),
        _task("e", deps=["b"], files=["e.py"]),
    ]
    prof = parallelism.profile(tasks)
    assert (prof.tasks, prof.critical_path, prof.width) == (5, 3, 3)
    assert prof.hot_files == ()


def test_tail_tasks_are_excluded_like_the_collision_check_does():
    tasks = _chain(3) + [_task("v", deps=["t2"], files=["src/hot.py"], kind="e2e")]
    assert parallelism.profile(tasks).tasks == 3
    assert parallelism.profile([]) == parallelism.Profile(0, 0, 0, ())


def test_the_estimate_uses_prior_journal_timing_summed_per_task(tmp_path):
    journal = tmp_path / "run-x.json"
    journal.write_text(
        json.dumps(
            {
                "entries": [
                    {"task": "1.1", "role": "implement", "duration_s": 600},
                    {"task": "1.1", "role": "review", "duration_s": 600},
                    {"task": "1.2", "role": "implement", "duration_s": 2400},
                    {"task": "1.3", "role": "gate"},  # no duration: ignored
                ]
            }
        )
    )
    prof = parallelism.profile(_chain(10))
    minutes, basis = parallelism.estimate_minutes(prof, [journal])
    # per-task: 1.1 = 20 min, 1.2 = 40 min -> mean 30 -> chain of 10 = 300
    assert minutes == 300.0
    assert "2 prior task(s)" in basis


def test_the_estimate_falls_back_to_the_default_and_says_so(tmp_path):
    (tmp_path / "run-bad.json").write_text("not json")
    prof = parallelism.profile(_chain(6))
    minutes, basis = parallelism.estimate_minutes(prof, [tmp_path / "run-bad.json"])
    assert minutes == 6 * parallelism.DEFAULT_TASK_MINUTES
    assert "default" in basis


# --------------------------------------------------------------------------- #
# shape_problems() -- design D2's three rules
# --------------------------------------------------------------------------- #
def test_a_new_module_with_no_test_counterpart_passes(tmp_path):
    tasks = [
        _task("a", files=["src/a.py"]),
        _task("b", files=["src/b.py"]),
    ]
    assert parallelism.shape_problems(tasks, tmp_path, {}) == []


def test_empty_fanout_passes(tmp_path):
    tasks = [_task("v", files=["src/hot.py"], kind="e2e")]
    assert parallelism.shape_problems(tasks, tmp_path, {}) == []


def test_serial_rule_fires_naming_the_longest_chain(tmp_path):
    tasks = [
        _task(f"t{i}", deps=[f"t{i - 1}"] if i else [], files=[f"f{i}.py"])
        for i in range(5)
    ]
    problems = parallelism.shape_problems(tasks, tmp_path, {})
    assert len(problems) == 1
    assert "serial" in problems[0]
    assert "t0 -> t1 -> t2 -> t3 -> t4" in problems[0]


def test_serial_rule_respects_the_policy_threshold(tmp_path):
    tasks = [
        _task(f"t{i}", deps=[f"t{i - 1}"] if i else [], files=[f"f{i}.py"])
        for i in range(5)
    ]
    policy = {"compile_max_critical_path_over_width": 10}
    assert parallelism.shape_problems(tasks, tmp_path, policy) == []


def test_serial_rule_default_threshold_is_two(tmp_path):
    # critical path 3, width 1: 3 > max(1, 2) fires with the default policy.
    tasks = [
        _task(f"t{i}", deps=[f"t{i - 1}"] if i else [], files=[f"f{i}.py"])
        for i in range(3)
    ]
    problems = parallelism.shape_problems(tasks, tmp_path, {})
    assert any("serial" in p for p in problems)


def test_same_file_chain_rule_fires_naming_ids_and_file(tmp_path):
    tasks = _chain(3, file="src/hot.py")
    # Suppress the serial rule so only the same-file rule is under test.
    policy = {"compile_max_critical_path_over_width": 100}
    problems = parallelism.shape_problems(tasks, tmp_path, policy)
    assert len(problems) == 1
    assert "same-file chain" in problems[0]
    assert "t0 -> t1 -> t2" in problems[0]
    assert "src/hot.py" in problems[0]


def test_same_file_chain_rule_respects_the_policy_threshold(tmp_path):
    tasks = _chain(3, file="src/hot.py")
    policy = {
        "compile_max_critical_path_over_width": 100,
        "compile_max_same_file_chain": 5,
    }
    assert parallelism.shape_problems(tasks, tmp_path, policy) == []


def test_same_file_chain_rule_default_threshold_is_two(tmp_path):
    tasks = _chain(3, file="src/hot.py")
    problems = parallelism.shape_problems(tasks, tmp_path, {})
    assert any("same-file chain" in p for p in problems)


def test_missing_test_scope_rule_names_the_existing_test_file(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_foo.py").write_text("")
    tasks = [_task("a", files=["src/foo.py"])]
    problems = parallelism.shape_problems(tasks, tmp_path, {})
    assert len(problems) == 1
    assert "a" in problems[0]
    assert "src/foo.py" in problems[0]
    assert "test_foo.py" in problems[0]


def test_missing_test_scope_rule_exempt_when_no_test_counterpart_exists(tmp_path):
    tasks = [_task("a", files=["src/bar.py"])]
    assert parallelism.shape_problems(tasks, tmp_path, {}) == []


def test_missing_test_scope_rule_exempt_when_the_task_co_scopes_its_test(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_foo.py").write_text("")
    tasks = [_task("a", files=["src/foo.py", "tests/test_foo.py"])]
    assert parallelism.shape_problems(tasks, tmp_path, {}) == []


def test_missing_test_scope_rule_exempt_for_non_src_paths_like_docs(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_guide.py").write_text("")
    tasks = [_task("a", files=["docs/guide.md"])]
    assert parallelism.shape_problems(tasks, tmp_path, {}) == []


def test_missing_test_scope_rule_exempt_for_docs_kind_task(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_foo.py").write_text("")
    tasks = [_task("a", files=["src/foo.py"], kind="docs")]
    assert parallelism.shape_problems(tasks, tmp_path, {}) == []


def test_missing_test_scope_rule_exempt_for_tail_kinds(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_foo.py").write_text("")
    tasks = [_task("a", files=["src/foo.py"], kind="e2e")]
    assert parallelism.shape_problems(tasks, tmp_path, {}) == []


# --------------------------------------------------------------------------- #
# shape_problems() -- design D2 rule 4: cleanup/verification-body mismatch
# --------------------------------------------------------------------------- #
def test_cleanup_task_with_backticked_command_is_rejected(tmp_path):
    tasks = [
        _task("v", kind="cleanup", title="Run `pytest -q` and confirm it is green")
    ]
    problems = parallelism.shape_problems(tasks, tmp_path, {})
    assert len(problems) == 1
    assert "cleanup verification mismatch" in problems[0]
    assert "v" in problems[0]
    assert "[e2e]" in problems[0]


def test_cleanup_task_with_live_incident_wording_is_rejected(tmp_path):
    # The exact incident wording (go-20260904-153010, task 2.1): no
    # backticks, but an imperative "Run" plus recognizable commands.
    tasks = [
        _task(
            "2.1",
            kind="cleanup",
            title=(
                "Run PYTHONPATH=src pytest -q and PYTHONPATH=src python3 -m "
                "worktrail.orchestrator.orchestrate check; confirm both are "
                "green and run openspec validate --strict"
            ),
        )
    ]
    problems = parallelism.shape_problems(tasks, tmp_path, {})
    assert any("cleanup verification mismatch" in p and "2.1" in p for p in problems)


def test_genuinely_inert_cleanup_task_passes(tmp_path):
    tasks = [_task("v", kind="cleanup", title="Remove debug logging left in tasks 1-4")]
    assert parallelism.shape_problems(tasks, tmp_path, {}) == []


def test_equivalent_e2e_task_is_unaffected(tmp_path):
    tasks = [
        _task("v", kind="e2e", title="Run `pytest -q` and confirm it is green")
    ]
    assert parallelism.shape_problems(tasks, tmp_path, {}) == []


def test_docs_tail_task_is_unaffected(tmp_path):
    tasks = [
        _task("v", kind="docs", title="Run `pytest -q` and confirm it is green")
    ]
    assert parallelism.shape_problems(tasks, tmp_path, {}) == []


def test_cleanup_mismatch_fires_even_when_fanout_is_empty(tmp_path):
    tasks = [
        _task("v", kind="cleanup", title="Run `pytest -q` and confirm it is green")
    ]
    problems = parallelism.shape_problems(tasks, tmp_path, {})
    assert any("cleanup verification mismatch" in p for p in problems)
