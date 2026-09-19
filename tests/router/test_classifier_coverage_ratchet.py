#!/usr/bin/env python3
"""CI routing-regression ratchet over ``tests/fixtures/classifier_corpus.json``.

The classifier coverage audit (``classifier_coverage.py``) can only run as a
manual CLI against the operator's personal ``~/work-queue`` corpus, which CI
cannot see. This test gives it a CI-visible, bounded, redacted stand-in so a
``classify.py`` signal-table change can be checked for a corpus-wide accuracy
regression before merge, not just against the hand-written routing cassette
scenarios (which pin individual cases, not aggregate accuracy).

It reuses ``audit_coverage()`` unmodified: the fixture corpus is materialized
into a real ``queue/`` directory of brief files (same shape ``load_briefs()``
already reads in production), then handed to ``audit_coverage()`` exactly as
the CLI would. No comparison/clustering logic is reimplemented here.

**Currently advisory, not required.** This module is intentionally excluded
from the main ``pytest -q`` run in ``CI: Lint, Test & Build`` (see the
``--ignore`` flag on that job's Test step) and instead runs in its own
non-required ``Classifier Coverage Ratchet`` job, so the baseline below can be
recalibrated over a PR or two without blocking merges. Promote it by folding
its job into ``required_status_checks`` in ``.github/rulesets/protect-main.json``
once the threshold has settled.

**What the number means.** Every fixture item is labelled with the route the
brief was ACTUALLY run under -- the ``selected_route`` of a run record that
consumed it (``label_source: "actual"``, enforced by
``test_classifier_corpus_redaction.py``). Until 2026-09-19 the corpus also
carried items labelled only by their own ``recommended-route`` frontmatter,
which is a machine suggestion, not an outcome: scoring ``classify.py`` against
it measured agreement with a guess, and the agreed count could not distinguish
a routing regression from label noise. Those items are gone, so this number is
now meaningful -- but only for outcome-labelled items, which is all the corpus
contains and all it may ever contain. It is NOT an accuracy figure for the
front door: the ``handoff_route`` hint is withheld (see
``classifier_coverage``'s module docstring), so it measures the organic text
signal alone.

**Re-baselining.** A change that intentionally shifts routing behavior may
lower the agreed count below ``BASELINE_AGREED``. Re-run this file's
materialization against the current ``classify.py`` (or just run this test
and read the failure's actual count), confirm the new number by eye against
``tests/fixtures/classifier_corpus.json``'s sample of expected routes, and
update ``BASELINE_AGREED`` with a short comment explaining what shifted and
why the new number is still correct routing behavior, not a fresh regression.
Regenerating the corpus itself (``scripts/regenerate_classifier_corpus.py``)
moves both baselines; it is a deliberate, hand-run step.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from worktrail.router import classifier_coverage as cc

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "classifier_corpus.json"
)

# Pinned to tests/fixtures/classifier_corpus.json (103 items) as of
# 2026-09-19, when the corpus was regenerated from outcome labels only: 2246
# briefs considered, 121 carrying a run record's selected_route, 103 kept after
# per-route stratification (A 4, C 11, D 8, E 6, F 24, G 13, H 13, I 17, J 7 --
# route B has no outcome-labelled brief at all and is absent). The previous
# 68/236 was scored against a corpus where most labels were the brief's own
# recommended-route frontmatter, so the drop to 22/103 is not a routing
# regression: it is what the classifier scores against real outcomes once the
# partly-tautological labels stop inflating it.
# classify() is pure and regex-only, and the fixture and replay inputs below
# are all fixed, so this is an exact reproducible count, not a tolerance band.
BASELINE_AGREED = 22
BASELINE_COMPARED = 103


def _materialize(items: list, queue_root: Path) -> None:
    """Write the fixture corpus as real brief files under ``queue_root/queue/``.

    Mirrors the frontmatter shape ``load_briefs()`` reads in production
    (``id``, ``created``, ``focus``, ``recommended-route``) -- the same
    pattern ``test_classifier_coverage.py``'s ``_write_brief`` helper uses --
    so ``audit_coverage()`` runs completely unmodified over fixture data.

    ``recommended-route`` is the transport, not the provenance: with no run
    records for fixture briefs it is the only field ``audit_coverage()`` will
    read an expected route from, and the value written into it is the item's
    outcome label (``label_source: "actual"``).
    """
    directory = queue_root / "queue"
    directory.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(items):
        text = (
            "---\n"
            f"id: fixture-{index:04d}\n"
            "created: '2026-01-01T00:00:00-07:00'\n"
            f"focus: {json.dumps(item['focus'])}\n"
            f"recommended-route: {item['expected_route']}\n"
            "---\n"
        )
        (directory / f"fixture-{index:04d}.md").write_text(text, encoding="utf-8")


class ClassifierCoverageRatchetTest(unittest.TestCase):
    def test_agreement_does_not_regress_below_baseline(self) -> None:
        data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        items = data["items"]

        with TemporaryDirectory() as queue_dir, TemporaryDirectory() as runs_dir:
            _materialize(items, Path(queue_dir))
            # No run records for fixture briefs, so every comparison resolves
            # its expected route from the `recommended-route` field written by
            # `_materialize` above -- which carries the item's outcome label,
            # not a frontmatter suggestion. See that helper's docstring.
            report = cc.audit_coverage(
                queue_root=Path(queue_dir),
                runs_root=Path(runs_dir),
                limit=0,
            )

        agreement = report["agreement"]

        # Sanity: every fixture item must actually be scored. A silent drop
        # here (e.g. a brief-loading regression) would otherwise show up as
        # a false "improvement" in the agreement rate.
        self.assertEqual(
            agreement["compared"],
            BASELINE_COMPARED,
            f"expected {BASELINE_COMPARED} fixture items to be compared, got "
            f"{agreement['compared']} -- a brief-loading change may be silently "
            "dropping fixture items rather than a genuine corpus size change",
        )

        self.assertGreaterEqual(
            agreement["agreed"],
            BASELINE_AGREED,
            f"classifier coverage ratchet regressed: {agreement['agreed']}/"
            f"{agreement['compared']} agreed, baseline is {BASELINE_AGREED}/"
            f"{BASELINE_COMPARED}. If this classify.py change intentionally "
            "shifts routing, re-baseline BASELINE_AGREED in this file with a "
            "comment explaining why the new number is correct.",
        )


if __name__ == "__main__":
    unittest.main()
