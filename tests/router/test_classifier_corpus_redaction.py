#!/usr/bin/env python3
"""The committed classifier corpus is redacted and outcome-labelled.

``tests/fixtures/classifier_corpus.json`` is derived from the operator's private
work queue by ``scripts/regenerate_classifier_corpus.py``. Two properties have
to hold on the *committed* file, not just inside the generator, because a
regeneration is a hand-run step and a redaction that silently stops matching
would publish real repo names, paths, PR numbers or commit shas into a public
repo.

1. **No identifier survives redaction.** Every pattern in the generator's own
   ``REDACTIONS`` list (plus the repo-name rule built from ``~/projects`` and
   ``EXTRA_NAMES``) is re-run against the committed text here. The generator
   already refuses to write a leaking fixture; this catches a fixture committed
   by an older generator, or one hand-edited afterwards.

2. **Every label is an outcome.** ``label_source`` must be ``"actual"`` on every
   item -- a route read from the ``selected_route`` of a run record that
   consumed the brief. A ``recommended-route`` frontmatter label is a machine
   suggestion, and a ratchet scored against it pins agreement with a guess.
"""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "classifier_corpus.json"
GENERATOR_PATH = REPO_ROOT / "scripts" / "regenerate_classifier_corpus.py"


def _load_generator():
    """Import the generator by path -- ``scripts/`` is not an importable package."""
    spec = importlib.util.spec_from_file_location("regen_corpus", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ClassifierCorpusRedactionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.generator = _load_generator()
        cls.data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_no_focus_text_matches_a_redaction_pattern(self) -> None:
        redactions = [
            *self.generator.REDACTIONS,
            self.generator.repo_name_redaction(
                self.generator.known_repo_names(Path.home() / "projects")
            ),
        ]
        leaks: dict[str, str] = {}
        for item in self.data["items"]:
            for name in self.generator.find_leaks(item["focus"], redactions):
                leaks.setdefault(name, item["focus"])
        self.assertEqual(
            leaks,
            {},
            "committed classifier corpus still matches redaction rule(s); "
            "re-run scripts/regenerate_classifier_corpus.py",
        )

    def test_every_item_is_outcome_labelled(self) -> None:
        sources = {item.get("label_source") for item in self.data["items"]}
        self.assertEqual(
            sources,
            {"actual"},
            "every corpus item must be labelled from a run record's "
            "selected_route; a recommended-route label is a machine "
            "suggestion, not an outcome",
        )

    def test_meta_item_count_matches_the_items(self) -> None:
        self.assertEqual(self.data["_meta"]["item_count"], len(self.data["items"]))

    def test_meta_per_route_counts_match_the_items(self) -> None:
        counted: dict[str, int] = {}
        for item in self.data["items"]:
            counted[item["expected_route"]] = counted.get(item["expected_route"], 0) + 1
        self.assertEqual(self.data["_meta"]["per_route"], counted)

    def test_every_item_carries_a_route_and_non_empty_focus(self) -> None:
        for item in self.data["items"]:
            self.assertIn(item["expected_route"], list("ABCDEFGHIJ"))
            self.assertTrue(item["focus"].strip())


class RedactionRuleTest(unittest.TestCase):
    """The rules themselves, on the shapes that actually got through once."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.generator = _load_generator()
        cls.repo_rule = cls.generator.repo_name_redaction(["worktrail", "datalena"])

    def _redact(self, text: str) -> str:
        return self.generator.redact(text, [*self.generator.REDACTIONS, self.repo_rule])

    def test_repo_name_inside_a_hyphenated_compound_is_redacted(self) -> None:
        # The original `[\w-]` boundaries excluded hyphen adjacency, which let
        # `worktrail-preflight` and `007-lena-knowledge-context-layer` through.
        self.assertEqual(
            self._redact("wired into worktrail-preflight/pre_pr_gate.py"),
            "wired into the-repo-preflight/pre_pr_gate.py",
        )

    def test_repo_name_is_case_insensitive(self) -> None:
        self.assertEqual(self._redact("Datalena CI"), "the-repo CI")

    def test_bare_issue_ref_after_a_slash_is_redacted(self) -> None:
        # `PRs #1686/#1687/#1688` -- a `/` in the lookbehind missed all but the
        # first, which the `PR #` rule had already taken.
        # `PRs` (plural) is not the `PR #` rule's shape, so both numbers are
        # left to the bare rule; the literal word is not an identifier.
        self.assertEqual(self._redact("across PRs #1686/#1687"), "across PRs <pr>/<pr>")

    def test_brief_and_run_ids_are_redacted(self) -> None:
        self.assertEqual(
            self._redact("brief 20260820-024527 and run go-20260811-132806"),
            "brief <brief-id> and run go-<brief-id>",
        )

    def test_home_paths_urls_and_emails_are_redacted(self) -> None:
        self.assertEqual(
            self._redact("see /home/someone/projects/x, https://example.com/a, a@b.io"),
            "see <the-repo-path>, <url> <email>",
        )

    def test_a_commit_sha_is_redacted_but_a_word_is_not(self) -> None:
        self.assertEqual(self._redact("at 9526232f now"), "at <sha> now")
        self.assertEqual(
            self._redact("decade of beefed cabbage"), "decade of beefed cabbage"
        )

    def test_ordinary_prose_is_left_alone(self) -> None:
        text = "The nightly digest never reached the operator after a deploy."
        self.assertEqual(self._redact(text), text)


if __name__ == "__main__":
    unittest.main()
