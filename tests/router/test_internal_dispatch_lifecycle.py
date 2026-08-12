"""End-to-end regression for adapter-to-internal-executor dispatch."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worktrail.router import skill_dispatch


_FAKE_AGENT = Path(__file__).with_name("fake_internal_dispatch_agent.py")


class InternalDispatchLifecycleTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
