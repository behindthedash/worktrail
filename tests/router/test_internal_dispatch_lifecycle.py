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
from worktrail.workqueue import decisions as decisions_mod

from ._subprocess_timeouts import subprocess_timeout_s

_FAKE_AGENT = Path(__file__).with_name("fake_internal_dispatch_agent.py")


class InternalDispatchLifecycleTests(unittest.TestCase):
    def _run_seeded_lifecycle(
        self,
        root: Path,
        agent: str,
        outcome: str,
        *,
        wrapper: bool = False,
    ) -> tuple[int, str, int | None, int | None]:
        bin_dir = root / "bin"
        bin_dir.mkdir()
        shim = bin_dir / agent
        shim.write_text(
            f'#!/bin/sh\nexec {sys.executable} {_FAKE_AGENT} {agent} "$@"\n'
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
                shutil.which("worktrail-go-seed"),
                "--repo",
                str(root),
                "--base",
                "main",
                "--route",
                "F",
                "--spec",
                "handoff:lifecycle",
                "--run",
                str(run),
                "--brief",
                str(brief),
                "--agent",
                agent,
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        proof = root / "proof"
        ready = root / "ready"
        wrapper_pid = root / "wrapper-pid"
        parent_home = root / "codex-parent-home"
        environment = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_INTERNAL_DISPATCH_PROOF": str(proof),
            "FAKE_INTERNAL_DISPATCH_EXPECTED": seed,
            "FAKE_INTERNAL_DISPATCH_LIFECYCLE": outcome,
            "FAKE_INTERNAL_DISPATCH_OWNERSHIP": ownership,
            "FAKE_INTERNAL_DISPATCH_READY": str(ready),
            "FAKE_INTERNAL_DISPATCH_WRAPPER_PID": str(wrapper_pid),
            "WORKTRAIL_SKILL_DISPATCH_DEPTH": "0",
            "WORKTRAIL_SKILL_ROOT": str(root / "skills"),
            "WORKTRAIL_CODEX_HOME": str(root / "codex-home"),
        }
        if agent == "codex":
            parent_home.mkdir()
            auth = parent_home / "auth.json"
            auth.write_text("fake-chatgpt-auth\n")
            auth.chmod(0o600)
            environment["CODEX_HOME"] = str(parent_home)
        if wrapper:
            environment["FAKE_INTERNAL_DISPATCH_WRAPPER"] = "1"
        command = [
            shutil.which("worktrail-skill-dispatch"),
            "--agent",
            agent,
            "--skill",
            "worktrail-sdd-workflow",
            "--args",
            seed,
            "--cwd",
            str(root),
            "--write",
        ]
        if agent == "codex":
            command.append("--no-inherit-codex-auth")
        process = subprocess.Popen(command, env=environment)
        if outcome == "interrupted":
            deadline = time.monotonic() + subprocess_timeout_s()
            while (
                not ready.exists()
                and process.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            self.assertTrue(
                ready.exists(), "fake seeded child never became interruptible"
            )
            if wrapper:
                deadline = time.monotonic() + subprocess_timeout_s()
                while (
                    not wrapper_pid.exists()
                    and process.poll() is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                self.assertTrue(
                    wrapper_pid.exists(), "fake seeded wrapper never published its pid"
                )
                target_pid = int(wrapper_pid.read_text())
            else:
                target_pid = int(ready.read_text())
            os.kill(target_pid, signal.SIGTERM)
        return (
            process.wait(timeout=subprocess_timeout_s()),
            run.read_text(),
            int(ready.read_text()) if ready.exists() else None,
            int(wrapper_pid.read_text()) if wrapper_pid.exists() else None,
        )

    def test_seeded_child_mutates_only_the_exact_parent_run_record(self):
        for agent in skill_dispatch.SUPPORTED_AGENTS:
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                returncode, record, _, _ = self._run_seeded_lifecycle(
                    root,
                    agent,
                    "complete",
                )
                self.assertEqual(returncode, 0)
                self.assertEqual(
                    list((root / "runs").glob("*.yaml")),
                    [root / "runs/parent.yaml"],
                )
                self.assertIn("dispatch_id: go-parent-dispatch-owner", record)
                self.assertIn("- fake-child", record)
                self.assertIn("final_status: investigation_complete", record)

    def test_seeded_child_records_terminal_state_before_nonzero_exit(self):
        for agent in skill_dispatch.SUPPORTED_AGENTS:
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as tmp:
                returncode, record, _, _ = self._run_seeded_lifecycle(
                    Path(tmp),
                    agent,
                    "nonzero",
                )
                self.assertEqual(returncode, 9)
                self.assertIn("dispatch_id: go-parent-dispatch-owner", record)
                self.assertIn("final_status: failed_recoverable", record)
                self.assertIn("seeded child exited nonzero", record)

    def test_seeded_child_records_terminal_state_when_interrupted(self):
        for agent in skill_dispatch.SUPPORTED_AGENTS:
            for wrapper in (False, True):
                label = "wrapper" if wrapper else "child"
                with (
                    self.subTest(agent=agent, interruption=label),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    returncode, record, child_pid, wrapper_pid = (
                        self._run_seeded_lifecycle(
                            Path(tmp),
                            agent,
                            "interrupted",
                            wrapper=wrapper,
                        )
                    )
                    self.assertEqual(returncode, 130)
                    self.assertIn("dispatch_id: go-parent-dispatch-owner", record)
                    self.assertIn("final_status: failed_recoverable", record)
                    self.assertIn("seeded child interrupted", record)
                    self.assertIsNotNone(child_pid)
                    with self.assertRaises(ProcessLookupError):
                        os.kill(child_pid, 0)
                    if wrapper:
                        self.assertIsNotNone(wrapper_pid)
                        with self.assertRaises(ProcessLookupError):
                            os.kill(wrapper_pid, 0)

    def test_handoff_route_reaches_each_provider_once_without_front_door_redirect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            for agent in skill_dispatch.SUPPORTED_AGENTS:
                shim = bin_dir / agent
                shim.write_text(
                    f'#!/bin/sh\nexec {sys.executable} {_FAKE_AGENT} {agent} "$@"\n'
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
                        "--agent",
                        agent,
                        "--skill",
                        "worktrail-sdd-workflow",
                        "--args",
                        "handoff:20260812-083302 route:F",
                        "--cwd",
                        str(root),
                        "--write",
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
        self.assertIsNotNone(
            dispatch_command, "worktrail-skill-dispatch is not installed"
        )

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
                    f'#!/bin/sh\nexec {sys.executable} {_FAKE_AGENT} {agent} "$@"\n'
                )
                shim.chmod(shim.stat().st_mode | stat.S_IEXEC)

            for agent in skill_dispatch.SUPPORTED_AGENTS:
                with self.subTest(agent=agent):
                    seed = subprocess.run(
                        [
                            seed_command,
                            "--repo",
                            str(root),
                            "--base",
                            "main",
                            "--route",
                            "F",
                            "--spec",
                            "handoff:20260812-090245",
                            "--run",
                            str(run),
                            "--brief",
                            str(brief),
                            "--agent",
                            agent,
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
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
                        dispatch_command,
                        "--agent",
                        agent,
                        "--skill",
                        "worktrail-sdd-workflow",
                        "--args",
                        seed,
                        "--cwd",
                        str(root),
                        "--write",
                    ]
                    if agent == "codex":
                        command.append("--no-inherit-codex-auth")
                    result = subprocess.run(
                        command,
                        check=False,
                        env=environment,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(proof.read_text(), f"executed:{agent}:{seed}\n")
                    provider_argv = json.loads(argv_proof.read_text())
                    prompt = next(
                        arg for arg in provider_argv if "worktrail-sdd-workflow" in arg
                    )
                    self.assertIn("[WORKTRAIL INTERNAL DISPATCH]", prompt)
                    self.assertIn(seed, prompt)
                    expected_argv = {
                        "claude": [
                            "-p",
                            prompt,
                            "--permission-mode",
                            "bypassPermissions",
                        ],
                        "codex": [
                            "exec",
                            "--json",
                            "-s",
                            "danger-full-access",
                            "-C",
                            str(root),
                            prompt,
                        ],
                        "opencode": [
                            "run",
                            "--format",
                            "json",
                            "--dir",
                            str(root),
                            "--auto",
                            prompt,
                        ],
                    }
                    self.assertEqual(provider_argv, expected_argv[agent])

                    blocked = subprocess.run(
                        command,
                        check=False,
                        env={**environment, "WORKTRAIL_SKILL_DISPATCH_DEPTH": "1"},
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(blocked.returncode, 2)
                    self.assertIn("blocked_internal_dispatch_recursion", blocked.stderr)

                    unseeded = subprocess.run(
                        [*command[:6], "handoff:20260812-090245", *command[7:]],
                        check=False,
                        env={
                            **environment,
                            "FAKE_INTERNAL_DISPATCH_EXPECTED": "handoff:20260812-090245",
                        },
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(unseeded.returncode, 6)
                    self.assertEqual(proof.read_text(), f"unseeded:{agent}\n")


class DecisionResumeLifecycleTests(unittest.TestCase):
    """The decision boundary survives the real adapter/subprocess process
    boundary: an answered record lets every provider's child receive the
    exact `decision:<id>` token; an open one fails closed before any spawn,
    and presentation prints the same envelope JSON regardless of host."""

    DECISION_ID = "dec-lifecycle-0001"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.queue_base = self.root / "queue"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        recorder = self.root / "recorder.py"
        recorder.write_text(
            "import json, os, sys\n"
            "proof = os.environ['DECISION_RECORDER_PROOF']\n"
            "with open(proof, 'w') as fh:\n"
            "    json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd()}, fh)\n"
        )
        for agent in skill_dispatch.SUPPORTED_AGENTS:
            shim = bin_dir / agent
            shim.write_text(
                f'#!/bin/sh\nexec {sys.executable} {recorder} {agent} "$@"\n'
            )
            shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
        skills = self.root / "skills" / "openspec-propose"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text("---\nname: openspec-propose\n---\n")
        self.environment = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "PYTHONPATH": str(Path(skill_dispatch.__file__).resolve().parents[2]),
            "WORK_QUEUE_DIR": str(self.queue_base),
            "WORKTRAIL_SKILL_ROOT": str(self.root / "skills"),
            "WORKTRAIL_CODEX_HOME": str(self.root / "codex-home"),
        }

    def _seed(self, *, answer_it: bool):
        result = decisions_mod.ask(
            "Which scope should this request take?",
            background="The shipped spec already covers the requested scope.",
            why="Scope direction is a product call.",
            context="verify() confirmed Implemented status and tracked files.",
            options=[
                "extend: continue the existing spec",
                "redirect: choose different scope",
            ],
            source="check_spec_collision",
            repo=str(self.root),
            subject="spec-a",
            decision_id=self.DECISION_ID,
            queue_base=self.queue_base,
        )
        assert result["status"] == "created", result
        if answer_it:
            answered = decisions_mod.answer(
                self.DECISION_ID,
                "extend: continue the existing spec",
                queue_base=self.queue_base,
            )
            assert answered["status"] == "answered", answered

    def _dispatch_command(self, agent, extra=()):
        command = [
            sys.executable,
            "-m",
            "worktrail.router.skill_dispatch",
            "--agent",
            agent,
            "--skill",
            "openspec-propose",
            "--args",
            "route:C spec:demo",
            "--cwd",
            str(self.root),
            *extra,
        ]
        if agent == "codex":
            command.append("--no-inherit-codex-auth")
        return command

    def test_answered_decision_resumes_each_provider_with_the_exact_id(self):
        self._seed(answer_it=True)
        token = f"decision:{self.DECISION_ID}"
        for agent in skill_dispatch.SUPPORTED_AGENTS:
            with self.subTest(agent=agent):
                proof = self.root / f"{agent}.resume-proof.json"
                env = {**self.environment, "DECISION_RECORDER_PROOF": str(proof)}
                result = subprocess.run(
                    self._dispatch_command(
                        agent, ["--resume-decision", self.DECISION_ID]
                    ),
                    check=False,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                recorded = json.loads(proof.read_text())
                prompts = [a for a in recorded["argv"] if token in a]
                self.assertEqual(len(prompts), 1)
                self.assertTrue(prompts[0].endswith(token))
                self.assertNotIn(token + "-", prompts[0])

    def test_open_decision_fails_closed_before_any_child_spawns(self):
        self._seed(answer_it=False)
        for agent in skill_dispatch.SUPPORTED_AGENTS:
            with self.subTest(agent=agent):
                proof = self.root / f"{agent}.blocked-proof.json"
                env = {**self.environment, "DECISION_RECORDER_PROOF": str(proof)}
                result = subprocess.run(
                    self._dispatch_command(
                        agent, ["--resume-decision", self.DECISION_ID]
                    ),
                    check=False,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("blocked_pending_decision", result.stderr)
                envelope = json.loads(result.stdout)
                self.assertEqual(envelope["schema"], "worktrail.pending-decision")
                self.assertEqual(envelope["status"], "open")
                self.assertEqual(envelope["decision_id"], self.DECISION_ID)
                self.assertFalse(proof.exists(), "child spawned despite open decision")

    def test_presentation_prints_one_envelope_for_every_host(self):
        self._seed(answer_it=True)
        for agent in skill_dispatch.SUPPORTED_AGENTS:
            with self.subTest(agent=agent):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "worktrail.router.skill_dispatch",
                        "--agent",
                        agent,
                        "--skill",
                        "openspec-propose",
                        "--present-decision",
                        self.DECISION_ID,
                    ],
                    check=False,
                    env=self.environment,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                envelope = json.loads(result.stdout)
                self.assertEqual(envelope["decision_id"], self.DECISION_ID)
                self.assertEqual(envelope["status"], "answered")
                self.assertIn("extend: continue the existing spec", envelope["answer"])


if __name__ == "__main__":
    unittest.main()
