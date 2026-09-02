#!/usr/bin/env python3
"""Build provider-preserving commands for dispatching an installed skill.

This module is the go/adapter boundary for the pending-user-decision
contract (`worktrail.pending-decision`, `workqueue/decisions.py`): an
attended host presents a guard's decision envelope through
`--present-decision` (the same versioned JSON regardless of provider), and
a resume dispatch is gated on the *exact* decision id via
`--resume-decision` — the child launches only when that exact record is
answered and live, and the id travels into the invocation verbatim as a
`decision:<id>` token. A decision that is open, superseded, or unknown
fails closed here (exit 2, nothing spawned) instead of letting a child
guess; an unresumable-but-known record's envelope is printed on stdout so
an unattended caller receives the structured pending result unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

from ..orchestrator import agent_capacity
from ..runtime.selection import Cell, NoExecutionTarget, select_cell

SUPPORTED_AGENTS = ("claude", "codex", "opencode")
_SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_DEFAULT_WORKTRAIL_CODEX_HOME = "~/.worktrail/codex-home"
_CODEX_AUTH_FILE = "auth.json"
_CHATGPT_LOGIN_STATUS = "Logged in using ChatGPT"
_INTERNAL_SKILLS = frozenset({"worktrail-sdd-workflow"})
_DISPATCH_DEPTH_ENV = "WORKTRAIL_SKILL_DISPATCH_DEPTH"
_MAX_DISPATCH_DEPTH = 1
# The bundled OpenSpec integration ships as Claude Code *commands*
# (commands/opsx/*.md), not Skills, so claude/opencode resolve them only
# under the plugin's own namespace prefix -- unlike a Skill's frontmatter
# `name:`, which those hosts match bare with no prefix. Live-verified
# 2026-08-24: bare `/opsx:propose` fails with "Unknown command" (exit 0, no
# artifacts written); `/worktrail:opsx:propose` is accepted.
_OPSX_COMMAND_PREFIX = "opsx:"
_OPSX_NAMESPACE = "worktrail:"
# Codex has no namespace-prefix mechanism -- it discovers skills by
# directory name under CODEX_HOME/skills, and the bundled OpenSpec
# integration's Codex-discoverable directories are named `openspec-propose`,
# `openspec-sync-specs`, etc, not the short `opsx:*` command name every
# caller passes uniformly across agents (`build_sync_command` in drain.py,
# `#openspec-authoring` in subagent-prompts.md). A raw `opsx:sync` bootstrap
# therefore looks for a directory that never exists.
_OPSX_CODEX_SKILL = {
    "opsx:propose": "openspec-propose",
    "opsx:explore": "openspec-explore",
    "opsx:update": "openspec-update-change",
    "opsx:sync": "openspec-sync-specs",
    "opsx:archive": "openspec-archive-change",
}


def _validate_skill_name(name: str) -> str:
    if not _SKILL_NAME.fullmatch(name):
        raise ValueError(f"invalid skill name: {name!r}")
    return name


def _namespaced_invocation_skill(agent: str, skill: str) -> str:
    """Return the skill name as the given host actually resolves it.

    Only the bundled `opsx:*` commands need this -- accept an
    already-namespaced value as a no-op so callers may pass either form.
    """
    if (
        agent in {"claude", "opencode"}
        and skill.startswith(_OPSX_COMMAND_PREFIX)
        and not skill.startswith(_OPSX_NAMESPACE)
    ):
        return f"{_OPSX_NAMESPACE}{skill}"
    return skill


def _codex_skill_name(skill: str) -> str:
    """Return the skill directory a Codex child actually has installed.

    Only the bundled `opsx:*` short names need translation -- everything
    else (worktrail-sdd-workflow, an already-resolved openspec-* name) is
    returned unchanged.
    """
    return _OPSX_CODEX_SKILL.get(skill, skill)


# --- pending-decision boundary (presentation + exact-id resume) ---------------

_DECISION_TOKEN_PREFIX = "decision:"


def _decision_helpers():
    """Best-effort import of the decision-envelope primitives. Returns
    `(load_decision_envelope, validate_decision_answer,
    parse_pending_decision_envelope)`, or `(None, None, None)` when
    `workqueue.decisions` cannot be imported -- decision handling degrades to
    a fail-closed refusal, never an exception."""
    try:
        from ..workqueue.decisions import (
            load_decision_envelope,
            parse_pending_decision_envelope,
            validate_decision_answer,
        )
    except Exception:  # noqa: BLE001 - boundary is additive, never fatal on import
        return None, None, None
    return (
        load_decision_envelope,
        validate_decision_answer,
        parse_pending_decision_envelope,
    )


def append_decision_token(args: str, decision_id: str) -> str:
    """Thread one exact decision id into an invocation's arguments.

    The token is the resume contract every provider sees identically: the
    executor consumes exactly this record via its own consume path. The id
    is appended verbatim -- never re-derived, never normalized.
    """
    token = f"{_DECISION_TOKEN_PREFIX}{decision_id}"
    return f"{args} {token}".strip() if args and args.strip() else token


def load_decision_for_boundary(decision_id: str) -> tuple[dict | None, str | None]:
    """Load one decision envelope for presentation or resume.

    Returns `(envelope, None)` or `(None|envelope, error)`. The requested id
    must match the record's stem exactly: a prefix or partial match is a
    different record, so it is refused instead of silently resumed.
    """
    did = (decision_id or "").strip()
    if not did:
        return None, "a decision id is required"
    load, _validate, parse = _decision_helpers()
    if load is None:
        return None, "decision-envelope primitives are unavailable"
    envelope = load(did)
    if envelope is None:
        return None, f"no decision record resolves exactly to {did!r}"
    if str(envelope.get("decision_id") or "") != did:
        return envelope, (
            f"requested decision id {did!r} does not exactly match record "
            f"{envelope.get('decision_id')!r}; refusing a prefix or partial match"
        )
    try:
        parse(envelope)
    except Exception as exc:  # noqa: BLE001 - refuse anything not fully readable
        return envelope, (
            f"record {did!r} is not a readable {envelope.get('schema', '')} "
            f"envelope: {exc}"
        )
    return envelope, None


def present_decision(decision_id: str) -> tuple[dict | None, str | None]:
    """Return the provider-neutral envelope an attended host presents.

    Works for any decision status -- presenting an *open* question is the
    attended use case. The printed value always round-trips
    `parse_pending_decision_envelope`, so every host renders the same
    structured contract rather than provider-specific prose.
    """
    envelope, error = load_decision_for_boundary(decision_id)
    if error:
        return None, error
    return envelope, None


def _stamp_presented(run_path: str | None, decision_id: str) -> None:
    """Best-effort `[presented]` hop on the run record's audit trail."""
    if not run_path:
        return
    try:
        from .run_record import record_decision_event

        record_decision_event(run_path, "presented", decision_id)
    except Exception as exc:  # noqa: BLE001 - audit stamp failure must not lose the envelope
        print(
            f"warning: could not stamp [presented] {decision_id} onto "
            f"run record {run_path}: {exc}",
            file=sys.stderr,
        )


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
        invocation_skill = _namespaced_invocation_skill(agent, skill)
        return f"/{invocation_skill} {args}".rstrip()
    invocation_skill = _codex_skill_name(skill)
    return f"Use the installed skill {invocation_skill!r}. Execute it with these arguments: {args}".rstrip()


