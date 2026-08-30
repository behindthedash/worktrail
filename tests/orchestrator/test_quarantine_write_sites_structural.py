#!/usr/bin/env python3
"""Structural guard for the failure class behind brief
20260807-214511-worktrail-serial-path-and-pipeline: two independent bugs
(PR #221, #227) where a QUARANTINED-state journal write forgot to persist
`state` and then `quarantine_reason`, because every call site hand-builds its
own write instead of going through one shared helper.

`test_quarantine_journal_persistence.py` proves the two known-fixed call
sites behave correctly today. It cannot catch a *third*, not-yet-written call
site that makes the same mistake tomorrow. This test statically enumerates
every call to the journal-write primitives (`_write_group_journal`,
`_record_group_fn`) across live.py/integrate.py and asserts that any call
passing state="QUARANTINED" also passes a non-empty `quarantine_reason` —
so a future call site missing it fails CI instead of requiring a third human
bug report.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from worktrail.orchestrator import integrate as integrate_module
from worktrail.orchestrator import live as live_module

LIVE_PATH = Path(live_module.__file__)
INTEGRATE_PATH = Path(integrate_module.__file__)

# 0-indexed positional slot of `state` / `quarantine_reason` in each callee's
# signature (see integrate.py's `_write_group_journal` and live.py's
# `_record_group_fn` / `_do_journal`).
_WRITE_GROUP_JOURNAL_SIG = {"state": 4, "quarantine_reason": 5}
_RECORD_GROUP_FN_SIG = {"state": 3, "quarantine_reason": 4}

_TARGETS = {
    "_write_group_journal": _WRITE_GROUP_JOURNAL_SIG,
    "_record_group_fn": _RECORD_GROUP_FN_SIG,
    "_do_journal": _WRITE_GROUP_JOURNAL_SIG,  # wraps _record_group/_write_group_journal 1:1
    "_persist_newly_quarantined": None,  # itself always passes a non-empty reason; skip
}


def _callee_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _const_str(node: ast.AST) -> str | None:
    return (
        node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else None
    )


def _arg_at(node: ast.Call, index: int, keyword: str) -> ast.AST | None:
    if len(node.args) > index:
        return node.args[index]
    for kw in node.keywords:
        if kw.arg == keyword:
            return kw.value
    return None


def _quarantined_writes_missing_reason(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, callee) for every call that journals state="QUARANTINED"
    without an explicit, non-empty `quarantine_reason` argument."""
    tree = ast.parse(path.read_text(), filename=str(path))
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = _callee_name(node)
        if callee not in _TARGETS or _TARGETS[callee] is None:
            continue
        sig = _TARGETS[callee]

        state_node = _arg_at(node, sig["state"], "state")
        if state_node is None or _const_str(state_node) != "QUARANTINED":
            continue  # not a quarantine write (or state passed some other way we can't see)

        reason_node = _arg_at(node, sig["quarantine_reason"], "quarantine_reason")
        if reason_node is None:
            offenders.append((node.lineno, callee))
            continue
        # A literal string must be non-empty. A non-literal (e.g. a
        # QUARANTINE_* module constant or a variable) is accepted -- this
        # test enforces "an explicit reason was passed", not its runtime
        # value, matching the actual PR #221/#227 bug shape (argument
        # entirely omitted, not an empty string deliberately passed).
        reason_str = _const_str(reason_node)
        if (
            reason_node is not None
            and isinstance(reason_node, ast.Constant)
            and reason_str == ""
        ):
            offenders.append((node.lineno, callee))
    return offenders


class QuarantineJournalWritesCarryReasonTest(unittest.TestCase):
    def test_every_quarantined_write_in_live_py_has_a_reason(self):
        offenders = _quarantined_writes_missing_reason(LIVE_PATH)
        self.assertEqual(
            offenders,
            [],
            f"QUARANTINED journal write(s) in {LIVE_PATH.name} missing quarantine_reason: "
            f"{offenders} -- see brief 20260807-214511 (PR #221, #227 both fixed this class "
            f"of bug at known call sites; this guards against a new one).",
        )

    def test_every_quarantined_write_in_integrate_py_has_a_reason(self):
        offenders = _quarantined_writes_missing_reason(INTEGRATE_PATH)
        self.assertEqual(
            offenders,
            [],
            f"QUARANTINED journal write(s) in {INTEGRATE_PATH.name} missing quarantine_reason: "
            f"{offenders} -- see brief 20260807-214511 (PR #221, #227 both fixed this class "
            f"of bug at known call sites; this guards against a new one).",
        )

    def test_scanner_actually_detects_a_missing_reason(self):
        """Meta-test: prove the AST scanner isn't vacuously passing by feeding
        it a synthetic snippet reproducing the exact PR #221 shape (state
        passed, quarantine_reason omitted entirely)."""
        import tempfile

        snippet = (
            "def f():\n"
            "    integrate._write_group_journal(\n"
            "        journal_path, name, '', head_branch, 'QUARANTINED',\n"
            "    )\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
            fh.write(snippet)
            tmp_path = Path(fh.name)
        try:
            offenders = _quarantined_writes_missing_reason(tmp_path)
        finally:
            tmp_path.unlink()
        self.assertEqual(offenders, [(2, "_write_group_journal")])


if __name__ == "__main__":
    unittest.main()
