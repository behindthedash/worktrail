"""Audit: every `spawn_agent`/`spawn_claude_p` call site in `src/worktrail/`
either handles exhaustion or declares an exemption here.

`spawn_agent` and `spawn_claude_p` return a `SpawnResult` whose `.text` is the
provider's error stream when `.exhausted` is set (design D2). A call site that
reads that payload without checking turns a capacity block into a bogus answer.
Task 1.1 added `spawnlib.raise_if_exhausted()` as the shared fail-closed
boundary; tasks 2.1-2.3 adopted it in the three deciding callers. This test
keeps the audit closed: a new spawn call site that ignores exhaustion fails the
build unless it is listed in `EXEMPT` with a written rationale.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "worktrail"

SPAWN_NAMES = {"spawn_agent", "spawn_claude_p"}

# `<module>:<qualname>` -> why this site needs no exhaustion check.
EXEMPT: dict[str, str] = {
    # Design D6 -- the two call sites justified as reading no model payload.
    "orchestrator/live.py:run_research_session": (
        "The research pre-load consumes only `result.session_id`, never "
        "`.text`; an exhausted spawn returns no session id and the function "
        "already warns that --fork-research has no effect."
    ),
    "orchestrator/live.py:smoke": (
        "`smoke()` is the operator-facing connectivity probe: it prints "
        "whatever came back and reports UNEXPECTED, which is the correct "
        "report for a capacity block too."
    ),
    # Not model payload consumers either.
    "orchestrator/spawnlib.py:spawn_claude_p": (
        "The defining module's own delegation: `spawn_claude_p` is a thin "
        "wrapper that returns `spawn_agent`'s `SpawnResult` unchanged, so the "
        "exhaustion flag is preserved for its caller to handle."
    ),
    "workqueue/queue_triage.py:_apply_propose_change.prepare": (
        "The propose-change spawn's result is discarded entirely -- nothing "
        "reads `.text` or any other field. An exhausted spawn writes no "
        "proposal.md/tasks.md, so the existing file-existence check below it "
        "already fails the step with its own message."
    ),
}


class _SpawnCallSite:
    def __init__(self, module: str, qualname: str, lineno: int, handled: bool):
        self.module = module
        self.qualname = qualname
        self.lineno = lineno
        self.handled = handled

    @property
    def key(self) -> str:
        return f"{self.module}:{self.qualname}"


def _walk(node: ast.AST, stack: list, module: str, sites: list) -> None:
    scoped = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    if scoped:
        stack.append(node)
    if isinstance(node, ast.Call):
        func = node.func
        name = (
            func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        )
        if name in SPAWN_NAMES and stack:
            enclosing = "\n".join(ast.unparse(s) for s in stack)
            sites.append(
                _SpawnCallSite(
                    module=module,
                    qualname=".".join(s.name for s in stack),
                    lineno=node.lineno,
                    handled=(
                        "exhausted" in enclosing or "raise_if_exhausted" in enclosing
                    ),
                )
            )
    for child in ast.iter_child_nodes(node):
        _walk(child, stack, module, sites)
    if scoped:
        stack.pop()


def _collect_call_sites() -> list[_SpawnCallSite]:
    sites: list[_SpawnCallSite] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        module = path.relative_to(SRC_ROOT).as_posix()
        _walk(ast.parse(path.read_text(encoding="utf-8")), [], module, sites)
    return sites


def test_spawn_call_sites_exist_to_audit():
    """The walk actually finds the known callers (guards against a silent
    no-op audit if the collector or the source layout changes)."""
    sites = _collect_call_sites()
    assert len(sites) >= 10
    keys = {s.key for s in sites}
    assert "conductor/compile.py:_default_spawn" in keys
    assert "orchestrator/live.py:LiveSpawn.__call__" in keys
    assert "orchestrator/verify.py:_make_live_spawn.spawn" in keys


def test_every_spawn_call_site_handles_or_declares_exhaustion():
    offenders = [
        f"{s.module}:{s.lineno} (in {s.qualname})"
        for s in _collect_call_sites()
        if not s.handled and s.key not in EXEMPT
    ]
    assert not offenders, (
        "spawn call site(s) neither check `exhausted`/`raise_if_exhausted` nor "
        "appear in EXEMPT in this test:\n  "
        + "\n  ".join(offenders)
        + "\n\nWrap the spawn in `spawnlib.raise_if_exhausted(..., context=...)` "
        "before reading `.text`, or add the `<module>:<qualname>` site to "
        "EXEMPT with a rationale."
    )


def test_every_exemption_still_resolves_to_a_real_call_site():
    keys = {s.key for s in _collect_call_sites()}
    stale = sorted(key for key in EXEMPT if key not in keys)
    assert not stale, (
        "EXEMPT names spawn call site(s) that no longer exist (renamed or "
        "removed); drop the entry:\n  " + "\n  ".join(stale)
    )


def test_every_exemption_carries_a_rationale():
    empty = sorted(key for key, why in EXEMPT.items() if not why.strip())
    assert not empty, f"EXEMPT entries with no rationale: {empty}"


def test_exempt_sites_are_not_silently_handled():
    """An exempt site that grew a real exhaustion check should lose its
    exemption rather than keep a misleading rationale."""
    handled = sorted(
        s.key for s in _collect_call_sites() if s.handled and s.key in EXEMPT
    )
    assert not handled, (
        "these sites now handle exhaustion; remove them from EXEMPT:\n  "
        + "\n  ".join(handled)
    )
