#!/usr/bin/env python3
"""
`worktrail-detach` -- run a long-running command outside the agent harness's
tracked process tree, with a log, a pid file, and an exit-code sentinel.

Why this exists. Claude Code's Bash tool `run_in_background` option registers
the child as a harness-tracked task, and on this fleet's WSL host the harness
periodically reaps *all* of its own tracked background tasks (bare `[killed]`
marker, no exit code, no traceback, session survives). Every
`worktrail-live full-real` orchestrator launch, `worktrail-preflight run`, and
headless `worktrail-skill-dispatch` spawn that went through that path died
mid-run -- some within 10-25 s of launch -- while the identical command
launched detached (`nohup ... & disown`) ran to a clean exit every time
(`~/.devops/background-kill-hypotheses.md`, H9 CONFIRMED 2026-08-29; 27
incidents in `background-kill-incidents.jsonl`). No cgroup memory limit or
kernel OOM is involved (both processes sit in `/init.scope` with
`memory.max=max`, `oom_kill 0`), and Claude Code documents no setting that
disables the reap. The reap only reaches tasks the harness holds a handle on,
so the fix is to never hand it one.

This module makes that pattern a first-class, testable primitive instead of a
hand-typed shell incantation each agent has to remember (and regularly got
wrong -- a `nohup` inside a `run_in_background` call is still tracked):

    worktrail-detach launch --name <slug> [--cwd DIR] -- <cmd> [args...]
        Spawns a supervisor in its own session (`setsid`), which runs <cmd>
        with stdout+stderr appended to <state>/<slug>.log, writes
        <state>/<slug>.pid, and on exit writes the return code to
        <state>/<slug>.exit. Prints a JSON handle and returns immediately.
    worktrail-detach status --name <slug>
        JSON: state running|exited|gone|unknown, pid, exit_code, log tail.
    worktrail-detach wait --name <slug> [--match REGEX] [--timeout S]
        Follows the log, prints lines matching REGEX (default: none), and
        exits with the command's own exit code once the sentinel appears --
        the one-line body for a `Monitor` tool watch.

State dir defaults to `$WORKTRAIL_HOME/detached` (`~/.worktrail/detached`).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from worktrail.shared.homedir import worktrail_home

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
MARKER = "[worktrail-detach]"

EXIT_OK = 0
EXIT_GONE = 1
EXIT_USAGE = 2
EXIT_ALREADY_RUNNING = 3
EXIT_TIMEOUT = 124


def state_dir(override: str | None) -> Path:
    return Path(override).expanduser() if override else worktrail_home() / "detached"


def handle_paths(name: str, sd: Path) -> dict[str, Path]:
    return {
        "log": sd / f"{name}.log",
        "pid": sd / f"{name}.pid",
        "exit": sd / f"{name}.exit",
    }


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _tail(path: Path, lines: int) -> list[str]:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:] if lines > 0 else []


def _validate_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise SystemExit(
            f"{MARKER} invalid --name {name!r}: letters, digits, '.', '_', '-' only"
        )


# ─── launch ─────────────────────────────────────────────────────────────


def launch(
    name: str,
    cmd: list[str],
    cwd: str | None,
    sd: Path,
    force: bool = False,
    pid_wait_s: float = 5.0,
) -> dict[str, object]:
    _validate_name(name)
    if not cmd:
        raise SystemExit(f"{MARKER} launch needs a command after '--'")
    sd.mkdir(parents=True, exist_ok=True)
    p = handle_paths(name, sd)
    existing = _read_int(p["pid"])
    if _alive(existing) and not p["exit"].exists() and not force:
        return {
            "error": "already-running",
            "name": name,
            "pid": existing,
            "log": str(p["log"]),
        }
    for stale in (p["pid"], p["exit"]):
        try:
            stale.unlink()
        except FileNotFoundError:
            pass
    supervisor = [
        sys.executable,
        "-m",
        "worktrail.runtime.detach",
        "_supervise",
        "--name",
        name,
        "--state-dir",
        str(sd),
        *(["--cwd", cwd] if cwd else []),
        "--",
        *cmd,
    ]
    proc = subprocess.Popen(
        supervisor,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    deadline = time.monotonic() + pid_wait_s
    child_pid: int | None = None
    while time.monotonic() < deadline:
        child_pid = _read_int(p["pid"])
        if child_pid or p["exit"].exists():
            break
        time.sleep(0.05)
    return {
        "name": name,
        "supervisor_pid": proc.pid,
        "pid": child_pid,
        "log": str(p["log"]),
        "pid_file": str(p["pid"]),
        "exit_file": str(p["exit"]),
        "cwd": cwd or os.getcwd(),
        "wait_cmd": f"worktrail-detach wait --name {name} --state-dir {sd}",
    }


def supervise(name: str, cmd: list[str], cwd: str | None, sd: Path) -> int:
    """The detached parent of the real command. Runs in its own session."""
    p = handle_paths(name, sd)
    with open(p["log"], "a", buffering=1) as log:
        log.write(
            f"{MARKER} started {time.strftime('%Y-%m-%dT%H:%M:%S%z')} cmd={cmd!r}\n"
        )
        try:
            child = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        except (FileNotFoundError, PermissionError) as exc:
            log.write(f"{MARKER} spawn failed: {exc}\n")
            p["exit"].write_text("127\n")
            return 127
        p["pid"].write_text(f"{child.pid}\n")

        def _forward(signum, _frame):
            try:
                child.send_signal(signum)
            except ProcessLookupError:
                pass

        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, _forward)
        rc = child.wait()
        log.write(f"{MARKER} exit rc={rc} {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n")
    p["exit"].write_text(f"{rc}\n")
    return rc


# ─── status / wait ──────────────────────────────────────────────────────


def status(name: str, sd: Path, tail_lines: int = 5) -> dict[str, object]:
    _validate_name(name)
    p = handle_paths(name, sd)
    pid = _read_int(p["pid"])
    exit_code = _read_int(p["exit"])
    if p["exit"].exists():
        state = "exited"
    elif pid is None:
        state = "unknown"
    elif _alive(pid):
        state = "running"
    else:
        state = "gone"
    return {
        "name": name,
        "state": state,
        "pid": pid,
        "exit_code": exit_code,
        "log": str(p["log"]),
        "log_tail": _tail(p["log"], tail_lines),
    }


def wait(
    name: str,
    sd: Path,
    match: str | None,
    interval: float,
    timeout: float | None,
    from_start: bool,
    out=sys.stdout,
) -> int:
    _validate_name(name)
    p = handle_paths(name, sd)
    pattern = re.compile(match) if match else None
    deadline = time.monotonic() + timeout if timeout else None
    pos = 0 if from_start else (p["log"].stat().st_size if p["log"].exists() else 0)
    buf = ""

    def drain_log() -> None:
        nonlocal pos, buf
        try:
            with open(p["log"], "r", errors="replace") as fh:
                fh.seek(pos)
                chunk = fh.read()
                pos = fh.tell()
        except OSError:
            return
        if not chunk:
            return
        buf += chunk
        *lines, buf = buf.split("\n")
        for line in lines:
            if pattern and pattern.search(line):
                print(line, file=out, flush=True)

    while True:
        drain_log()
        if p["exit"].exists():
            drain_log()
            rc = _read_int(p["exit"])
            print(f"{MARKER} exited rc={rc}", file=out, flush=True)
            return rc if rc is not None else EXIT_GONE
        pid = _read_int(p["pid"])
        if pid is not None and not _alive(pid):
            # Give the supervisor one interval to land the sentinel after the child dies.
            time.sleep(interval)
            if p["exit"].exists():
                continue
            print(
                f"{MARKER} process {pid} gone without exit sentinel",
                file=out,
                flush=True,
            )
            return EXIT_GONE
        if deadline and time.monotonic() > deadline:
            print(f"{MARKER} wait timed out after {timeout}s", file=out, flush=True)
            return EXIT_TIMEOUT
        time.sleep(interval)


# ─── CLI ────────────────────────────────────────────────────────────────


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def _common(sp):
        sp.add_argument(
            "--name", required=True, help="handle slug (letters, digits, . _ -)"
        )
        sp.add_argument(
            "--state-dir", default=None, help="default: $WORKTRAIL_HOME/detached"
        )

    sp = sub.add_parser("launch", help="spawn <cmd> detached; print a JSON handle")
    _common(sp)
    sp.add_argument("--cwd", default=None)
    sp.add_argument(
        "--force",
        action="store_true",
        help="replace a still-running handle of the same name",
    )
    sp.add_argument("command", nargs=argparse.REMAINDER, help="-- <cmd> [args...]")

    sp = sub.add_parser("_supervise", help=argparse.SUPPRESS)
    _common(sp)
    sp.add_argument("--cwd", default=None)
    sp.add_argument("command", nargs=argparse.REMAINDER)

    sp = sub.add_parser("status", help="JSON state of a handle")
    _common(sp)
    sp.add_argument("--tail", type=int, default=5)

    sp = sub.add_parser(
        "wait", help="follow the log until exit; exit with the command's rc"
    )
    _common(sp)
    sp.add_argument(
        "--match", default=None, help="regex; print matching log lines as they appear"
    )
    sp.add_argument("--interval", type=float, default=2.0)
    sp.add_argument("--timeout", type=float, default=None)
    sp.add_argument(
        "--from-start",
        action="store_true",
        help="replay the whole log, not just new lines",
    )

    a = p.parse_args(argv)
    sd = state_dir(a.state_dir)
    if a.cmd in ("launch", "_supervise"):
        cmd = list(a.command)
        if cmd and cmd[0] == "--":
            cmd = cmd[1:]
    if a.cmd == "launch":
        result = launch(a.name, cmd, a.cwd, sd, force=a.force)
        print(json.dumps(result))
        return EXIT_ALREADY_RUNNING if result.get("error") else EXIT_OK
    if a.cmd == "_supervise":
        return supervise(a.name, cmd, a.cwd, sd)
    if a.cmd == "status":
        result = status(a.name, sd, a.tail)
        print(json.dumps(result))
        return EXIT_USAGE if result["state"] == "unknown" else EXIT_OK
    if a.cmd == "wait":
        return wait(a.name, sd, a.match, a.interval, a.timeout, a.from_start)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
