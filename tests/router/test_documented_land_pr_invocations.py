#!/usr/bin/env python3
"""Regression test: every documented `worktrail-land-pr` invocation carries
`--commit-message`.

Brief `20261002-223206`. `land_pr.main` runs `_commit_pending` (step 1) before
`_ensure_compile_markers` (step 2), and `_commit_pending` refuses
(`refused_step: "dirty_tree"`) any dirty tree when no `commit_message` was
supplied -- only a clean tree may omit it. A dispatch's work is uncommitted by
construction, so the standard documented invocation lands on a dirty tree and
the message is therefore required, not optional. The Phase 8 prose called it
"optional" and three examples omitted it (run go-20261002-221353, PR #1388:
the first call refused with `refused_step: dirty_tree`, the identical re-run
with `--commit-message` landed and merged).

Passing `--commit-message` against a clean tree is a documented no-op --
`_commit_pending` returns at its `if not status.stdout.strip(): return None`
line before the message is ever read -- so requiring it in every documented
example is safe as well as correct.

The scan covers both shapes the skills use: a fenced bash block (SKILL.md,
subagent-prompts.md) where the command ends at the first line not continued
with a backslash, and an inline code span (routes.md) where it ends at the
closing backtick. Bare prose mentions of `worktrail-land-pr` with no arguments
are not invocations and are skipped.
"""

import unittest
from pathlib import Path

from worktrail.router import land_pr as land_pr_mod

REPO_ROOT = Path(land_pr_mod.__file__).resolve().parents[3]
SKILLS_ROOT = REPO_ROOT / "skills"

INVOCATION = "worktrail-land-pr"
REQUIRED_FLAG = "--commit-message"


def _invocation_span(text: str, start: int) -> str:
    """Text of the `worktrail-land-pr` invocation beginning at `start`.

    Inline-code shape (the command is wrapped in backticks) ends at the
    closing backtick; shell shape ends at the first line not continued with a
    trailing backslash. Returns "" for a bare mention that never starts an
    argument list."""
    if text.rfind("`", 0, start) > text.rfind("\n", 0, start):
        end = text.find("`", start)
        return text[start : end if end != -1 else len(text)]
    end = len(text)
    pos = start
    while True:
        line_end = text.find("\n", pos)
        if line_end == -1:
            break
        if not text[pos:line_end].rstrip().endswith("\\"):
            end = line_end
            break
        pos = line_end + 1
    return text[start:end]


def documented_invocations() -> list[tuple[str, str]]:
    """Every `(file, invocation-text)` pair in `skills/**/*.md` that actually
    invokes `worktrail-land-pr` (i.e. names at least one flag)."""
    found = []
    for path in sorted(SKILLS_ROOT.rglob("*.md")):
        text = path.read_text()
        rel = str(path.relative_to(SKILLS_ROOT))
        pos = 0
        while (idx := text.find(INVOCATION, pos)) != -1:
            pos = idx + len(INVOCATION)
            span = _invocation_span(text, idx)
            if "--" in span:
                found.append((rel, span))
    return found


class TestDocumentedLandPrInvocations(unittest.TestCase):
    def test_scan_finds_invocations(self):
        """Guard against the scanner silently matching nothing after a doc
        restructure -- an empty corpus would make the assertion below vacuous."""
        files = {rel for rel, _ in documented_invocations()}
        self.assertIn("worktrail-sdd-workflow/SKILL.md", files)
        self.assertIn("worktrail-go/references/routes.md", files)
        self.assertIn("worktrail-go/references/subagent-prompts.md", files)

    def test_every_documented_invocation_passes_commit_message(self):
        missing = [
            (rel, span.strip().replace("\n", " "))
            for rel, span in documented_invocations()
            if REQUIRED_FLAG not in span
        ]
        self.assertFalse(
            missing,
            "documented `worktrail-land-pr` invocation(s) omit "
            f"`{REQUIRED_FLAG}`; `_commit_pending` refuses a dirty tree without "
            "it and a dispatch's tree is dirty by construction, so following "
            "the doc burns a refused round trip:\n"
            + "\n".join(f"  {rel}: {span}" for rel, span in missing),
        )


if __name__ == "__main__":
    unittest.main()
