#!/usr/bin/env python3
"""
go_seed: Seed prompt generator for sdd-workflow Phase 6 (go subprocess dispatch).

Given resolved orchestrator parameters (repo, base branch, route, spec, run record path),
emits a canonical seed prompt for sdd-workflow Phase 6. The seed contains ONLY the
resolved fields and an instruction to execute sdd-workflow, with no intermediate go state
(dashboard, policy, classify) included.

Security (CWE-78): all path inputs are validated as absolute; the script never
shells out and writes nothing — the seed prompt goes to stdout only.

Run the inline unit tests with `--self-test`.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional


SUPPORTED_AGENTS = ("claude", "codex", "opencode")


def detect_default_agent(environ: Optional[dict[str, str]] = None) -> str:
    """Resolve the worker CLI when the caller did not pass one explicitly."""
    env = os.environ if environ is None else environ
    return (
        env.get("GO_AGENT_CLI")
        or env.get("ORCH_AGENT")
        or ("opencode" if env.get("OPENCODE_PARENT") else None)
        or ("codex" if env.get("CODEX_CI") or env.get("CODEX_THREAD_ID") else None)
        or "claude"
    )


def validate_path(path: str, name: str) -> str:
    """Validate that a path is absolute. Raise SystemExit if not.

    Args:
        path: The path to validate.
        name: The argument name (for error messages).

    Returns:
        The validated path string.

    Raises:
        SystemExit: If the path is not absolute.
    """
    if not os.path.isabs(path):
        print(f"error: --{name} must be an absolute path, got: {path}", file=sys.stderr)
        sys.exit(1)
    return path


def build_seed(
    repo: str,
    base: str,
    route: str,
    spec: str,
    run: str,
    brief: Optional[str] = None,
    agent: str | None = None,
) -> str:
    """Build the canonical seed prompt.

    Args:
        repo: Absolute path to the repository.
        base: Base branch name (e.g., "dev", "main").
        route: Route designation (e.g., "A", "D").
        spec: Spec identifier (e.g., "016-go-subprocess-dispatch").
        run: Absolute path to the run record JSON file.
        brief: Optional absolute path to a brief file.
        agent: Headless CLI agent resolved at the Go front door. Falls back
               to detect_default_agent() when absent (caller did not specify).

    Returns:
        Canonical seed prompt as a string.
    """
    resolved_agent = agent or detect_default_agent()
    lines = [
        f"Repo: {repo}",
        f"Base branch: {base}",
        f"Route: {route}",
        f"Spec: {spec}",
        f"Run record path: {run}",
        f"Agent CLI: {resolved_agent}",
    ]

    if brief:
        lines.append(f"Brief: {brief}")

    lines.append("")
    lines.append("Instruction: Execute sdd-workflow starting at Phase 6 to begin parallel task dispatch.")

    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    """CLI entry point.

    Args:
        argv: Command-line arguments (for testing). Defaults to sys.argv[1:].

    Returns:
        Exit code: 0 on success, 1 on error.
    """
    parser = argparse.ArgumentParser(
        description="Generate a seed prompt for sdd-workflow Phase 6 (go subprocess dispatch)",
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="Absolute path to the target repository",
    )
    parser.add_argument(
        "--base",
        required=True,
        help="Base branch name (e.g., 'dev', 'main')",
    )
    parser.add_argument(
        "--route",
        required=True,
        help="Route designation (e.g., 'A', 'D')",
    )
    parser.add_argument(
        "--spec",
        required=True,
        help="Spec identifier (e.g., '016-go-subprocess-dispatch')",
    )
    parser.add_argument(
        "--run",
        required=True,
        help="Absolute path to the run record JSON file",
    )
    parser.add_argument(
        "--brief",
        default=None,
        help="Optional absolute path to a brief file",
    )
    parser.add_argument(
        "--agent",
        default=detect_default_agent(),
        choices=SUPPORTED_AGENTS,
        help="Headless agent CLI to use for downstream SDD workers",
    )

    args = parser.parse_args(argv)

    # Validate paths.
    repo = validate_path(args.repo, "repo")
    run = validate_path(args.run, "run")
    brief = None
    if args.brief:
        brief = validate_path(args.brief, "brief")

    seed = build_seed(repo, args.base, args.route, args.spec, run, brief, args.agent)
    print(seed)
    return 0


def _run_tests() -> int:
    """Run the inline unit test suite."""
    import subprocess
    import unittest

    class TestGoSeed(unittest.TestCase):
        """Inline unit tests for go_seed.py CLI."""

        def _run_cli(self, args: list) -> tuple[int, str, str]:
            """Run the CLI via subprocess and capture stdout/stderr.

            Args:
                args: Command-line arguments (without the script name).

            Returns:
                Tuple of (exit_code, stdout, stderr).
            """
            env = os.environ.copy()
            for key in ("GO_AGENT_CLI", "ORCH_AGENT", "OPENCODE_PARENT", "CODEX_CI", "CODEX_THREAD_ID"):
                env.pop(key, None)
            result = subprocess.run(
                [sys.executable, __file__] + args,
                capture_output=True,
                text=True,
                env=env,
            )
            return result.returncode, result.stdout, result.stderr

        def test_valid_required_only(self) -> None:
            """Test valid invocation with required arguments only."""
            code, stdout, stderr = self._run_cli([
                "--repo", "/home/user/projects/foo",
                "--base", "dev",
                "--route", "D",
                "--spec", "016-go-subprocess-dispatch",
                "--run", "/home/user/.go/runs/test.json",
            ])
            self.assertEqual(code, 0, f"stderr: {stderr}")
            self.assertIn("Repo: /home/user/projects/foo", stdout)
            self.assertIn("Base branch: dev", stdout)
            self.assertIn("Route: D", stdout)
            self.assertIn("Spec: 016-go-subprocess-dispatch", stdout)
            self.assertIn("Run record path: /home/user/.go/runs/test.json", stdout)
            self.assertIn("Agent CLI: claude", stdout)
            self.assertIn("Instruction: Execute sdd-workflow", stdout)
            # Seed must NOT contain go internals.
            self.assertNotIn("dashboard", stdout)
            self.assertNotIn("policy", stdout)
            self.assertNotIn("classify", stdout)
            self.assertNotIn("go SKILL.md", stdout)

        def test_valid_with_brief(self) -> None:
            """Test valid invocation with optional --brief."""
            code, stdout, stderr = self._run_cli([
                "--repo", "/home/user/projects/foo",
                "--base", "dev",
                "--route", "D",
                "--spec", "016-go-subprocess-dispatch",
                "--run", "/home/user/.go/runs/test.json",
                "--brief", "/home/user/work-queue/queue/brief.md",
            ])
            self.assertEqual(code, 0, f"stderr: {stderr}")
            self.assertIn("Brief: /home/user/work-queue/queue/brief.md", stdout)

        def test_valid_with_codex_agent(self) -> None:
            """Test valid invocation with --agent codex."""
            code, stdout, stderr = self._run_cli([
                "--repo", "/home/user/projects/foo",
                "--base", "dev",
                "--route", "D",
                "--spec", "016-go-subprocess-dispatch",
                "--run", "/home/user/.go/runs/test.json",
                "--agent", "codex",
            ])
            self.assertEqual(code, 0, f"stderr: {stderr}")
            self.assertIn("Agent CLI: codex", stdout)

        def test_valid_with_opencode_agent(self) -> None:
            """Test OpenCode is a first-class seeded provider."""
            code, stdout, stderr = self._run_cli([
                "--repo", "/home/user/projects/foo",
                "--base", "dev",
                "--route", "D",
                "--spec", "016-go-subprocess-dispatch",
                "--run", "/home/user/.go/runs/test.json",
                "--agent", "opencode",
            ])
            self.assertEqual(code, 0, f"stderr: {stderr}")
            self.assertIn("Agent CLI: opencode", stdout)

        def test_parent_marker_selects_opencode(self) -> None:
            self.assertEqual(
                detect_default_agent({"OPENCODE_PARENT": "1"}), "opencode"
            )
            self.assertEqual(
                detect_default_agent({"OPENCODE_PARENT": "1", "GO_AGENT_CLI": "claude"}),
                "claude",
            )

        def test_missing_required_arg(self) -> None:
            """Test error when required argument is missing."""
            code, stdout, stderr = self._run_cli([
                "--base", "dev",
                "--route", "D",
                "--spec", "016-go-subprocess-dispatch",
                "--run", "/home/user/.go/runs/test.json",
            ])
            self.assertNotEqual(code, 0, "Should exit non-zero when --repo is missing")
            self.assertIn("required", stderr.lower() or stdout.lower())

        def test_relative_path_validation(self) -> None:
            """Test error when a path argument is relative."""
            code, stdout, stderr = self._run_cli([
                "--repo", "./relative/path",
                "--base", "dev",
                "--route", "D",
                "--spec", "016-go-subprocess-dispatch",
                "--run", "/home/user/.go/runs/test.json",
            ])
            self.assertNotEqual(code, 0, "Should exit non-zero for relative path")
            self.assertIn("absolute", stderr.lower())

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestGoSeed)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    # Inline tests run only on explicit request; a bare invocation gets
    # argparse's usage error instead of silently running the test suite.
    if "--self-test" in sys.argv:
        sys.exit(_run_tests())
    sys.exit(main())