# --- dispatch cell resolution (design D3: a single selector walks a tier row
# across targets in preference order) ------------------------------------


def _record_skipped_cells(run_path: str | None, skipped: list[dict]) -> None:
    """Best-effort audit stamp: append each capacity-gated cell `select_cell`
    walked past onto the run record's `skipped_cells` list. A stamping
    failure must never affect dispatch -- same degrade-to-warning posture as
    `_stamp_presented`."""
    if not run_path:
        return
    try:
        from .run_record import _load, _save

        path = Path(run_path)
        record = _load(path)
        entries = record.get("skipped_cells")
        if entries is None:
            entries = record["skipped_cells"] = []
        elif not isinstance(entries, list):
            return
        entries.extend(json.dumps(item, sort_keys=True) for item in skipped)
        _save(path, record)
    except Exception as exc:  # noqa: BLE001 - audit stamp failure must not lose dispatch
        print(
            f"warning: could not record skipped cells onto run record {run_path}: {exc}",
            file=sys.stderr,
        )


def select_dispatch_cell(
    routing: dict,
    *,
    tier: str | None = None,
    prefer: str | None = None,
    run_path: str | None = None,
    capacity_path=None,
) -> Cell:
    """Resolve the dispatch cell for the `front-door` role (design D3).

    `roles["front-door"]["tier"]` wins over `routing["default_tier"]`;
    `select_cell` then walks that tier row across `routing["targets"]` in
    preference order, `roles["front-door"]["prefer"]` (if set) moving its
    target to the front. Every cell it skips because `agent_capacity` gates
    it is recorded onto `run_path`'s run record (best-effort, never fatal to
    dispatch) so a capacity outage is visible without re-deriving it from the
    agent-capacity cache after the fact.
    """
    roles = routing.get("roles") or {}
    front_door = roles.get("front-door") or {}
    resolved_tier = tier or front_door.get("tier") or routing.get("default_tier")
    resolved_prefer = prefer or front_door.get("prefer")

    skipped: list[dict] = []

    def _capacity(target: str, model: str, *, now=None):
        try:
            agent_capacity.check(target, model, path=capacity_path, now=now)
        except agent_capacity.ProviderUnavailable as exc:
            skipped.append(
                {
                    "target": target,
                    "model": model,
                    "failure_class": exc.state.get("failure_class"),
                    "retry_after": exc.state.get("retry_after"),
                }
            )
            raise

    try:
        return select_cell(
            routing, resolved_tier, prefer=resolved_prefer, capacity=_capacity
        )
    finally:
        if skipped:
            _record_skipped_cells(run_path, skipped)


