#!/usr/bin/env python3
"""Build provider-preserving commands for dispatching an installed skill."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

SUPPORTED_AGENTS = ("claude", "codex", "opencode")
_SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_DEFAULT_WORKTRAIL_CODEX_HOME = "~/.worktrail/codex-home"
_CODEX_AUTH_FILE = "auth.json"
_MAX_CODEX_AUTH_BYTES = 1024 * 1024
_CHATGPT_LOGIN_STATUS = "Logged in using ChatGPT"
_INTERNAL_SKILLS = frozenset({"worktrail-sdd-workflow"})
_DISPATCH_DEPTH_ENV = "WORKTRAIL_SKILL_DISPATCH_DEPTH"
_MAX_DISPATCH_DEPTH = 1


def _validate_skill_name(name: str) -> str:
    if not _SKILL_NAME.fullmatch(name):
        raise ValueError(f"invalid skill name: {name!r}")
    return name


def _prompt(agent: str, skill: str, args: str) -> str:
    if skill in _INTERNAL_SKILLS:
        invocation = f"/{skill} {args}".rstrip()
        authorization = (
            "[WORKTRAIL INTERNAL DISPATCH]\n"
            "This invocation was created by worktrail-skill-dispatch after the "
            "worktrail-go front door selected the executor. Execute the installed "
            f"skill directly with these arguments: {args}\n"
            "Do not invoke worktrail-go again. The executor's route:X guard remains "
            "authoritative; if the arguments are not sufficient, stop with an "
            "actionable error.\n"
            f"Invocation: {invocation}"
        )
        if agent in {"claude", "opencode"}:
            # Keep the native slash invocation first so those hosts resolve the
            # skill before passing the adapter authorization through to it.
            return f"{invocation}\n\n{authorization}"
        return authorization
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
    flag is still passed where it exists because codex derives its sandbox root
    from the working root, not from process cwd.

    `write` opts into the permissions a skill needs to author files headlessly.
    It is opt-in because granting them by default would silently widen every
    existing dispatch. Codex worker dispatches use
    `-s danger-full-access` so local integration tests can bind loopback
    sockets. `claude` and
    `opencode` are otherwise unable to write without
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
        command = ["codex", "exec", "--json", "-s", "danger-full-access"]
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


def default_worktrail_codex_home() -> str:
    """Return the isolated, persistent home used for an automatic child."""
    return os.path.expanduser(_DEFAULT_WORKTRAIL_CODEX_HOME)


def select_codex_home(codex_home_override: str | None) -> tuple[str, bool]:
    """Choose a child home without inheriting a read-only parent home.

    Explicit overrides remain fail-closed.  An inherited ``CODEX_HOME`` is
    retained when writable, but a sandboxed parent commonly exposes a read-only
    value; in that case Worktrail uses its own persistent home automatically.
    The boolean identifies an automatic choice for diagnostics and tests.
    """
    explicit = codex_home_override or os.environ.get("WORKTRAIL_CODEX_HOME")
    if explicit:
        return explicit, False
    inherited = os.environ.get("CODEX_HOME")
    if inherited and codex_home_write_remediation(inherited) is None:
        return inherited, False
    return default_worktrail_codex_home(), True


def ensure_codex_home(path: str) -> None:
    """Create the child home with private permissions, without copying state."""
    home = Path(path).expanduser()
    if home.is_symlink():
        raise OSError(f"Codex child home '{home}' must not be a symlink")
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    home.chmod(0o700)


def resolve_parent_codex_home() -> Path:
    """Resolve the parent home whose authenticated session may be inherited."""
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def _is_chatgpt_login_status(status: subprocess.CompletedProcess[str]) -> bool:
    """Recognize the exact status line without relaying captured diagnostics."""
    if status.returncode != 0:
        return False
    lines = [
        line.strip()
        for output in (status.stdout, status.stderr)
        for line in output.splitlines()
    ]
    return _CHATGPT_LOGIN_STATUS in lines


def _read_private_regular_file(path: Path) -> bytes:
    """Read a bounded, owner-only regular file without following symlinks."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise OSError(f"required Codex authentication file is unavailable: {path.name}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(f"Codex authentication source must be a regular file: {path.name}")
        if metadata.st_uid != os.geteuid():
            raise OSError(f"Codex authentication source is not owned by the current user: {path.name}")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise OSError(f"Codex authentication source permissions must be 0600: {path.name}")
        if metadata.st_size > _MAX_CODEX_AUTH_BYTES:
            raise OSError(f"Codex authentication source is unexpectedly large: {path.name}")
        chunks: list[bytes] = []
        remaining = _MAX_CODEX_AUTH_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > _MAX_CODEX_AUTH_BYTES:
            raise OSError(f"Codex authentication source is unexpectedly large: {path.name}")
        return data
    finally:
        os.close(descriptor)


def _atomic_private_write(path: Path, data: bytes) -> None:
    """Replace one private child-home file without exposing partial contents."""
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def inherit_codex_chatgpt_auth(parent_home: Path, child_home: Path) -> None:
    """Copy a verified file-backed ChatGPT session into a private child home."""
    if parent_home.is_symlink():
        raise OSError("parent CODEX_HOME must not be a symlink")
    if child_home.is_symlink():
        raise OSError("Codex child home must not be a symlink")
    status = subprocess.run(
        ["codex", "login", "status"],
        cwd=parent_home,
        env={**os.environ, "CODEX_HOME": str(parent_home)},
        capture_output=True,
        text=True,
        check=False,
    )
    if not _is_chatgpt_login_status(status):
        raise OSError(
            "parent Codex is not authenticated with ChatGPT; run 'codex login' "
            "or omit --inherit-codex-auth"
        )
    auth = _read_private_regular_file(parent_home / _CODEX_AUTH_FILE)
    _atomic_private_write(child_home / _CODEX_AUTH_FILE, auth)
    _atomic_private_write(
        child_home / "config.toml",
        b'cli_auth_credentials_store = "file"\n',
    )


