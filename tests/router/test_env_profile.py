"""Tests for `env_profile.resolve_env_profile` -- the `auth.profile` lane that
supplies a worker's environment from a JSON file the operator already maintains
(typically `~/.claude/settings.json`), because `--setting-sources
project,local` excludes the user-level settings file that would otherwise carry
it.

The load-bearing invariant across every failure path is that a value is never
printed for a key the operator did not declare in `expect`. That assertion is
checked against a single sentinel secret in every raising case below, so a
future edit that widens an error message cannot leak one silently.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worktrail.router.env_profile import resolve_env_profile
from worktrail.router.policy import OperatorConfigError

SECRET = "sk-sentinel-do-not-print-0000"

TARGET = "claude-deepseek"
PROFILE = "deepseek"


def _write(document, *, directory: Path, name: str = "settings.json") -> Path:
    path = directory / name
    path.write_text(
        document if isinstance(document, str) else json.dumps(document),
        encoding="utf-8",
    )
    return path


class EnvProfileTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = Path(self._dir.name)

    def resolve(self, profile, *, target=TARGET):
        return resolve_env_profile(
            PROFILE, profile, target=target, declared_in=self.dir / "routing.yaml"
        )

    def assertRefused(self, profile, *, includes=(), target=TARGET):
        """Resolve, assert `OperatorConfigError`, and assert the sentinel secret
        is nowhere in the message."""
        with self.assertRaises(OperatorConfigError) as ctx:
            self.resolve(profile, target=target)
        message = str(ctx.exception)
        self.assertNotIn(SECRET, message, "a value must never reach an error message")
        for fragment in includes:
            self.assertIn(fragment, message)
        return message


class TestHappyPath(EnvProfileTestCase):
    def test_returns_only_declared_keys(self):
        path = _write(
            {
                "env": {
                    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
                    "ANTHROPIC_AUTH_TOKEN": SECRET,
                    "UNRELATED": "x",
                }
            },
            directory=self.dir,
        )
        resolved = self.resolve(
            {
                "from": str(path),
                "keys": ["ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"],
                "expect": {"ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic"},
            }
        )
        self.assertEqual(
            resolved,
            {
                "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
                "ANTHROPIC_AUTH_TOKEN": SECRET,
            },
        )
        self.assertNotIn("UNRELATED", resolved)

    def test_expect_may_assert_a_key_it_does_not_copy(self):
        """`keys` and `expect` are independent axes: asserting provenance on a
        key you do not want copied is legitimate, and is what makes the
        never-print-a-value rule easy to honour."""
        path = _write(
            {"env": {"PROVENANCE": "expected", "A": "1"}},
            directory=self.dir,
        )
        resolved = self.resolve(
            {
                "from": str(path),
                "keys": ["A"],
                "expect": {"PROVENANCE": "expected"},
            }
        )
        self.assertEqual(resolved, {"A": "1"})

    def test_profile_without_expect_copies_without_asserting(self):
        path = _write({"env": {"A": SECRET}}, directory=self.dir)
        resolved = self.resolve({"from": str(path), "keys": ["A"]})
        self.assertEqual(resolved, {"A": SECRET})

    def test_tilde_is_expanded(self):
        _write({"env": {"A": "1"}}, directory=self.dir, name=".settings.json")
        with mock.patch.dict(os.environ, {"HOME": str(self.dir)}):
            resolved = self.resolve({"from": "~/.settings.json", "keys": ["A"]})
        self.assertEqual(resolved, {"A": "1"})


class TestExpect(EnvProfileTestCase):
    def test_mismatch_names_key_expected_and_actual(self):
        path = _write(
            {"env": {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}},
            directory=self.dir,
        )
        message = self.assertRefused(
            {
                "from": str(path),
                "keys": ["ANTHROPIC_BASE_URL"],
                "expect": {"ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic"},
            },
            includes=["ANTHROPIC_BASE_URL", "api.deepseek.com"],
        )
        # Both sides are declared non-secret by the operator writing `expect`,
        # so an expect mismatch is the ONE place an actual value may appear.
        self.assertIn("api.anthropic.com", message)

    def test_asserted_key_absent_from_file_is_a_mismatch(self):
        path = _write({"env": {"OTHER": "x"}}, directory=self.dir)
        self.assertRefused(
            {"from": str(path), "keys": [], "expect": {"MISSING": "wanted"}},
            includes=["MISSING"],
        )


class TestSourceFailures(EnvProfileTestCase):
    def test_missing_file(self):
        self.assertRefused(
            {"from": str(self.dir / "nope.json"), "keys": ["A"]},
            includes=["does not exist"],
        )

    def test_relative_from_is_refused(self):
        """Refused, not resolved relative to the routing file: an explicit
        `--model-map`/`--effort` override points WORKTRAIL_ROUTING_FILE at a
        mkstemp file under /tmp, so "relative to the routing file" would mean a
        different directory exactly when an override is in play."""
        self.assertRefused(
            {"from": "secrets.json", "keys": ["A"]},
            includes=["not absolute"],
        )

    def test_malformed_json(self):
        path = _write("{not json", directory=self.dir)
        self.assertRefused(
            {"from": str(path), "keys": ["A"]}, includes=["not valid JSON"]
        )

    def test_no_env_object(self):
        path = _write({"model": "x"}, directory=self.dir)
        self.assertRefused({"from": str(path), "keys": ["A"]}, includes=["no", "env"])

    def test_env_not_a_mapping(self):
        path = _write({"env": ["ANTHROPIC_BASE_URL"]}, directory=self.dir)
        self.assertRefused({"from": str(path), "keys": ["A"]}, includes=["env"])


class TestKeyFailures(EnvProfileTestCase):
    def test_key_absent(self):
        path = _write({"env": {"OTHER": SECRET}}, directory=self.dir)
        self.assertRefused(
            {"from": str(path), "keys": ["ANTHROPIC_AUTH_TOKEN"]},
            includes=["ANTHROPIC_AUTH_TOKEN", "is not set"],
        )

    def test_key_empty(self):
        path = _write({"env": {"ANTHROPIC_AUTH_TOKEN": ""}}, directory=self.dir)
        self.assertRefused(
            {"from": str(path), "keys": ["ANTHROPIC_AUTH_TOKEN"]},
            includes=["is empty"],
        )

    def test_key_not_a_string(self):
        path = _write({"env": {"A": {"nested": SECRET}}}, directory=self.dir)
        self.assertRefused(
            {"from": str(path), "keys": ["A"]},
            includes=["A", "must be a string"],
        )


class TestMessageShape(EnvProfileTestCase):
    def test_every_failure_names_target_profile_and_file(self):
        """An operator reading the error must know which target, which profile,
        and which routing file to edit."""
        path = _write({"env": {}}, directory=self.dir)
        message = self.assertRefused(
            {"from": str(path), "keys": ["A"]}, includes=[TARGET, PROFILE]
        )
        self.assertIn(str(self.dir / "routing.yaml"), message)

    def test_secret_never_leaks_across_the_whole_failure_matrix(self):
        """One sentinel, every raising path. The key holding SECRET is only
        ever present-but-unusable or absent -- never declared in `expect` -- so
        it must not appear in any message."""
        not_a_string = _write(
            {"env": {"LEAK": {"inner": SECRET}}},
            directory=self.dir,
            name="not_a_string.json",
        )
        empty = _write({"env": {"LEAK": ""}}, directory=self.dir, name="empty.json")
        valid = _write(
            {"env": {"LEAK": SECRET, "ANTHROPIC_BASE_URL": "https://wrong"}},
            directory=self.dir,
            name="valid.json",
        )
        cases = [
            {"from": str(self.dir / "absent.json"), "keys": ["LEAK"]},
            {"from": "relative.json", "keys": ["LEAK"]},
            {"from": str(not_a_string), "keys": ["LEAK"]},
            {"from": str(empty), "keys": ["LEAK"]},
            {"from": str(valid), "keys": ["LEAK"]},  # valid, but never raises
        ]
        for profile in cases:
            with self.subTest(profile=profile):
                try:
                    self.resolve(profile)
                except OperatorConfigError as exc:
                    self.assertNotIn(SECRET, str(exc))


if __name__ == "__main__":
    unittest.main()