def build_command(
    agent: str,
    skill: str,
    args: str = "",
    *,
    model: str | None = None,
    cwd: str | None = None,
    write: bool = False,
    add_dirs: Sequence[str] = (),
    extra_args: Sequence[str] = (),
) -> list[str]:
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


def _validate_private_regular_file(path: Path) -> None:
    """Require an owner-only regular file, without following symlinks or
    reading its contents."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise OSError(
            f"required Codex authentication file is unavailable: {path.name}"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError(
            f"Codex authentication source must be a regular file: {path.name}"
        )
    if metadata.st_uid != os.geteuid():
        raise OSError(
            f"Codex authentication source is not owned by the current user: {path.name}"
        )
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise OSError(
            f"Codex authentication source permissions must be 0600: {path.name}"
        )


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
    """Link a verified file-backed ChatGPT session into a private child home.

    ``auth.json`` is symlinked, never copied: ChatGPT refresh tokens are
    single-use and rotate on every refresh, so a copy strands the rotated
    token in the child home while the parent (and every later copy) keeps
    presenting the consumed one -- ``401: Your refresh token has already been
    used``.  Codex writes ``auth.json`` in place (verified against codex-cli
    0.152.1), so the link keeps rotation landing in the one real file.
    """
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
    source = parent_home / _CODEX_AUTH_FILE
    _validate_private_regular_file(source)
    link = child_home / _CODEX_AUTH_FILE
    if link.is_symlink() or link.exists():
        # A stale per-spawn copy from the previous behavior holds a burned
        # token; replace it with the write-through link.
        link.unlink()
    link.symlink_to(source)
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
        roots.extend(
            sorted(
                (
                    Path(parent_home).expanduser() / "plugins/cache/worktrail/worktrail"
                ).glob("*/skills"),
                reverse=True,
            )
        )
    roots.extend(
        sorted(
            (Path.home() / ".codex/plugins/cache/worktrail/worktrail").glob("*/skills"),
            reverse=True,
        )
    )
    return roots


def bootstrap_codex_skills(codex_home: str, skill: str) -> bool:
    """Expose the installed Worktrail skills in an isolated Codex home.

    Codex discovers skills below ``CODEX_HOME/skills``.  A fresh child home
    therefore needs links to the plugin's skill directories.  Worktrail-owned
    symlinks are refreshed on every dispatch because plugin-cache versions and
    source worktrees are replaceable.  Real files and directories are
    preserved, so this remains safe for a user-maintained child home.

    ``skill`` may be a bundled `opsx:*` short name -- resolved to the real
    skill directory Codex needs (see `_codex_skill_name`) before searching.
    """
    skill = _codex_skill_name(skill)
    source_root = next(
        (root for root in _codex_skill_roots() if (root / skill).is_dir()), None
    )
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


def prepare_codex_child_environment(
    codex_home_override: str | None = None,
    *,
    inherit_auth: bool = True,
) -> tuple[dict[str, str], str, bool]:
    """Prepare a writable, provider-preserving environment for a Codex child.

    The skill adapter and the parallel orchestrator both launch Codex children.
    Keep home selection and authentication inheritance in one place so a direct
    orchestrator worker cannot accidentally inherit a read-only parent home.
    Skill bootstrapping remains the adapter's responsibility because workers do
    not discover Worktrail skills through ``CODEX_HOME``.
    """
    codex_home, automatic_home = select_codex_home(codex_home_override)
    remediation = codex_home_write_remediation(resolve_codex_home(codex_home))
    if remediation:
        raise OSError(remediation)
    ensure_codex_home(codex_home)
    if inherit_auth:
        inherit_codex_chatgpt_auth(
            resolve_parent_codex_home(), Path(codex_home).expanduser()
        )
    child_env = os.environ.copy()
    child_env["CODEX_HOME"] = codex_home
    return child_env, codex_home, automatic_home


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


def _run_command_with_sigterm_forwarding(
    command: list[str], run_kwargs: dict[str, object]
) -> int:
    """Run a provider child, forwarding wrapper SIGTERM and reaping it."""
    if isinstance(subprocess.run, mock.Mock):
        return subprocess.run(command, **run_kwargs).returncode  # noqa: PLW1510 -- run_kwargs already carries check
    child_kwargs = {key: value for key, value in run_kwargs.items() if key != "check"}
    interrupted = False
    child: subprocess.Popen[str] | None = None
    previous_sigterm_handler = signal.getsignal(signal.SIGTERM)

    def _forward_sigterm(_signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = True
        if child is not None and child.poll() is None:
            try:
                child.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass

    signal.signal(signal.SIGTERM, _forward_sigterm)
    try:
        child = subprocess.Popen(command, **child_kwargs)
        if interrupted and child.poll() is None:
            try:
                child.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
        while True:
            try:
                returncode = child.wait()
                break
            except InterruptedError:
                continue
        if interrupted:
            return 130
        return returncode
    finally:
        if child is not None:
            while child.poll() is None:
                try:
                    child.wait()
                except InterruptedError:
                    continue
        signal.signal(signal.SIGTERM, previous_sigterm_handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=SUPPORTED_AGENTS)
    parser.add_argument("--skill")
    parser.add_argument("--args", default="")
    parser.add_argument("--model")
    parser.add_argument(
        "--routing",
        help="JSON object from resolve_routing() (targets/tiers/roles/"
        "default_tier); when set, the dispatch cell is resolved via "
        "select_cell() instead of --agent/--model, which are ignored",
    )
    parser.add_argument(
        "--tier",
        help="explicit tier row to resolve with --routing, overriding "
        "roles['front-door'].tier/default_tier",
    )
    parser.add_argument(
        "--prefer",
        help="target to move to the front of the resolved tier row with "
        "--routing, overriding roles['front-door'].prefer",
    )
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
    parser.add_argument(
        "--present-decision",
        metavar="DECISION_ID",
        default=None,
        help="attended presentation: print this decision's provider-neutral "
        "envelope JSON (any status, including open) and exit without "
        "spawning a child; exit 2 when the id does not resolve exactly",
    )
    parser.add_argument(
        "--resume-decision",
        metavar="DECISION_ID",
        default=None,
        help="exact decision-ID resume: launch the child only when this exact "
        "record is answered and live, threading `decision:<id>` into the "
        "invocation; an open/superseded/unknown id fails closed with exit "
        "2 and nothing spawned (a known-but-unresumable record's envelope "
        "is printed on stdout for propagation)",
    )
    parser.add_argument(
        "--run",
        help="run record whose pending_decisions audit trail receives the "
        "[presented] hop (with --present-decision), and whose "
        "skipped_cells audit trail receives any capacity-gated cell "
        "select_cell() walked past (with --routing)",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parsed = parser.parse_args(argv)
    if parsed.present_decision is None and not parsed.skill:
        parser.error("--skill is required unless --present-decision is used")
    if parsed.present_decision is None and not parsed.agent and not parsed.routing:
        parser.error(
            "--agent is required unless --present-decision or --routing is used"
        )
    if parsed.no_inherit_codex_auth and parsed.agent != "codex":
        parser.error("--no-inherit-codex-auth is only valid with --agent codex")
    if parsed.present_decision is not None:
        envelope, error = present_decision(parsed.present_decision)
        if error:
            print(f"blocked_pending_decision: {error}", file=sys.stderr)
            return 2
        _stamp_presented(parsed.run, envelope["decision_id"])
        print(json.dumps(envelope))
        return 0
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
    resume_args = parsed.args
    if parsed.resume_decision is not None:
        envelope, error = load_decision_for_boundary(parsed.resume_decision)
        if error is None:
            _load, validate, _parse = _decision_helpers()
            reasons = validate(envelope)["reasons"]
            if reasons:
                error = "decision is not resumable: " + "; ".join(reasons)
        if error:
            if envelope is not None:
                # Propagate the structured pending result unchanged so an
                # unattended caller receives it verbatim.
                print(json.dumps(envelope))
            print(f"blocked_pending_decision: {error}", file=sys.stderr)
            return 2
        resume_args = append_decision_token(resume_args, envelope["decision_id"])
    if parsed.routing is not None:
        try:
            routing = json.loads(parsed.routing)
        except json.JSONDecodeError as exc:
            print(f"--routing must be valid JSON: {exc}", file=sys.stderr)
            return 1
        if not isinstance(routing, dict):
            print("--routing must be a JSON object", file=sys.stderr)
            return 1
        try:
            cell = select_dispatch_cell(
                routing,
                tier=parsed.tier,
                prefer=parsed.prefer,
                run_path=parsed.run,
            )
        except NoExecutionTarget as exc:
            print(f"blocked_no_capacity: {exc}", file=sys.stderr)
            return 2
        parsed.agent = cell.harness
        parsed.model = cell.model
    command = build_command(
        parsed.agent,
        parsed.skill,
        resume_args,
        model=parsed.model,
        cwd=parsed.cwd,
        write=parsed.write,
        add_dirs=parsed.add_dir,
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
        try:
            child_env, codex_home, automatic_home = prepare_codex_child_environment(
                parsed.codex_home,
                inherit_auth=not parsed.no_inherit_codex_auth,
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
        if parsed.skill in _INTERNAL_SKILLS:
            child_env[_DISPATCH_DEPTH_ENV] = str(dispatch_depth + 1)
        if automatic_home:
            print(
                f"Using automatic Worktrail Codex home: {codex_home}", file=sys.stderr
            )
    run_kwargs = {"check": False}
    if parsed.skill in _INTERNAL_SKILLS or parsed.agent == "codex":
        run_kwargs["env"] = child_env
    if parsed.cwd:
        run_kwargs["cwd"] = parsed.cwd
    return _run_command_with_sigterm_forwarding(command, run_kwargs)


if __name__ == "__main__":
    raise SystemExit(main())
