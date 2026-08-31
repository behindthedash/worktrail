import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worktrail.router import gitnexus_preflight


class GitNexusPreflightTests(unittest.TestCase):
    @staticmethod
    def mcp_result(stdout: str = "") -> mock.Mock:
        return mock.Mock(returncode=0, stdout=stdout, stderr="")

    @staticmethod
    def live_stdout() -> str:
        return "\n".join(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {"serverInfo": {"name": "gitnexus"}},
                    }
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "result": {"contents": [{"text": "project: repo"}]},
                    }
                ),
            ]
        )

    def test_available_registered_canonical_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            registry = Path(tmp) / "registry.json"
            registry.write_text(json.dumps([{"name": "repo", "path": str(root)}]))
            with mock.patch.object(
                gitnexus_preflight, "canonical_repo_root", return_value=root
            ):
                result = gitnexus_preflight.check(
                    root,
                    registry,
                    mcp_runner=lambda *args, **kwargs: self.mcp_result(
                        self.live_stdout()
                    ),
                )
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["reason"], "mcp-context-readable")

    def test_unavailable_mcp_is_degraded_not_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            registry = Path(tmp) / "registry.json"
            registry.write_text(json.dumps([{"name": "repo", "path": str(root)}]))
            with mock.patch.object(
                gitnexus_preflight, "canonical_repo_root", return_value=root
            ):
                result = gitnexus_preflight.check(
                    root,
                    registry,
                    mcp_runner=lambda *args, **kwargs: self.mcp_result(),
                )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "mcp-unavailable")

    def test_mcp_timeout_is_explicit(self) -> None:
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            registry = Path(tmp) / "registry.json"
            registry.write_text(json.dumps([{"name": "repo", "path": str(root)}]))
            with mock.patch.object(
                gitnexus_preflight, "canonical_repo_root", return_value=root
            ):
                result = gitnexus_preflight.check(
                    root, registry, mcp_runner=timeout, timeout=0.1
                )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "mcp-timeout")

    def test_stale_registry_skips_live_probe(self) -> None:
        mcp_runner = mock.Mock()

        def git_runner(repo, *args):
            return mock.Mock(returncode=0, stdout="current\n", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            registry = Path(tmp) / "registry.json"
            registry.write_text(
                json.dumps(
                    [{"name": "repo", "path": str(root), "lastCommit": "indexed"}]
                )
            )
            with mock.patch.object(
                gitnexus_preflight, "canonical_repo_root", return_value=root
            ):
                result = gitnexus_preflight.check(
                    root, registry, runner=git_runner, mcp_runner=mcp_runner
                )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "registry-stale")
        mcp_runner.assert_not_called()

    def test_missing_registry_is_degraded_not_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            with mock.patch.object(
                gitnexus_preflight, "canonical_repo_root", return_value=root
            ):
                result = gitnexus_preflight.check(root, Path(tmp) / "missing.json")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "registry-missing")
        self.assertIn("actual worktree", gitnexus_preflight.prompt_note(result))

    def test_git_unavailable_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            with mock.patch.object(
                gitnexus_preflight, "canonical_repo_root", return_value=None
            ):
                result = gitnexus_preflight.check(root, Path(tmp) / "registry.json")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "canonical-repo-unavailable")


class GitNexusPreflightCliTests(unittest.TestCase):
    def test_json_output_and_advisory_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            with (
                mock.patch.object(
                    gitnexus_preflight, "canonical_repo_root", return_value=None
                ),
                mock.patch("sys.stdout", new_callable=io.StringIO) as out,
            ):
                exit_code = gitnexus_preflight.main(
                    ["--repo", str(root), "--registry", str(Path(tmp) / "missing.json"), "--json"]
                )
        self.assertEqual(exit_code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["reason"], "canonical-repo-unavailable")

    def test_strict_exits_nonzero_on_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            with mock.patch.object(
                gitnexus_preflight, "canonical_repo_root", return_value=None
            ):
                exit_code = gitnexus_preflight.main(
                    ["--repo", str(root), "--registry", str(Path(tmp) / "missing.json"), "--strict"]
                )
        self.assertEqual(exit_code, 1)

    def test_default_no_strict_exits_zero_even_when_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            with mock.patch.object(
                gitnexus_preflight, "canonical_repo_root", return_value=None
            ):
                exit_code = gitnexus_preflight.main(
                    ["--repo", str(root), "--registry", str(Path(tmp) / "missing.json")]
                )
        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
