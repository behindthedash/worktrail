"""Tests for the advisory DAG-shape / wall-clock signal (parallelism.py).

The incident this guards against (docstring of the module): a correct plan
whose same-file repair serialised 19 tasks, with no signal until hours in.
"""

from __future__ import annotations

import json

from worktrail.conductor import parallelism


def _task(tid, *, deps=(), files=(), kind="impl"):
    return {"id": tid, "deps": list(deps), "files": list(files), "kind": kind}


def _chain(n, file="src/hot.py"):
    return [
        _task(f"t{i}", deps=[f"t{i - 1}"] if i else [], files=[file]) for i in range(n)
    ]


def test_a_fully_serialised_chain_is_reported_as_width_one():
    prof = parallelism.profile(_chain(19))
    assert (prof.tasks, prof.critical_path, prof.width) == (19, 19, 1)
    assert prof.serialized
    assert prof.hot_files == ("src/hot.py",)


def test_a_short_chain_is_serial_but_not_worth_a_warning():
    prof = parallelism.profile(_chain(parallelism.SERIAL_WARN_MIN_TASKS - 1))
    assert prof.width == 1
    assert not prof.serialized
    assert parallelism.format_warning(prof, 100.0, "x") is None


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
    assert not prof.serialized
    assert prof.hot_files == ()


def test_a_nearly_serial_dag_with_one_stray_parallel_pair_still_warns():
    """The incident change's real shape: 16 tasks, critical path 13, width 2."""
    tasks = _chain(13)
    tasks += [_task(f"s{i}", deps=["t0"], files=[f"s{i}.py"]) for i in range(3)]
    prof = parallelism.profile(tasks)
    assert (prof.tasks, prof.critical_path) == (16, 13)
    assert prof.width > 1
    assert prof.serialized


def test_a_wide_dag_whose_chain_is_under_the_fraction_does_not_warn():
    tasks = _chain(6) + [_task(f"s{i}", files=[f"s{i}.py"]) for i in range(6)]
    prof = parallelism.profile(tasks)
    assert (prof.tasks, prof.critical_path) == (12, 6)
    assert not prof.serialized


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


def test_the_warning_names_the_hot_file_and_the_projected_hours():
    prof = parallelism.profile(_chain(19))
    line = parallelism.format_warning(prof, 19 * 50.0, "50 min/task, test")
    assert line.startswith(
        "WARN: task DAG is effectively serial (19 tasks, critical path 19, width 1)"
    )
    assert "~15.8 h" in line
    assert "src/hot.py" in line and "consolidate" in line
