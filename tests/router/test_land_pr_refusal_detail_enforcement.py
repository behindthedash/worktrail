#!/usr/bin/env python3
"""Regression tests closing the "refusal with an empty detail" recurring failure mode.

Three instances of one shape, each found by live incident rather than by any
guard: `_run_preflight_and_labels` (PR #1402) and `_commit_pending` (PR #1388)
both returned refusals with a `None` detail while every sibling refusal path
populated it, and `_run_record_main` dropped the captured stdout whenever
`run_record` exited via a bare int `SystemExit` (PR #1429). The tracked OpenSpec
change `land-pr-refusal-diagnostics` (PR #1411/#1412) fixed the first two
instances and their own tests; nothing structurally stopped a fourth refusal
site from shipping the same way (brief 20261003-192532).

This module closes the discovery half, in the AST-enforcement posture
`test_pr_creation_callsite_enforcement_coverage.py` established:

1. `refused_construction_sites()` AST-walks `land_pr.py` for every
   `LandOutcome(outcome="refused", ...)` construction -- no hand-maintained grep
   -- and `test_every_refused_site_carries_a_detail` asserts each one passes a
   `detail=` that is statically non-empty, unless its line appears in
   `DETAIL_ALLOWLIST` (empty today: no site needs an exception, and an entry
   must carry a written reason). `computed_outcome_sites()` closes the walk's
   own blind spot: a site that computes its `outcome` would be invisible to it.
2. `capture_wrappers()` AST-walks every `router/*.py` module for a function
   that redirects BOTH stdout and stderr around an in-process `main()` call, and
   `test_capture_wrappers_return_a_stream_symmetric_detail` asserts the returned
   detail is the three-fallback chain `detail or <stderr> or <stdout>` with
   `detail` assigned from a string `SystemExit` code -- exactly the shape PR
   #1429 fixed by hand in both of land_pr.py's wrappers. A wrapper that keeps a
   non-empty detail while dropping one stream is indistinguishable from that
   defect, which is why the refused-outcome guard alone cannot cover it.
3. `test_main_reports_a_refused_outcome_on_stderr` covers the operator-facing
   half (brief 20261003-204324): `land-pr --json` keeps stdout machine-only and
   writes one `REFUSED at <step>: <detail>` line to stderr.
"""

from __future__ import annotations

import ast
import json
import textwrap
import unittest
from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

from worktrail.router import land_pr

LAND_PR_SRC = Path(land_pr.__file__).resolve()
ROUTER_SRC = LAND_PR_SRC.parent

# Refused construction sites excused from the non-empty-detail rule, keyed by
# line number in land_pr.py and carrying the reason the site is exempt.
# Deliberately brittle: when the site moves, the entry stops matching a refused
# site and `test_allowlist_entries_still_name_refused_sites` fails, forcing a
# re-confirmation instead of letting the exception drift onto unrelated code.
DETAIL_ALLOWLIST: Mapping[int, str] = {}


def _outcome_keyword(node: ast.Call) -> ast.expr | None:
    for kw in node.keywords:
        if kw.arg == "outcome":
            return kw.value
    return None


def _land_outcome_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "LandOutcome"
    ]


def refused_construction_sites(tree: ast.AST) -> list[dict[str, Any]]:
    """Every `LandOutcome(outcome="refused", ...)` construction in `tree`."""
    sites: list[dict[str, Any]] = []
    for node in _land_outcome_calls(tree):
        outcome = _outcome_keyword(node)
        if not (isinstance(outcome, ast.Constant) and outcome.value == "refused"):
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        step = keywords.get("refused_step")
        sites.append(
            {
                "lineno": node.lineno,
                "step": ast.unparse(step) if step is not None else "<none>",
                "detail": keywords.get("detail"),
            }
        )
    return sites


def detail_violations(
    tree: ast.AST, allowlist: Mapping[int, str] | None = None
) -> list[str]:
    """Refused sites whose `detail=` is missing or a statically-empty constant."""
    allowed = set(allowlist or {})
    problems: list[str] = []
    for site in refused_construction_sites(tree):
        if site["lineno"] in allowed:
            continue
        detail = site["detail"]
        if detail is None:
            problems.append(
                f"line {site['lineno']} (step {site['step']}): no detail= keyword"
            )
        elif isinstance(detail, ast.Constant) and not (
            isinstance(detail.value, str) and detail.value.strip()
        ):
            problems.append(
                f"line {site['lineno']} (step {site['step']}): detail={detail.value!r}"
            )
    return problems


def computed_outcome_sites(tree: ast.AST) -> list[str]:
    """`LandOutcome` calls whose `outcome=` is not a string literal.

    `refused_construction_sites()` can only see literal outcomes, so a computed
    one would take a refusal out of the walk's coverage entirely.
    """
    problems: list[str] = []
    for node in _land_outcome_calls(tree):
        outcome = _outcome_keyword(node)
        if not (isinstance(outcome, ast.Constant) and isinstance(outcome.value, str)):
            rendered = ast.unparse(outcome) if outcome is not None else "<missing>"
            problems.append(f"line {node.lineno}: outcome={rendered}")
    return problems


