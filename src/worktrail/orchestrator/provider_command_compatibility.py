"""Validate Worktrail's generated provider commands without launching model work."""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from worktrail.drain.drain import build_command as build_drain_command
from worktrail.router.cluster_detect import _AGENT_VERIFY_CMD
from worktrail.router.skill_dispatch import build_command as build_skill_command
from worktrail.runtime.selection import Cell

from .spawnlib import build_cmd as build_spawn_command


PROVIDERS = ("claude", "codex", "opencode")
DEFAULT_TIMEOUT = 10.0
_PROMPT = "WORKTRAIL_PROVIDER_COMPATIBILITY_PROBE"


@dataclass(frozen=True)
class ProbeResult:
    provider: str
    surface: str
    ok: bool
    returncode: int | None
    detail: str


def command_matrix() -> Dict[Tuple[str, str], List[str]]:
    """Return representative argv for every built-in headless dispatch surface."""
    cwd = str(Path.cwd())
    commands: Dict[Tuple[str, str], List[str]] = {}
    for provider in PROVIDERS:
        commands[(provider, "cluster-detect")] = _AGENT_VERIFY_CMD[provider](_PROMPT)
        commands[(provider, "drain")] = build_drain_command(provider, [])
        commands[(provider, "skill-dispatch")] = build_skill_command(
            provider,
            "worktrail-go",
            "auto",
            model="worktrail-compat-probe",
            cwd=cwd,
            write=True,
        )
        commands[(provider, "spawnlib")] = build_spawn_command(
            _PROMPT,
            Cell(
                target=provider,
                harness=provider,
                model="worktrail-compat-probe",
                effort="high",
                pool="subscription",
            ),
            output_last_message="/dev/null",
        )
    return commands


def _detail(stdout: str, stderr: str) -> str:
    combined = "\n".join(part.strip() for part in (stderr, stdout) if part.strip())
    return combined or "provider returned no help output"


def probe_all(timeout: float = DEFAULT_TIMEOUT) -> List[ProbeResult]:
    """Ask each installed provider parser to validate generated argv via ``--help``."""
    results: List[ProbeResult] = []
    for (provider, surface), command in sorted(command_matrix().items()):
        try:
            completed = subprocess.run(
                [*command, "--help"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            results.append(ProbeResult(provider, surface, False, None, str(exc)))
            continue
        detail = _detail(completed.stdout, completed.stderr)
        results.append(
            ProbeResult(provider, surface, completed.returncode == 0, completed.returncode, detail)
        )
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate every built-in Claude, Codex, and OpenCode command against "
            "the installed CLI parser without authentication or model execution."
        )
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)

    results = probe_all(timeout=args.timeout)
    for result in results:
        if result.ok:
            print(f"PASS {result.provider}/{result.surface}")
        else:
            print(f"FAIL {result.provider}/{result.surface}: {result.detail}")
    passed = sum(result.ok for result in results)
    print(f"Provider command compatibility: {passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
