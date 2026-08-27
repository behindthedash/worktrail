#!/usr/bin/env python3
"""Tests for routing_cli.py. Run: python3 test_routing_cli.py"""
import subprocess
import unittest

from worktrail.router.routing_cli import list_opencode_models


class _FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class ListOpencodeModelsTests(unittest.TestCase):
    def test_parses_one_model_id_per_line(self):
        def fake_runner(cmd, **kwargs):
            self.assertEqual(cmd, ["opencode", "models"])
            return _FakeResult(stdout="opencode/deepseek-v4-flash-free\nopenrouter/gpt-5.4\ngoogle/gemini-3\n")

        models = list_opencode_models(runner=fake_runner)
        self.assertEqual(
            models,
            {"opencode/deepseek-v4-flash-free", "openrouter/gpt-5.4", "google/gemini-3"},
        )

    def test_ignores_blank_lines_and_strips_whitespace(self):
        def fake_runner(cmd, **kwargs):
            return _FakeResult(stdout="  opencode/foo  \n\n\nopenrouter/bar\n")

        models = list_opencode_models(runner=fake_runner)
        self.assertEqual(models, {"opencode/foo", "openrouter/bar"})

    def test_nonzero_exit_returns_empty_set_and_warns(self):
        def fake_runner(cmd, **kwargs):
            return _FakeResult(returncode=1, stdout="", stderr="boom")

        models = list_opencode_models(runner=fake_runner)
        self.assertEqual(models, set())

    def test_missing_binary_returns_empty_set_and_warns(self):
        def fake_runner(cmd, **kwargs):
            raise FileNotFoundError("opencode: command not found")

        models = list_opencode_models(runner=fake_runner)
        self.assertEqual(models, set())

    def test_timeout_returns_empty_set_and_warns(self):
        def fake_runner(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=["opencode", "models"], timeout=30)

        models = list_opencode_models(runner=fake_runner)
        self.assertEqual(models, set())

    def test_default_runner_is_subprocess_run(self):
        import inspect

        self.assertIs(inspect.signature(list_opencode_models).parameters["runner"].default,
                       subprocess.run)


if __name__ == "__main__":
    unittest.main()
