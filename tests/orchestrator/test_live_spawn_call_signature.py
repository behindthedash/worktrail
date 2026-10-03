"""Regression for handoff 20261001-190740: two `live.py` spawn call sites
passed kwargs `spawn_agent` does not accept, so both raised `TypeError` on any
real invocation.

`run_research_session` (the `--fork-research` pre-load) passed
`agent=`/`model=`/`effort=` and omitted the required `tier=`;
`smoke()` (the `worktrail-live smoke` connectivity probe) passed
`agent=`/`model=` and omitted `tier=`. Neither site carries a task/role
context, so both resolve through `tier`/`prefer` -- the routing file's
`default_tier` row with the requested harness as a target preference
(`live._default_tier_and_prefer`) -- not through kwargs. The explicit-cell
pinning (`spawnlib.explicit_cell_override()` + `tier="explicit"`) these tests
first asserted for the two sites was superseded by the model-tier-routing D3
decision (openspec/changes/model-tier-routing-zero-usage-spawn-detection/
design.md): `model`/`effort` stay compatibility-only here, the same contract
`LiveSpawn.__init__` documents for its own `model`.

Both defects survived because their tests patched `spawn_agent` with an
unconstrained `MagicMock` (`side_effect=lambda *_, **kw:`), which accepts any
signature; `smoke()` had no test at all. This module closes both halves: the
behavioral tests bind each captured call against the real signature, and the
AST audit keeps every future call site honest.
"""

from __future__ import annotations

import ast
import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live, spawnlib

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "worktrail"
SPAWN_NAMES = {"spawn_agent", "spawn_claude_p"}


class SpawnCallSignatureConformanceTests(unittest.TestCase):
    """Every spawn call site in `src/worktrail/` binds against the real
    signature -- no unexpected keyword, no missing required one."""

    def _call_sites(self):
        for path in sorted(SRC_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else getattr(func, "id", None)
                )
                if name in SPAWN_NAMES:
                    yield path, name, node

    def test_no_unexpected_kwargs_and_no_missing_required(self):
        violations: list[str] = []
        checked = 0
        for path, name, node in self._call_sites():
            signature = inspect.signature(getattr(spawnlib, name))
            params = signature.parameters
            accepts_kwargs = any(
                p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
            site = f"{path.relative_to(SRC_ROOT)}:{node.lineno}"

            named = [kw for kw in node.keywords if kw.arg is not None]
            unpacks = [kw for kw in node.keywords if kw.arg is None]

            for kw in named:
                if kw.arg not in params:
                    violations.append(f"{site} {name}(): unexpected kwarg {kw.arg!r}")

            # A `**mapping` unpack can supply anything, so the required-argument
            # check is only meaningful when every keyword is spelled out.
            if unpacks or accepts_kwargs:
                checked += 1
                continue
            # Only keyword-only params are checked for presence: every argument
            # either spawn entry point requires is keyword-only, so a positional
            # argument can never satisfy one and `node.args` carries no names.
            supplied = {kw.arg for kw in named}
            for pname, p in params.items():
                required = (
                    p.kind is inspect.Parameter.KEYWORD_ONLY
                    and p.default is inspect.Parameter.empty
                )
                if required and pname not in supplied:
                    violations.append(
                        f"{site} {name}(): missing required kwarg {pname!r}"
                    )
            checked += 1

        self.assertGreater(checked, 0, "sanity: the audit must find spawn call sites")
        self.assertEqual(violations, [], "\n".join(violations))


class SmokeSpawnCallTests(unittest.TestCase):
    """`smoke()` is the operator-facing probe; the spawn must express the
    requested harness as its target preference, so it can never silently serve
    another harness's cell."""

    def _capture(self, harness, model):
        captured = {}

        def _capture(*args, **kwargs):
            # autospec binds against the real signature before side_effect runs,
            # so a bad kwarg raises TypeError here exactly as it would in
            # production.
            captured["args"] = args
            captured["kwargs"] = kwargs
            return type(
                "R",
                (),
                {"text": "PONG", "session_id": None, "exhausted": False},
            )()

        with patch(
            "worktrail.orchestrator.live.spawnlib.spawn_agent",
            side_effect=_capture,
            autospec=True,
        ):
            ok = live.smoke(harness, model)
        return ok, captured

    def test_smoke_reaches_the_spawn_with_the_real_signature(self):
        ok, captured = self._capture("claude", "opus")
        self.assertTrue(ok)
        inspect.signature(spawnlib.spawn_agent).bind(
            *captured["args"], **captured["kwargs"]
        )

    def test_smoke_prefers_the_requested_harness_target(self):
        # `model="opus"` is compatibility-only (not fed to the spawn, see
        # test_research_session_ignores_model_and_effort_for_dispatch): the
        # dispatch contract is the default_tier row plus claude's own target.
        _, captured = self._capture("claude", "opus")
        self.assertEqual(captured["kwargs"].get("tier"), "t2-build")
        self.assertEqual(captured["kwargs"].get("prefer"), "claude-sub")
        self.assertNotIn("model", captured["kwargs"])

    def test_smoke_prefers_a_non_claude_harness_target(self):
        ok, captured = self._capture("codex", "gpt-5.4-mini")
        self.assertTrue(ok)
        self.assertEqual(captured["kwargs"].get("prefer"), "codex-sub")


class ResearchSessionSpawnCallTests(unittest.TestCase):
    """`run_research_session()` backs `--fork-research`; its pre-load spawn
    must use the same signature and the same (`tier`, `prefer`) resolution."""

    def _run(self, **kwargs):
        captured = {}

        def _capture(*args, **kw):
            captured["args"] = args
            captured["kwargs"] = kw
            return type("R", (), {"text": "", "session_id": "sid-1"})()

        with tempfile.TemporaryDirectory() as t:
            spec_folder = Path(t) / "docs" / "specs" / "001-spec"
            spec_folder.mkdir(parents=True)
            with patch(
                "worktrail.orchestrator.live.spawnlib.spawn_agent",
                side_effect=_capture,
                autospec=True,
            ):
                sid = live.run_research_session(spec_folder, **kwargs)
        return sid, captured

    def test_research_session_reaches_the_spawn_with_the_real_signature(self):
        sid, captured = self._run(agent="claude", model="opus", effort="high")
        self.assertEqual(sid, "sid-1")
        inspect.signature(spawnlib.spawn_agent).bind(
            *captured["args"], **captured["kwargs"]
        )

    def test_research_session_prefers_the_requested_harness_target(self):
        _, captured = self._run(agent="claude", model="opus", effort="high")
        self.assertEqual(captured["kwargs"].get("tier"), "t2-build")
        self.assertEqual(captured["kwargs"].get("prefer"), "claude-sub")

    def test_research_session_ignores_model_and_effort_for_dispatch(self):
        # A run-level model/effort is compatibility-only on these surfaces:
        # neither may reach the spawn, only the harness's target preference.
        _, captured = self._run(agent="codex", model="gpt-5.4-mini")
        self.assertEqual(captured["kwargs"].get("prefer"), "codex-sub")
        self.assertNotIn("model", captured["kwargs"])
        self.assertNotIn("effort", captured["kwargs"])
