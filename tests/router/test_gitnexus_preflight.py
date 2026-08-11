import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worktrail.router import gitnexus_preflight


class GitNexusPreflightTests(unittest.TestCase):
    def test_available_registered_canonical_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            registry = Path(tmp) / "registry.json"
            registry.write_text(json.dumps([{"name": "repo", "path": str(root)}]))
            with mock.patch.object(gitnexus_preflight, "canonical_repo_root", return_value=root):
                result = gitnexus_preflight.check(root, registry)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["reason"], "canonical-base-index-registered")

    def test_missing_registry_is_degraded_not_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            with mock.patch.object(gitnexus_preflight, "canonical_repo_root", return_value=root):
                result = gitnexus_preflight.check(root, Path(tmp) / "missing.json")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "registry-missing")
        self.assertIn("actual worktree", gitnexus_preflight.prompt_note(result))

    def test_git_unavailable_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            with mock.patch.object(gitnexus_preflight, "canonical_repo_root", return_value=None):
                result = gitnexus_preflight.check(root, Path(tmp) / "registry.json")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "canonical-repo-unavailable")


if __name__ == "__main__":
    unittest.main()