def _called_name(call: ast.AST) -> str | None:
    """The bare function name of `call` -- `f(...)` and `mod.f(...)` alike."""
    if not isinstance(call, ast.Call):
        return None
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _getvalue_source(node: ast.AST) -> str | None:
    """The `Name` whose `.getvalue()` produced `node` (through `.strip()`), if any."""
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "getvalue" and isinstance(node.func.value, ast.Name):
            return node.func.value.id
        node = node.func.value
    return None


def capture_wrappers(tree: ast.AST) -> list[dict[str, Any]]:
    """Functions redirecting stdout AND stderr around an in-process `main()` call."""
    wrappers: list[dict[str, Any]] = []
    for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        stdout_name: str | None = None
        stderr_name: str | None = None
        wraps_main = False
        for node in ast.walk(func):
            if isinstance(node, ast.With):
                for item in node.items:
                    context = item.context_expr
                    name = _called_name(context)
                    args = getattr(context, "args", [])
                    if not args or not isinstance(args[0], ast.Name):
                        continue
                    if name == "redirect_stdout":
                        stdout_name = args[0].id
                    elif name == "redirect_stderr":
                        stderr_name = args[0].id
            if isinstance(node, ast.Call) and _called_name(node) == "main":
                wraps_main = True
        if stdout_name and stderr_name and wraps_main:
            wrappers.append(
                {
                    "name": func.name,
                    "lineno": func.lineno,
                    "stdout": stdout_name,
                    "stderr": stderr_name,
                    "func": func,
                }
            )
    return wrappers


def _assigns_string_systemexit_detail(func: ast.FunctionDef) -> bool:
    """True when a `SystemExit` handler assigns `detail = str(exc.code)`."""
    for node in ast.walk(func):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            exc_type = handler.type
            if not (isinstance(exc_type, ast.Name) and exc_type.id == "SystemExit"):
                continue
            for stmt in ast.walk(handler):
                if (
                    isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    and stmt.targets[0].id == "detail"
                    and _called_name(stmt.value) == "str"
                ):
                    return True
    return False


def capture_wrapper_violations(wrapper: dict[str, Any]) -> list[str]:
    """Everything wrong with one capture wrapper's returned detail expression."""
    name = wrapper["name"]
    function = wrapper["func"]
    problems: list[str] = []
    returns = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Return) and node.value is not None
    ]
    if not returns:
        problems.append(f"{name}: no return statement")
    for ret in returns:
        elements = (
            list(ret.value.elts) if isinstance(ret.value, ast.Tuple) else [ret.value]
        )
        chain = next(
            (
                element
                for element in elements
                if isinstance(element, ast.BoolOp) and isinstance(element.op, ast.Or)
            ),
            None,
        )
        if chain is None:
            problems.append(
                f"{name} line {ret.lineno}: returned detail is not a fallback chain"
            )
            continue
        values = list(chain.values)
        if len(values) != 3:
            problems.append(
                f"{name} line {ret.lineno}: fallback chain has {len(values)} value(s), "
                "expected the three-way detail -> stderr -> stdout shape"
            )
            continue
        first, second, third = values
        if not (isinstance(first, ast.Name) and first.id == "detail"):
            problems.append(
                f"{name} line {ret.lineno}: fallback chain does not start from `detail`"
            )
        if _getvalue_source(second) != wrapper["stderr"]:
            problems.append(
                f"{name} line {ret.lineno}: second fallback is not the captured stderr "
                f"({wrapper['stderr']!r})"
            )
        if _getvalue_source(third) != wrapper["stdout"]:
            problems.append(
                f"{name} line {ret.lineno}: third fallback is not the captured stdout "
                f"({wrapper['stdout']!r})"
            )
    if not _assigns_string_systemexit_detail(function):
        problems.append(
            f"{name}: no `detail = str(exc.code)` in a SystemExit handler -- a string "
            "SystemExit code reaches the caller as no detail at all"
        )
    return problems


