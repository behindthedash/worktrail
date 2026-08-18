"""End-to-end regression for adapter-to-internal-executor dispatch."""

from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from worktrail.router import skill_dispatch


_FAKE_AGENT = Path(__file__).with_name("fake_internal_dispatch_agent.py")


class InternalDispatchLifecycleTests(unittest.TestCase):
    def _run_seeded_lifecycle(
        self, root: Path, agent: str, outcome: str,
    ) -> tuple[int, str]:
        bin_dir = root / "bin"
        bin_dir.mkdir()
        shim = bin_dir / agent
        shim.write_text(
            f"#!/bin/sh\nexec {sys.executable} {_FAKE_AGENT} {agent} \"$@\"\n"
        )
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
        skills = root / "skills" / "worktrail-sdd-workflow"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text("---\nname: worktrail-sdd-workflow\n---\n")
        runs = root / "runs"
        runs.mkdir()
        run = runs / "parent.yaml"
        ownership = "go-parent-dispatch-owner"
        run.write_text(
            "run_id: parent\n"
            f"repository: {root}\n"
            "selected_route: F\n"
            f"dispatch_id: {ownership}\n"
            "subagents_called:\n"
            "final_status: null\n"
        )
        brief = root / "brief.md"
        brief.write_text("shared run lifecycle\n")
        seed = subprocess.run(
            [
                shutil.which("worktrail-go-seed"), "--repo", str(root),
                "--base", "main", "--route", "F", "--spec", "handoff:lifecycle",
                "--run", str(run), "--brief", str(brief), "--agent", agent,
            ],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        proof = root / "proof"
        ready = root / "ready"
        environment = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_INTERNAL_DISPATCH_PROOF": str(proof),
            "FAKE_INTERNAL_DISPATCH_EXPECTED": seed,
            "FAKE_INTERNAL_DISPATCH_LIFECYCLE": outcome,
            "FAKE_INTERNAL_DISPATCH_OWNERSHIP": ownership,
            "FAKE_INTERNAL_DISPATCH_READY": str(ready),
            "WORKTRAIL_SKILL_DISPATCH_DEPTH": "0",
            "WORKTRAIL_SKILL_ROOT": str(root / "skills"),
            "WORKTRAIL_CODEX_HOME": str(root / "codex-home"),
        }
        command = [
            shutil.which("worktrail-skill-dispatch"), "--agent", agent,
            "--skill", "worktrail-sdd-workflow", "--args", seed,
            "--cwd", str(root), "--write",
        ]
        if agent == "codex":
            command.append("--no-inherit-codex-auth")
        process = subprocess.Popen(command, env=environment)
        if outcome == "interrupted":
            deadline = time.monotonic() + 5
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "fake seeded child never became interruptible")
            os.kill(int(ready.read_text()), signal.SIGTERM)
        return process.wait(timeout=5), run.read_text()

    def test_seeded_child_mutates_only_the_exact_parent_run_record(self):
        for agent in skill_dispatch.SUPPORTED_AGENTS:
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                returncode, record = self._run_seeded_lifecycle(
                    root, agent, "complete",
                )
                self.assertEqual(returncode, 0)
                self.assertEqual(
                    list((root / "runs").glob("*.yaml")), [root / "runs/parent.yaml"],
                )
                self.assertIn("dispatch_id: go-parent-dispatch-owner", record)
                self.assertIn("- fake-child", record)
                self.assertIn("final_status: investigation_complete", record)

    def test_seeded_child_records_terminal_state_before_nonzero_exit(self):
        for agent in skill_dispatch.SUPPORTED_AGENTS:
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as tmp:
                returncode, record = self._run_seeded_lifecycle(
                    Path(tmp), agent, "nonzero",
                )
                self.assertEqual(returncode, 9)
                self.assertIn("dispatch_id: go-parent-dispatch-owner", record)
                self.assertIn("final_status: failed_recoverable", record)
                self.assertIn("seeded child exited nonzero", record)

    def test_seeded_child_records_terminal_state_when_interrupted(self):
        for agent in skill_dispatch.SUPPORTED_AGENTS:
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as tmp:
                returncode, record = self._run_seeded_lifecycle(
                    Path(tmp), agent, "interrupted",
                )
                self.assertEqual(returncode, 130)
                self.assertIn("dispatch_id: go-parent-dispatch-owner", record)
                self.assertIn("final_status: failed_recoverable", record)
                self.assertIn("seeded child interrupted", record)

    def test_handoff_route_reaches_each_provider_once_without_front_door_redirect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            for agent in skill_dispatch.SUPPORTED_AGENTS:
                shim = bin_dir / agent
                shim.write_text(
                    f"#!/bin/sh\nexec {sys.executable} {_FAKE_AGENT} {agent} \"$@\"\n"
                )
                shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
            skills = root / "skills" / "worktrail-sdd-workflow"
            skills.mkdir(parents=True)
            (skills / "SKILL.md").write_text("---\nname: worktrail-sdd-workflow\n---\n")

            for agent in skill_dispatch.SUPPORTED_AGENTS:
                with self.subTest(agent=agent):
                    proof = root / f"{agent}.proof"
                    environment = {
                        "PATH": f"{bin_dir}:{os.environ['PATH']}",
                        "FAKE_INTERNAL_DISPATCH_PROOF": str(proof),
                        "WORKTRAIL_SKILL_DISPATCH_DEPTH": "0",
                        "WORKTRAIL_SKILL_ROOT": str(root / "skills"),
                        "WORKTRAIL_CODEX_HOME": str(root / f"{agent}-home"),
                    }
                    arguments = [
                        "--agent", agent, "--skill", "worktrail-sdd-workflow",
                        "--args", "handoff:20260812-083302 route:F",
                        "--cwd", str(root), "--write",
                    ]
                    if agent == "codex":
                        arguments.append("--no-inherit-codex-auth")
                    with mock.patch.dict(os.environ, environment, clear=False):
                        self.assertEqual(skill_dispatch.main(arguments), 0)
                    self.assertEqual(
                        proof.read_text(),
                        f"executed:{agent}:handoff:20260812-083302:route:F\n",
                    )

    def test_installed_seed_and_dispatch_contract_across_providers(self):
        seed_command = shutil.which("worktrail-go-seed")
        dispatch_command = shutil.which("worktrail-skill-dispatch")
        self.assertIsNotNone(seed_command, "worktrail-go-seed is not installed")
        self.assertIsNotNone(dispatch_command, "worktrail-skill-dispatch is not installed")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            brief = root / "20260812-090245-add-an-install-level-cross.md"
            brief.write_text("install dispatch contract\n")
            run = root / "run.yaml"
            run.write_text("run_id: install-contract\n")
            skills = root / "skills" / "worktrail-sdd-workflow"
            skills.mkdir(parents=True)
            (skills / "SKILL.md").write_text("---\nname: worktrail-sdd-workflow\n---\n")
            for agent in skill_dispatch.SUPPORTED_AGENTS:
                shim = bin_dir / agent
                shim.write_text(
                    f"#!/bin/sh\nexec {sys.executable} {_FAKE_AGENT} {agent} \"$@\"\n"
                )
                shim.chmod(shim.stat().st_mode | stat.S_IEXEC)

            for agent in skill_dispatch.SUPPORTED_AGENTS:
                with self.subTest(agent=agent):
                    seed = subprocess.run(
                        [
                            seed_command, "--repo", str(root), "--base", "main",
                            "--route", "F", "--spec", "handoff:20260812-090245",
                            "--run", str(run), "--brief", str(brief), "--agent", agent,
                        ],
                        check=True, capture_output=True, text=True,
                    ).stdout.strip()
                    self.assertIn("Route: F", seed)
                    self.assertIn("Spec: handoff:20260812-090245", seed)
                    self.assertIn(f"Agent CLI: {agent}", seed)
                    self.assertIn(f"Brief: {brief}", seed)

                    proof = root / f"installed-{agent}.proof"
                    argv_proof = root / f"installed-{agent}.argv.json"
                    environment = {
                        **os.environ,
                        "PATH": f"{bin_dir}:{os.environ['PATH']}",
                        "FAKE_INTERNAL_DISPATCH_PROOF": str(proof),
                        "FAKE_INTERNAL_DISPATCH_ARGV_PROOF": str(argv_proof),
                        "FAKE_INTERNAL_DISPATCH_EXPECTED": seed,
                        "WORKTRAIL_SKILL_DISPATCH_DEPTH": "0",
                        "WORKTRAIL_SKILL_ROOT": str(root / "skills"),
                        "WORKTRAIL_CODEX_HOME": str(root / f"{agent}-home"),
                    }
                    command = [
                        dispatch_command, "--agent", agent,
                        "--skill", "worktrail-sdd-workflow", "--args", seed,
                        "--cwd", str(root), "--write",
                    ]
                    if agent == "codex":
                        command.append("--no-inherit-codex-auth")
                    result = subprocess.run(command, env=environment, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(proof.read_text(), f"executed:{agent}:{seed}\n")
                    provider_argv = json.loads(argv_proof.read_text())
                    prompt = next(arg for arg in provider_argv if "worktrail-sdd-workflow" in arg)
                    self.assertIn("[WORKTRAIL INTERNAL DISPATCH]", prompt)
                    self.assertIn(seed, prompt)
                    expected_argv = {
                        "claude": ["-p", prompt, "--permission-mode", "bypassPermissions"],
                        "codex": [
                            "exec", "--json", "-s", "danger-full-access",
                            "-C", str(root), prompt,
                        ],
                        "opencode": [
                            "run", "--format", "json", "--dir", str(root),
                            "--auto", prompt,
                        ],
                    }
                    self.assertEqual(provider_argv, expected_argv[agent])

                    blocked = subprocess.run(
                        command,
                        env={**environment, "WORKTRAIL_SKILL_DISPATCH_DEPTH": "1"},
                        capture_output=True, text=True,
                    )
                    self.assertEqual(blocked.returncode, 2)
                    self.assertIn("blocked_internal_dispatch_recursion", blocked.stderr)

                    unseeded = subprocess.run(
                        [*command[:6], "handoff:20260812-090245", *command[7:]],
                        env={
                            **environment,
                            "FAKE_INTERNAL_DISPATCH_EXPECTED": "handoff:20260812-090245",
                        },
                        capture_output=True, text=True,
                    )
                    self.assertEqual(unseeded.returncode, 6)
                    self.assertEqual(proof.read_text(), f"unseeded:{agent}\n")


if __name__ == "__main__":
    unittest.main()
