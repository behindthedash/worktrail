"""The compiled RunPlan: per-task file scope and dependency edges, cached by content.

Why this exists
---------------
`coordinator.runnable_frontier()` decides what may run concurrently from two
per-task fields: `deps` and `files`. The devkit format carried both in task
frontmatter. OpenSpec's `tasks.md` carries neither (design `conductor-lanes.md`
§2), so `OpenSpecTaskSource` declares no file scope at all and falls back to
"every task in a section depends on the one before it" -- correct, but it
serialises work that could run in parallel.

The RunPlan is where those fields come from once the authoring format stops
supplying them. It is compiled **once** per content-distinct version of a change
(`compile.py`), cached, and never committed into the change directory: it is
derived run state, not an authoring artifact.

The safety rule
---------------
A RunPlan is partly LLM-inferred, so applying it verbatim would let a bad
inference make a run *less* safe. `apply_to_tasks()` enforces one invariant:

    A dependency edge may only be dropped if BOTH endpoints carry a
    non-empty file scope.

That is not a heuristic -- it falls directly out of `runnable_frontier`, which
treats an empty file set as "collides with nothing". Two tasks with declared,
disjoint scopes are exactly the case it is built to run in parallel; a task with
no scope is invisible to the collision check, so decoupling it from its
predecessor would let it race with unbounded blast radius. Dropping an edge is
therefore something the plan has to *pay for* with file scope, and a plan that
supplies no scope reduces to the conservative baseline it started from.

Everything else the plan touches is either additive (`complexity`, `review`,
fill-only) or sticky in the safe direction (`kind`: a task the artifact declared
as a tail stays a tail, so the plan can never un-hold-out an e2e task).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from worktrail.orchestrator.coordinator import TAIL_KINDS, compute_levels

# Bump when the meaning of a serialised plan changes. It is folded into the
# fingerprint, so a bump invalidates every cached plan rather than silently
# reading an old one under new semantics.
PLAN_VERSION = 1

SOURCE_SEED = "seed"  # the format already declared scope for every task; no LLM ran
SOURCE_COMPILED = "compiled"  # one LLM pass inferred the missing scope
SOURCE_BASELINE = "baseline"  # scope was missing and compiling was not permitted


@dataclass(frozen=True)
class TaskPlan:
    """Planning fields for one task. Immutable so a cached plan cannot be edited
    in place by a consumer and then written back under the same fingerprint."""

    id: str
    files: Tuple[str, ...] = ()
    deps: Tuple[str, ...] = ()
    kind: str = ""
    complexity: str = ""
    review: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TaskPlan":
        return cls(
            id=str(d["id"]),
            files=tuple(_norm_str_list(d.get("files"))),
            deps=tuple(_norm_str_list(d.get("deps"))),
            kind=str(d.get("kind") or ""),
            complexity=str(d.get("complexity") or ""),
            review=str(d.get("review") or ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "files": list(self.files),
            "deps": list(self.deps),
            "kind": self.kind,
            "complexity": self.complexity,
            "review": self.review,
        }


@dataclass(frozen=True)
class RunPlan:
    spec_id: str
    fingerprint: str
    source: str
    tasks: Tuple[TaskPlan, ...] = ()
    notes: Tuple[str, ...] = ()
    plan_version: int = PLAN_VERSION

    def by_id(self) -> Dict[str, TaskPlan]:
        return {tp.id: tp for tp in self.tasks}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "fingerprint": self.fingerprint,
            "source": self.source,
            "plan_version": self.plan_version,
            "notes": list(self.notes),
            "tasks": [tp.to_dict() for tp in self.tasks],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RunPlan":
        return cls(
            spec_id=str(d.get("spec_id", "")),
            fingerprint=str(d.get("fingerprint", "")),
            source=str(d.get("source", SOURCE_COMPILED)),
            tasks=tuple(TaskPlan.from_dict(t) for t in d.get("tasks") or []),
            notes=tuple(str(n) for n in d.get("notes") or []),
            plan_version=int(d.get("plan_version", 0)),
        )


def _norm_str_list(v: Any) -> List[str]:
    if not v:
        return []
    if isinstance(v, str):
        v = [v]
    return sorted({str(x).strip() for x in v if str(x).strip()})


# --------------------------------------------------------------------------- #
# Fingerprinting
# --------------------------------------------------------------------------- #
def fingerprint(spec_dir: "str | Path", tasks: Sequence[Dict[str, Any]]) -> str:
    """Content hash of everything a compile pass would read, minus run status.

    Status is deliberately excluded. It changes on every completed task, and a
    fingerprint that moved with it would miss the cache on exactly the case the
    cache exists for -- resuming a partially-finished run.

    Two inputs are folded together:

    1. The loaded tasks, reduced to their planning-relevant fields. This covers
       the task artifact's content without hashing its bytes (which carry
       status), and it is format-agnostic: whatever the adapter parsed is what
       compile will see.
    2. Every other file in the change directory (`proposal.md`, `design.md`,
       `specs/**`) by content. Task-bearing files are skipped by basename --
       over-skipping is safe here (the task tuples already capture their
       meaning), while under-skipping would only cost a cache miss.
    """
    spec_dir = Path(spec_dir)
    h = hashlib.sha256()
    h.update(f"worktrail-runplan/v{PLAN_VERSION}\n".encode())

    for t in sorted(tasks, key=lambda x: str(x.get("id", ""))):
        h.update(
            "\x1f".join(
                [
                    str(t.get("id") or ""),
                    str(t.get("title") or ""),
                    str(t.get("kind") or ""),
                    ",".join(_norm_str_list(t.get("deps"))),
                    ",".join(_norm_str_list(t.get("files"))),
                ]
            ).encode()
            + b"\x1e"
        )

    task_basenames = {Path(str(t.get("path") or "")).name for t in tasks} - {""}
    if spec_dir.is_dir():
        for f in sorted(p for p in spec_dir.rglob("*") if p.is_file()):
            if f.name in task_basenames:
                continue
            h.update(str(f.relative_to(spec_dir)).encode() + b"\x1f")
            h.update(hashlib.sha256(f.read_bytes()).hexdigest().encode() + b"\x1e")
    return h.hexdigest()


def cache_path(cache_dir: "str | Path", spec_id: str, fp: str) -> Path:
    """One file per (change, content version). Old versions are left in place:
    they are small, and keeping them means flipping a change back to a previous
    state re-hits the cache instead of paying for another compile."""
    safe = "".join(c if c.isalnum() or c in "-._" else "-" for c in spec_id) or "spec"
    return Path(cache_dir) / f"{safe}-{fp[:12]}.json"


def load_cached(cache_dir: "str | Path", spec_id: str, fp: str) -> Optional[RunPlan]:
    """Return the cached plan for this exact content version, or None.

    A plan whose recorded fingerprint or `plan_version` disagrees with what we
    asked for is treated as absent rather than repaired -- it means the file was
    hand-edited or written by an older worktrail, and recompiling is cheaper
    than reasoning about what it meant.
    """
    p = cache_path(cache_dir, spec_id, fp)
    try:
        plan = RunPlan.from_dict(json.loads(p.read_text()))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if plan.fingerprint != fp or plan.plan_version != PLAN_VERSION:
        return None
    return plan


def store(cache_dir: "str | Path", plan: RunPlan) -> Path:
    p = cache_path(cache_dir, plan.spec_id, plan.fingerprint)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n")
    tmp.replace(p)  # atomic: a concurrent reader never sees a half-written plan
    return p


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #
def apply_to_tasks(
    tasks: Sequence[Dict[str, Any]], plan: Optional[RunPlan]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Merge *plan* onto freshly-loaded *tasks*, enforcing the module invariant.

    Returns `(tasks, notes)`. `tasks` are copies -- the caller's list is never
    mutated, so a rejected plan leaves the run exactly as it would have been.
    `notes` is human-readable and belongs in the run journal; it is the only
    record of a plan having been distrusted, so it is never silently dropped.

    Rejection is whole-plan, not per-task. A plan whose task set has drifted
    from the artifact, or whose merged edges form a cycle, is not partially
    salvageable: the file scopes it supplies were inferred alongside the edges
    that turned out to be wrong, and trusting half of that is worse than
    trusting none of it.
    """
    out = [dict(t) for t in tasks]
    if plan is None:
        return out, []

    notes: List[str] = list(plan.notes)
    ids = {t["id"] for t in out}
    planned = plan.by_id()

    missing = sorted(ids - set(planned))
    extra = sorted(set(planned) - ids)
    if missing or extra:
        notes.append(
            "run plan rejected: task set drifted from the artifact "
            f"(missing={missing or '-'}, unknown={extra or '-'}); "
            "using the format's own deps and file scope"
        )
        return [dict(t) for t in tasks], notes

    files = {tid: set(tp.files) for tid, tp in planned.items()}

    merged: List[Dict[str, Any]] = []
    for t in out:
        tp = planned[t["id"]]
        baseline_deps = set(_norm_str_list(t.get("deps")))

        # Unknown ids in a planned edge are dropped, not kept: `runnable_frontier`
        # waits for every dep to be done, so an invented dep id would park the
        # task forever instead of failing loudly.
        keep = {d for d in tp.deps if d in ids}

        # The invariant. An edge the plan wants to drop is put back unless both
        # ends declare file scope for the collision check to work with.
        restored = {
            d
            for d in baseline_deps - keep
            if d in ids and (not files[t["id"]] or not files.get(d))
        }
        deps = sorted(keep | restored)

        # A tail is sticky: the artifact said this task verifies or tidies up
        # after the others, and no inference may demote it back into the fan-out.
        kind = t.get("kind") or ""
        if kind not in TAIL_KINDS and tp.kind:
            kind = tp.kind

        m = dict(t)
        m["deps"] = deps
        m["files"] = sorted(files[t["id"]])
        if kind:
            m["kind"] = kind
        for field in ("complexity", "review"):
            if not m.get(field) and getattr(tp, field):
                m[field] = getattr(tp, field)
        merged.append(m)

    # `keep` and `restored` are each acyclic on their own, but their union need
    # not be: the plan may assert a->b where the baseline asserted b->a.
    try:
        compute_levels(merged)
    except (ValueError, RecursionError) as exc:
        notes.append(
            f"run plan rejected: merging its edges with the format's own creates a "
            f"cycle ({exc}); using the format's deps and file scope"
        )
        return [dict(t) for t in tasks], notes

    loosened = sum(
        1
        for orig, m in zip(out, merged)
        if set(_norm_str_list(orig.get("deps"))) - set(m["deps"])
    )
    scoped = sum(1 for m in merged if m["files"])
    notes.append(
        f"run plan applied ({plan.source}, {plan.fingerprint[:12]}): "
        f"{scoped}/{len(merged)} tasks scoped, {loosened} loosened"
    )
    return merged, notes