def _codex_skill_roots() -> list[Path]:
    """Find Worktrail skill trees available to the invoking installation."""
    roots: list[Path] = []
    configured = os.environ.get("WORKTRAIL_SKILL_ROOT")
    if configured:
        roots.append(Path(configured).expanduser())

    # Editable/source installs have the plugin skills beside this module.
    source_root = Path(__file__).resolve().parents[3] / "skills"
    roots.append(source_root)

    # Installed plugins keep the skill tree under the parent Codex home.  Only
    # skill documentation is linked; auth/config files are never copied.
    parent_home = os.environ.get("CODEX_HOME")
    if parent_home:
        roots.extend(sorted(
            (Path(parent_home).expanduser() / "plugins/cache/worktrail/worktrail").glob("*/skills"),
            reverse=True,
        ))
    roots.extend(sorted(
        (Path.home() / ".codex/plugins/cache/worktrail/worktrail").glob("*/skills"),
        reverse=True,
    ))
    return roots


def bootstrap_codex_skills(codex_home: str, skill: str) -> bool:
    """Expose the installed Worktrail skills in an isolated Codex home.

    Codex discovers skills below ``CODEX_HOME/skills``.  A fresh child home
    therefore needs links to the plugin's skill directories.  Worktrail-owned
    symlinks are refreshed on every dispatch because plugin-cache versions and
    source worktrees are replaceable.  Real files and directories are
    preserved, so this remains safe for a user-maintained child home.
    """
    source_root = next((root for root in _codex_skill_roots()
                        if (root / skill).is_dir()), None)
    if source_root is None:
        return False
    destination_root = Path(codex_home).expanduser() / "skills"
    destination_root.mkdir(parents=True, exist_ok=True)
    for source in source_root.iterdir():
        destination = destination_root / source.name
        if destination.is_symlink():
            if destination.readlink() == source:
                continue
            destination.unlink()
        if not destination.exists():
            destination.symlink_to(source, target_is_directory=source.is_dir())
    return (destination_root / skill).exists()


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
    parser.add_argument(
        "--no-inherit-codex-auth",
        action="store_true",
        help="keep the Codex child isolated instead of inheriting the parent's verified ChatGPT session",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parsed = parser.parse_args(argv)
    if parsed.no_inherit_codex_auth and parsed.agent != "codex":
        parser.error("--no-inherit-codex-auth is only valid with --agent codex")
    try:
        dispatch_depth = int(os.environ.get(_DISPATCH_DEPTH_ENV, "0"))
    except ValueError:
        dispatch_depth = _MAX_DISPATCH_DEPTH
    if parsed.skill in _INTERNAL_SKILLS and dispatch_depth >= _MAX_DISPATCH_DEPTH:
        print(
            "blocked_internal_dispatch_recursion: worktrail-skill-dispatch refused "
            f"to launch {parsed.skill!r} at depth {dispatch_depth}; the internal "
            "executor appears to have re-entered worktrail-go instead of honoring "
            "the adapter dispatch. Inspect the child transcript and preserve the "
            "original handoff:<id> route:<X> arguments.",
            file=sys.stderr,
        )
        return 2
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
    codex_home = None
    child_env = os.environ.copy()
    if parsed.skill in _INTERNAL_SKILLS:
        child_env[_DISPATCH_DEPTH_ENV] = str(dispatch_depth + 1)
    if parsed.agent == "codex":
        codex_home, automatic_home = select_codex_home(parsed.codex_home)
        remediation = codex_home_write_remediation(resolve_codex_home(codex_home))
        if remediation:
            print(remediation, file=sys.stderr)
            return 1
        try:
            ensure_codex_home(codex_home)
            if not parsed.no_inherit_codex_auth:
                inherit_codex_chatgpt_auth(
                    resolve_parent_codex_home(), Path(codex_home).expanduser()
                )
            if not bootstrap_codex_skills(codex_home, parsed.skill):
                print(
                    f"Worktrail skill '{parsed.skill}' was not found for the Codex child. "
                    "Set WORKTRAIL_SKILL_ROOT to the installed Worktrail skills directory.",
                    file=sys.stderr,
                )
                return 1
        except OSError as exc:
            print(
                f"blocked_external_dependency: could not prepare authenticated "
                f"Codex child home: {exc}",
                file=sys.stderr,
            )
            return 1
        child_env["CODEX_HOME"] = codex_home
        if automatic_home:
            print(f"Using automatic Worktrail Codex home: {codex_home}", file=sys.stderr)
    run_kwargs = {"check": False}
    if parsed.skill in _INTERNAL_SKILLS or parsed.agent == "codex":
        run_kwargs["env"] = child_env
    if parsed.cwd:
        run_kwargs["cwd"] = parsed.cwd
    return subprocess.run(command, **run_kwargs).returncode


if __name__ == "__main__":
    raise SystemExit(main())
