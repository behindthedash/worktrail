#!/usr/bin/env python3
"""Installed-wheel smoke for the drain provider launch contract.

The harness deliberately runs the installed package from a temporary directory,
with only a venv and synthetic provider runtimes on PATH.  Each provider stub
records its exact argv, waits briefly, writes a terminal marker, and exits.  The
caller must still own the foreground process when that marker appears.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--agent", required=True, choices=("claude", "codex", "opencode")
    )
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    args.output = args.output.resolve()
    result_path = args.output / "result.json"
    argv_path = args.output / "provider-argv.json"
    terminal_path = args.output / "terminal.json"

    with tempfile.TemporaryDirectory(prefix="worktrail-drain-smoke-") as raw_tmp:
        tmp = Path(raw_tmp)
        venv = tmp / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        python = venv / "bin" / "python"
        subprocess.run(
            [str(python), "-m", "pip", "install", str(args.wheel.resolve())],
            check=True,
        )

        fake_bin = tmp / "provider-bin"
        fake_bin.mkdir()
        _write_executable(fake_bin / "node", "#!/bin/sh\nexit 0\n")
        _write_executable(
            fake_bin / args.agent,
            "#!/usr/bin/env python3\n"
            "import json, os, sys, time\n"
            "from pathlib import Path\n"
            "Path(os.environ['WT_ARGV']).write_text(json.dumps(sys.argv))\n"
            "time.sleep(0.25)\n"
            "Path(os.environ['WT_TERMINAL']).write_text(json.dumps({'state': 'terminal'}))\n",
        )
        minimal_path = os.pathsep.join(
            (str(fake_bin), str(venv / "bin"), "/usr/bin", "/bin")
        )
        env = {
            "PATH": minimal_path,
            "WT_AGENT": args.agent,
            "WT_ARGV": str(argv_path),
            "WT_TERMINAL": str(terminal_path),
        }
        probe = r"""
import json, os, time
from pathlib import Path
from worktrail.drain.drain import build_command, run_one_shot, validate_agent_runtime

agent = os.environ["WT_AGENT"]
env = dict(os.environ)
missing_path = str(Path(env["PATH"].split(os.pathsep)[1]))
try:
    validate_agent_runtime(agent, {**env, "PATH": missing_path})
except RuntimeError as exc:
    missing_runtime_failure = str(exc)
else:
    raise AssertionError("missing provider runtime did not fail closed")

validate_agent_runtime(agent, env)
command = build_command(agent, [], go_repo="worktrail")
prompt = next(part for part in command if part.startswith("worktrail-go "))
assert prompt.startswith("worktrail-go worktrail auto")
assert "in the foreground" in prompt
assert "real final_status" in prompt
assert "PR checks are pending" in prompt

# The explicit template (the supported attended/custom launcher path) remains
# an argv substitution only; provider-specific flags are not injected.
attended = build_command(
    agent, ["--must-not-leak"], template="runner {prompt}", go_repo="worktrail"
)
assert attended == ["runner", prompt]

started = time.monotonic()
outcome = run_one_shot(command, timeout=10, env=env)
elapsed = time.monotonic() - started
terminal = Path(os.environ["WT_TERMINAL"])
assert outcome.exit_code == 0, (outcome.stdout, outcome.stderr)
assert terminal.is_file(), "provider exited before terminal completion marker"
assert elapsed >= 0.20, f"launcher returned early after {elapsed:.3f}s"
print(json.dumps({
    "agent": agent,
    "command": command,
    "attended_template": attended,
    "elapsed_seconds": elapsed,
    "foreground_terminal_owned": True,
    "missing_runtime_failure": missing_runtime_failure,
    "installed_package": __import__("worktrail").__file__,
}, sort_keys=True))
"""
        completed = subprocess.run(
            [str(python), "-I", "-c", probe],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
        )
        (args.output / "stdout.log").write_text(completed.stdout, encoding="utf-8")
        (args.output / "stderr.log").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode != 0:
            result_path.write_text(
                json.dumps(
                    {
                        "agent": args.agent,
                        "status": "failed",
                        "exit_code": completed.returncode,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            sys.stderr.write(completed.stderr)
            return completed.returncode
        result = json.loads(completed.stdout)
        result["status"] = "passed"
        result["provider_argv"] = json.loads(argv_path.read_text(encoding="utf-8"))
        result_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