class RefusedDetailEnforcementTests(unittest.TestCase):
    def _tree(self) -> ast.AST:
        return ast.parse(LAND_PR_SRC.read_text(encoding="utf-8"))

    def test_every_refused_site_carries_a_detail(self):
        tree = self._tree()
        sites = refused_construction_sites(tree)
        self.assertTrue(
            sites,
            "the AST walk found no refused construction sites in land_pr.py -- the "
            "detection itself is broken, not the code",
        )
        self.assertEqual(detail_violations(tree, DETAIL_ALLOWLIST), [])

    def test_every_outcome_is_spelled_out(self):
        self.assertEqual(computed_outcome_sites(self._tree()), [])

    def test_allowlist_entries_still_name_refused_sites(self):
        lines = {site["lineno"] for site in refused_construction_sites(self._tree())}
        stale = sorted(set(DETAIL_ALLOWLIST) - lines)
        self.assertEqual(
            stale,
            [],
            f"DETAIL_ALLOWLIST entries no longer name a refused site: {stale} -- "
            "re-confirm the exemption and drop them",
        )

    def test_allowlist_entries_carry_a_reason(self):
        for line, reason in DETAIL_ALLOWLIST.items():
            self.assertTrue(reason.strip(), f"DETAIL_ALLOWLIST[{line}] has no reason")

    def test_detector_flags_planted_violations(self):
        """The detector itself is proven to fail on the shape it guards."""
        missing = ast.parse(
            "def f():\n"
            '    return LandOutcome(outcome="refused", refused_step="dirty_tree")\n'
        )
        self.assertEqual(len(detail_violations(missing, {})), 1)

        empty = ast.parse(
            'x = LandOutcome(outcome="refused", refused_step="push", detail="")\n'
        )
        self.assertEqual(len(detail_violations(empty, {})), 1)

        excused = ast.parse(
            'def f():\n    return LandOutcome(outcome="refused", refused_step="push")\n'
        )
        self.assertEqual(detail_violations(excused, {2: "reviewed: reason"}), [])

        computed = ast.parse(
            'x = LandOutcome(outcome=candidate, refused_step="push")\n'
        )
        self.assertEqual(len(computed_outcome_sites(computed)), 1)


class CaptureWrapperSymmetryTests(unittest.TestCase):
    def test_capture_wrappers_return_a_stream_symmetric_detail(self):
        found: list[str] = []
        problems: list[str] = []
        for path in sorted(ROUTER_SRC.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for wrapper in capture_wrappers(tree):
                found.append(f"{path.name}:{wrapper['name']}")
                problems += [
                    f"{path.name}:{problem}"
                    for problem in capture_wrapper_violations(wrapper)
                ]
        self.assertEqual(
            sorted(found),
            ["land_pr.py:_preflight_main", "land_pr.py:_run_record_main"],
            "the set of in-process capture wrappers changed -- audit the new wrapper "
            "against the three-fallback shape and register it here",
        )
        self.assertEqual(problems, [])

    def test_detector_flags_a_wrapper_that_drops_the_stderr_fallback(self):
        planted = ast.parse(
            textwrap.dedent(
                """
                def _wrapper_main(argv):
                    out = io.StringIO()
                    err = io.StringIO()
                    detail = ""
                    try:
                        with redirect_stdout(out), redirect_stderr(err):
                            exit_code = preflight.main(argv)
                    except SystemExit as exc:
                        if isinstance(exc.code, int):
                            exit_code = exc.code
                        elif exc.code:
                            exit_code = 1
                            detail = str(exc.code)
                        else:
                            exit_code = 0
                    return exit_code, detail or out.getvalue().strip()
                """
            )
        )
        wrappers = capture_wrappers(planted)
        self.assertEqual(len(wrappers), 1)
        problems = capture_wrapper_violations(wrappers[0])
        self.assertTrue(problems, "a stream-dropping wrapper passed the symmetry check")


class RefusalLineTests(unittest.TestCase):
    def _main(self, argv: list[str]) -> tuple[int, str, str]:
        out = StringIO()
        err = StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = land_pr.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_main_reports_a_refused_outcome_on_stderr(self):
        """An out-of-range route refuses before any git call -- the cheapest refusal."""
        code, stdout, stderr = self._main(
            [
                "--repo",
                "/nonexistent/repo",
                "--base",
                "main",
                "--title",
                "t",
                "--summary",
                "s",
                "--route",
                "Z",
                "--json",
            ]
        )
        self.assertEqual(code, 2)
        lines = stderr.strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("REFUSED at route: "), lines[0])
        self.assertIn("route 'Z' is not one of", lines[0])
        # stdout stays the machine contract: the JSON payload, unchanged.
        self.assertEqual(json.loads(stdout)["outcome"], "refused")

    def test_refusal_line_takes_the_first_non_blank_detail_line(self):
        outcome = land_pr.LandOutcome(
            outcome="refused",
            refused_step="preflight",
            detail="\n  gate denied: forbidden path\nmore detail\n",
        )
        self.assertEqual(
            land_pr._refusal_line(outcome),
            "REFUSED at preflight: gate denied: forbidden path",
        )

    def test_refusal_line_truncates_a_long_detail(self):
        outcome = land_pr.LandOutcome(
            outcome="refused", refused_step="push", detail="x" * 1000
        )
        line = land_pr._refusal_line(outcome)
        self.assertTrue(line.endswith("..."), line)
        self.assertLessEqual(len(line), len("REFUSED at push: ") + 203)

    def test_refusal_line_tolerates_an_empty_detail(self):
        outcome = land_pr.LandOutcome(
            outcome="refused", refused_step="push", detail=None
        )
        self.assertEqual(land_pr._refusal_line(outcome), "REFUSED at push")


if __name__ == "__main__":
    unittest.main(verbosity=2)
