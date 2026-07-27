#!/usr/bin/env python3
"""
Parallel SDD Orchestrator -- top-level loop (record / replay / live).

Ties the pieces together end to end:

    plan groups
      -> tick loop { runnable_frontier -> per-task worktree + role-sequence }
      -> integration (merge order from the group DAG, worktree teardown)
      -> tail (e2e, cleanup, serialized)
      -> one PR per group

Spawn is pluggable (the ONE seam between deterministic tests and live agents):

  ScriptedSpawn  canned outcomes (a `scenario` controls fix-loop triggers).
  ReplaySpawn    replays a recorded cassette -> DETERMINISTIC -> golden-able.
  LiveSpawn      real Agent-tool spawns (design section 11, Option B). GATED.

Record/replay makes a non-deterministic live run into a REPEATABLE golden:
  1. record a live run once  -> cassette.json (real worker outcomes)
  2. replay the cassette      -> deterministic output -> commit as golden
  3. enhance the orchestrator -> `check` replays + diffs -> measured behavior delta

Usage:
  python3 orchestrate.py demo
  python3 orchestrate.py run    [--file F | --spec DIR] [--cassette C] [--write-golden P]
  python3 orchestrate.py record [--file F | --spec DIR] --out cassette.json
  python3 orchestrate.py check  [--file F | --spec DIR] [--cassette C] --golden P
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import coordinator
from . import dispatch
from . import worktree
from ..taskformats import resolve as taskformats

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent
DEFAULT_FIXTURE = _SKILL / ".fixtures" / "toy-spec.json"
DEFAULT_GOLDEN = _SKILL / ".fixtures" / "toy-spec.golden.txt"

TERMINAL = {"done", "failed", "escalated"}
ROLE_BY_STATUS = {
    "pending": dispatch.ROLE_IMPLEMENT,
    "claimed": dispatch.ROLE_IMPLEMENT,
    "implementing": dispatch.ROLE_IMPLEMENT,
    "reviewing": dispatch.ROLE_REVIEW,
    "fixing": dispatch.ROLE_FIX,
    "cleaning": dispatch.ROLE_CLEANUP,
}
_REPORT_FIELDS = (
    "status",
    "head_sha",
    "tests",
    "review_status",
    "critical_issues",
    "major_issues",
    "context_quality",
    "missing_context",
    "notes",
)


# --------------------------------------------------------------------------- #
# Spawns (the seam)
# --------------------------------------------------------------------------- #
class ScriptedSpawn:
    """Canned outcomes. `scenario.fail_review_once` lists tasks that FAIL their
    first review (exercising the fix loop) then PASS."""

    label = "placeholder spawn; no live agents"

    def __init__(self, scenario: Optional[Dict[str, Any]] = None) -> None:
        self.scenario = scenario or {}
        self.counters: Dict = {}

    def __call__(self, role: str, task: Dict[str, Any]) -> str:
        tid = task["id"]
        sha = f"{tid[-3:]}{role[:3]}"
        review_field = "null"
        if role == dispatch.ROLE_REVIEW:
            first_fail = tid in self.scenario.get("fail_review_once", [])
            seen = self.counters.get((tid, "revfail"), 0)
            if first_fail and seen < 1:
                self.counters[(tid, "revfail")] = seen + 1
                review_field = '"FAILED"'
            else:
                review_field = '"PASSED"'
        return (
            "work complete.\n```json\n"
            f'{{"task":"{tid}","step":"{role}","status":"success",'
            f'"head_sha":"{sha}","tests":"passed","review_status":{review_field},'
            f'"critical_issues":0,"major_issues":0,"notes":"scripted"}}\n```'
        )


def _wrap_report(rep: Dict[str, Any], tid: str, role: str) -> str:
    body = {
        "task": tid,
        "step": role,
        "status": rep.get("status", "success"),
        "head_sha": rep.get("head_sha", ""),
        "tests": rep.get("tests", "none"),
        "review_status": rep.get("review_status", None),
        "critical_issues": rep.get("critical_issues", 0),
        "major_issues": rep.get("major_issues", 0),
        "notes": rep.get("notes", "replayed"),
    }
    return "recorded run.\n```json\n" + json.dumps(body) + "\n```"


class ReplaySpawn:
    """Replays a recorded cassette. Deterministic -> golden-able. A missing
    entry raises (a sign the orchestrator's dispatch sequence changed)."""

    def __init__(self, cassette: Dict[str, Any]) -> None:
        entries = cassette.get("entries", [])
        self._q: Dict = defaultdict(deque)
        for e in entries:
            self._q[(e["task"], e["role"])].append(e.get("report", {}))
        self.label = f"replay cassette ({len(entries)} entries)"

    def __call__(self, role: str, task: Dict[str, Any]) -> str:
        key = (task["id"], role)
        if not self._q[key]:
            raise KeyError(f"cassette has no remaining '{role}' report for {task['id']}")
        return _wrap_report(self._q[key].popleft(), task["id"], role)


class LiveSpawn:
    """Real Agent-tool spawns (design section 11, Option B). GATED.

    When enabled it will: dispatch.build_worker_prompt(role, task, ctx) ->
    spawn a fresh agent (run_in_background for parallel batches) -> capture the
    agent's final message -> return it (and, if recording, append to a cassette).
    """

    label = "LIVE agent spawn"

    def __call__(self, role: str, task: Dict[str, Any]) -> str:
        raise NotImplementedError(
            "LiveSpawn requires Agent-tool wiring and an explicit go-ahead. "
            "Build the prompt with dispatch.build_worker_prompt(), spawn the "
            "agent, capture its final message, and return it."
        )


# --------------------------------------------------------------------------- #
# Simulation -> deterministic text (+ optional cassette recording)
# --------------------------------------------------------------------------- #
def simulate_text(
    spec_id: str,
    tasks: List[Dict[str, Any]],
    spawn,
    max_workers: int = 3,
    record: Optional[List] = None,
) -> str:
    tasks = copy.deepcopy(tasks)
    for t in tasks:
        t.setdefault("status", "pending")
        t.setdefault("retry_count", 0)
    by_id = {t["id"]: t for t in tasks}
    lines: List[str] = []
    out = lines.append

    base = worktree.default_worktree_base("/repos/app")
    runnable_tasks = [t for t in tasks if t.get("status") not in coordinator.DONE]
    plan = coordinator.pr_plan(runnable_tasks)

    out("=" * 70)
    out(f"ORCHESTRATION   spec={spec_id}   max_workers={max_workers}")
    out(f"({spawn.label})")
    out("=" * 70)
    out("PR group plan:")
    for g in plan["groups"]:
        stack = f"   stacks-on={g['depends_on']}" if g["depends_on"] else ""
        out(
            f"  [{g['name']:9}] {', '.join(g['tasks'])}{stack}   reqs={', '.join(g['reqs']) or '-'}"
        )
    out(f"  tail (serialized): {', '.join(plan['tail'])}")
    out("")

    def drive(task: Dict[str, Any], indent: str) -> None:
        task["status"] = "claimed"
        while task["status"] not in TERMINAL:
            role = ROLE_BY_STATUS[task["status"]]
            report = dispatch.parse_report_back(spawn(role, task))
            if record is not None:
                record.append(
                    {
                        "task": report["task"],
                        "role": report["step"],
                        "report": {k: report.get(k) for k in _REPORT_FIELDS},
                    }
                )
            old, new = dispatch.apply_report(tasks, report, role)
            extra = f" [{report.get('review_status')}]" if role == dispatch.ROLE_REVIEW else ""
            out(f"{indent}{role:9} {old:12} -> {new:11} (retry={task['retry_count']}){extra}")

    tick = 0
    while True:
        frontier = coordinator.runnable_frontier(tasks, max_workers)
        if not frontier:
            break
        tick += 1
        out(f"TICK {tick}: parallel batch -> {', '.join(t['id'] for t in frontier)}")
        for ft in frontier:
            t = by_id[ft["id"]]
            wt = worktree.worktree_path(base, spec_id, t["id"])
            out(f"  + git worktree add -b {worktree.task_branch(spec_id, t['id'])} {wt} <base>")
            drive(t, "        ")
            out(f"  = {t['id']} {t['status']} (sha {t.get('head_sha')})")
        out("")

    out("INTEGRATION (merge order from group DAG):")
    order = [g for g in plan["groups"] if g["name"] == "base"] + [
        g for g in plan["groups"] if g["name"] != "base"
    ]
    for g in order:
        out(f"  merge [{g['name']}] on {worktree.group_branch(spec_id, g['name'])}")
        for tid in g["tasks"]:
            out(f"        git worktree remove {worktree.worktree_path(base, spec_id, tid)}")
    out("")

    out("TAIL (serialized on final integration branch):")
    for tid in plan["tail"]:
        drive(by_id[tid], f"  {tid} ")
    out("")

    out("PR PLAN (one PR per group):")
    for g in plan["groups"]:
        target = (
            "dev"
            if (g["name"] == "base" or not g["depends_on"])
            else f"{g['depends_on'][0]} (stacked)"
        )
        out(
            f"  PR {worktree.group_branch(spec_id, g['name']):30} -> {target:18} tasks={', '.join(g['tasks'])}"
        )
    out(
        f"  final PR (e2e+cleanup)             -> dev                tasks={', '.join(plan['tail'])}"
    )
    out("")

    done = sum(1 for t in tasks if t.get("status") in coordinator.DONE)
    out(
        f"SUMMARY: {done}/{len(tasks)} tasks done; "
        f"{len(plan['groups'])} group PRs + 1 final PR; ticks={tick}."
    )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Input assembly
# --------------------------------------------------------------------------- #
def _build(file: Optional[str], spec: Optional[str], cassette: Optional[str]):
    if spec:
        spec_id, tasks = taskformats.load_spec(spec)
        scenario: Dict[str, Any] = {}
    else:
        data = json.load(open(file or DEFAULT_FIXTURE))
        spec_id = data.get("spec_id", "spec")
        tasks = data["tasks"]
        scenario = data.get("scenario", {})
    if cassette:
        cass = json.load(open(cassette))
        spawn = ReplaySpawn(cass)
        recorded = {e["task"] for e in cass.get("entries", [])}
        if recorded:  # replay only what the cassette covers
            tasks = [t for t in tasks if t["id"] in recorded]
    else:
        spawn = ScriptedSpawn(scenario)
    return spec_id, tasks, spawn


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Top-level orchestration loop")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="Run the default toy fixture")
    for name in ("run", "record", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("--file", default=None)
        sp.add_argument("--spec", default=None, help="spec folder relative to the repo root")
        sp.add_argument("--cassette", default=None)
        if name == "run":
            sp.add_argument("--write-golden", default=None)
        if name == "record":
            sp.add_argument("--out", required=True)
        if name == "check":
            sp.add_argument("--golden", default=str(DEFAULT_GOLDEN))

    args = p.parse_args(argv)

    if args.cmd == "demo":
        sid, tasks, spawn = _build(str(DEFAULT_FIXTURE), None, None)
        sys.stdout.write(simulate_text(sid, tasks, spawn))
        return 0

    if args.cmd == "run":
        sid, tasks, spawn = _build(args.file, args.spec, args.cassette)
        text = simulate_text(sid, tasks, spawn)
        sys.stdout.write(text)
        if args.write_golden:
            Path(args.write_golden).write_text(text)
            sys.stderr.write(f"\n[golden written: {args.write_golden}]\n")
        return 0

    if args.cmd == "record":
        sid, tasks, spawn = _build(args.file, args.spec, args.cassette)
        entries: List = []
        simulate_text(sid, tasks, spawn, record=entries)
        Path(args.out).write_text(
            json.dumps({"spec_id": sid, "entries": entries}, indent=2, sort_keys=True) + "\n"
        )
        sys.stderr.write(f"[cassette written: {args.out} ({len(entries)} entries)]\n")
        return 0

    if args.cmd == "check":
        sid, tasks, spawn = _build(args.file, args.spec, args.cassette)
        text = simulate_text(sid, tasks, spawn)
        golden = Path(args.golden)
        if not golden.exists():
            sys.stderr.write(f"golden missing: {golden}\n")
            return 2
        expected = golden.read_text()
        if text == expected:
            print(f"GOLDEN OK ({golden.name}): orchestrator output unchanged.")
            return 0
        sys.stdout.writelines(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                text.splitlines(keepends=True),
                fromfile="golden",
                tofile="current",
            )
        )
        print("\nGOLDEN DRIFT: output changed (see diff above).")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
