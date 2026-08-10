#!/usr/bin/env python3
"""Build provider-preserving commands for dispatching an installed skill."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Sequence

SUPPORTED_AGENTS = ("claude", "codex", "opencode")
_SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


def _validate_skill_name(name: str) -> str:
    if not _SKILL_NAME.fullmatch(name):
        raise ValueError(f"invalid skill name: {name!r}")
    return name


def _prompt(agent: str, skill: str, args: str) -> str:
    if agent in {"claude", "opencode"}:
        return f"/{skill} {args}".rstrip()
    return f"Use the installed skill {skill!r}. Execute it with these arguments: {args}".rstrip()


def build_command(agent: str, skill: str, args: str = "", *, model: str | None = None,
                  cwd: str | None = None, write: bool = False,
                  add_dirs: Sequence[str] = (),
                  extra_args: Sequence[str] = ()) -> list[str]:
    """Return an argv list that preserves the requested provider identity.

    `cwd` targets a directory (typically a task worktree) without relocating the
    calling session. Only `codex` and `opencode` expose a working-root flag
    (`-C` / `--dir`); `claude` has none, so for every agent the caller must also
    launch the child with that directory as its process cwd (`main` does). The
    flag is still passed where it exists because codex derives its
    `workspace-write` sandbox root from the working root, not from process cwd.

    `write` opts into the permissions a skill needs to author files headlessly.
    It is opt-in because granting them by default would silently widen every
    existing dispatch. codex already carries `-s workspace-write` and so needs
    nothing extra; `claude` and `opencode` are otherwise unable to write without
    an interactive approval that a headless run has no channel to answer, which
    strands the spawn instead of failing it.

    `add_dirs` grants Codex additional writable roots alongside `cwd`. It is
    intentionally explicit because these paths may contain run records,
    sibling worktrees, or other state outside the target checkout.
    """
    if agent not in SUPPORTED_AGENTS:
        raise ValueError(f"unsupported agent: {agent!r}")
    skill = _validate_skill_name(skill)
    prompt = _prompt(agent, skill, args)
    if agent == "claude":
        command = ["claude", "-p", prompt]
        if write:
            command += ["--permission-mode", "bypassPermissions"]
        if model:
            command += ["--model", model]
    elif agent == "opencode":
        command = ["opencode", "run", "--format", "json"]
        if cwd:
            command += ["--dir", cwd]
        if write:
            command.append("--auto")
        if model:
            command += ["--model", model]
        command.append(prompt)
    else:
        command = ["codex", "exec", "--json", "-s", "workspace-write"]
        if cwd:
            command += ["-C", cwd]
        for directory in add_dirs:
            command += ["--add-dir", directory]
        if model:
            command += ["--model", model]
        command.append(prompt)
    return command + list(extra_args)


def resolve_codex_home(codex_home_override: str | None) -> str:
    """Resolve the CODEX_HOME that will actually govern a Codex child process:
    an explicit override (flag or WORKTRAIL_CODEX_HOME, already merged by the
    caller) wins, then the inherited CODEX_HOME, then Codex's own conventional
    default."""
    return (
        codex_home_override
        or os.environ.get("CODEX_HOME")
        or os.path.join(os.path.expanduser("~"), ".codex")
    )


def codex_home_write_remediation(path: str) -> str | None:
    """Return a remediation message if the nested Codex app-server would not
    be able to write to `path`, or None if it can. Checks directory
    existence and write-permission bits on the nearest existing ancestor
    only -- never opens or reads any file under `path`, so credentials
    already stored there are never probed or exposed."""
    probe = path.rstrip("/") or path
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    if not probe or not os.access(probe, os.W_OK):
        return (
            f"CODEX_HOME '{path}' is not writable by the nested Codex app-server "
            f"(nearest existing directory '{probe or path}' denies write access).\n"
            "Set WORKTRAIL_CODEX_HOME to a persistent writable directory (for "
            "example ~/.worktrail/codex-home) or pass --codex-home <path>."
        )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True, choices=SUPPORTED_AGENTS)
    parser.add_argument("--skill", required=True)
    parser.add_argument("--args", default="")
    parser.add_argument("--model")
    parser.add_argument(
        "--cwd",
        help="run the skill against this directory (e.g. a task worktree) "
             "without relocating the calling session",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="grant the child the permissions needed to author files headlessly",
    )
    parser.add_argument(
        "--add-dir",
        action="append",
        default=[],
        metavar="DIR",
        help="additional directory Codex may write alongside --cwd (repeatable)",
    )
    parser.add_argument(
        "--codex-home",
        help="override CODEX_HOME for a Codex child process (or use WORKTRAIL_CODEX_HOME)",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parsed = parser.parse_args(argv)
    command = build_command(
        parsed.agent, parsed.skill, parsed.args, model=parsed.model,
        cwd=parsed.cwd, write=parsed.write, add_dirs=parsed.add_dir,
    )
    if parsed.dry_run:
        print(json.dumps(command) if parsed.json else " ".join(command))
        return 0
    if parsed.cwd and not os.path.isdir(parsed.cwd):
        # Fail loudly: a child launched in the wrong directory authors artifacts
        # into the wrong tree, which reads as a successful run to the caller.
        print(f"--cwd '{parsed.cwd}' is not a directory", file=sys.stderr)
        return 1
    codex_home = parsed.codex_home or os.environ.get("WORKTRAIL_CODEX_HOME")
    child_env = None
    if parsed.agent == "codex" and codex_home:
        child_env = os.environ.copy()
        child_env["CODEX_HOME"] = codex_home
    if parsed.agent == "codex":
        remediation = codex_home_write_remediation(resolve_codex_home(codex_home))
        if remediation:
            print(remediation, file=sys.stderr)
            return 1
    run_kwargs = {"check": False}
    if child_env is not None:
        run_kwargs["env"] = child_env
    if parsed.cwd:
        run_kwargs["cwd"] = parsed.cwd
    return subprocess.run(command, **run_kwargs).returncode


if __name__ == "__main__":
    raise SystemExit(main())
