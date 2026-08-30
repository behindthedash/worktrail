#!/usr/bin/env python3
"""Every `verify_and_cleanup` call site must tell the deny-list which spec root
to guard.

`Verifier(spec_rel=...)` is optional so callers predating the seam keep working.
That default is also how the bug shipped: #24 added the seam and wired one of
three construction paths, so `full-real` -- the path an actual run uses -- built
a Verifier with `spec_rel=None`. The deny-list silently fell back to devkit's
`docs/specs/`, which on an OpenSpec run leaves `openspec/**` unguarded while
falsely flagging `docs/specs/**`. Exactly the failure
`forbidden_prefixes_for`'s own docstring warns about, shipped anyway.

No test of `Verifier` itself can see this: the object behaves correctly for
whatever `spec_rel` it is handed. The defect lives in the caller, so this checks
the callers -- the same approach `test_plugin_surface.py` takes for the other
cross-file contracts in this repo that no runtime assertion can reach.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "worktrail" / "orchestrator"


def _call_sites(source: str, callee: str) -> list[str]:
    """Return the argument text of each `callee(...)` call, brackets balanced."""
    out = []
    for m in re.finditer(rf"\b{re.escape(callee)}\s*\(", source):
        i, depth = m.end(), 1
        while i < len(source) and depth:
            if source[i] == "(":
                depth += 1
            elif source[i] == ")":
                depth -= 1
            i += 1
        out.append(source[m.end() : i - 1])
    return out


def test_every_verify_and_cleanup_call_names_the_spec_root():
    live = (SRC / "live.py").read_text()
    calls = _call_sites(live, "verify.verify_and_cleanup")
    assert calls, "no verify_and_cleanup call sites found -- did the callee move?"
    missing = [c for c in calls if "spec_rel" not in c]
    assert not missing, (
        f"{len(missing)} of {len(calls)} verify_and_cleanup call(s) omit spec_rel, "
        "so the worker deny-list guards devkit's spec root regardless of which "
        "format is running"
    )


def test_every_verifier_construction_names_the_spec_root():
    live = (SRC / "live.py").read_text()
    calls = _call_sites(live, "verify_module.Verifier")
    assert calls, "no Verifier construction found in live.py -- did it move?"
    missing = [c for c in calls if "spec_rel" not in c]
    assert not missing, f"{len(missing)} Verifier construction(s) omit spec_rel"


def test_declared_files_reaches_the_deny_list():
    """Without it a CI-focused spec's own deliverable reads as a violation."""
    live = (SRC / "live.py").read_text()
    calls = _call_sites(live, "verify.verify_and_cleanup")
    missing = [c for c in calls if "declared_files" not in c]
    assert not missing, (
        f"{len(missing)} of {len(calls)} verify_and_cleanup call(s) omit "
        "declared_files, so a declared `.github/workflows/**` deliverable is "
        "struck out as an out-of-scope edit"
    )
