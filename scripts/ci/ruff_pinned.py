#!/usr/bin/env python3
"""Run the exact `ruff` this repo pins, or fail loudly -- never a different one.

CI installs the repo with `pip install -e ".[dev]"`, so the `ruff` on its PATH
is always the pinned version. A developer machine has whatever `ruff` was
installed once, globally, for every repo on it -- on 2026-09-19 that was
0.16.5 against a 0.16.7 pin. ruff's lint behavior changes between patch
releases, so a local PASS is only evidence about CI when the two are the same
build.

What this does NOT fix, stated plainly because it was the original (wrong)
diagnosis: PR #1279's escaped EXE001 findings. Those rules are disabled by
*platform*, not version -- ruff's own docs say `shebang-not-executable` is
"not enforced on Windows or WSL" -- and `uvx ruff@0.16.7 check --select
EXE001,EXE002` finds nothing on a WSL checkout either. That gap is closed by
`check_shebang_exec_bits.py`, which reads git's index instead. This script
addresses the separate, real drift risk across every other rule.

Resolution order:

1. `ruff` on PATH, **only if** its `--version` equals the pin. This is CI's
   path and costs one extra subprocess.
2. `uvx ruff@<pin>`, which fetches and caches that exact version without
   touching any global install. This is the developer path: no environment
   surgery, and no "upgrade your ruff" step that then breaks a sibling repo
   pinning something else.

Neither available -> exit 2 with the command to fix it. Refusing is the point;
falling back to a mismatched ruff would restore the drift.

The pin is read from `pyproject.toml`'s `[project.optional-dependencies] dev`
list, so it lives in exactly one place. Bump it there and every caller -- this
script, CI, and `.worktrail/policy.yaml`'s lint commands -- follows.

Usage (argv is forwarded verbatim):

    python3 scripts/ci/ruff_pinned.py check .
    python3 scripts/ci/ruff_pinned.py format --check .
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import tomllib

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PIN_RE = re.compile(r"^ruff==(?P<version>[0-9][0-9A-Za-z.\-+]*)$")


class PinError(RuntimeError):
    """The pin could not be read, or no matching ruff could be found."""


def read_pin(pyproject: Path | None = None) -> str:
    """The exact ruff version pinned in `[project.optional-dependencies] dev`.

    Only an `==` pin counts. A range (`ruff>=0.8`) cannot tell two machines to
    agree on a version, which is the entire problem this script exists for, so
    it is rejected rather than silently accepted as "close enough".
    """
    path = pyproject or (_REPO_ROOT / "pyproject.toml")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PinError(f"could not read {path}: {exc}") from exc
    dev = (data.get("project", {}).get("optional-dependencies", {}) or {}).get("dev")
    for entry in dev or []:
        match = _PIN_RE.match(str(entry).strip())
        if match:
            return match.group("version")
    raise PinError(
        f"{path} has no exact `ruff==<version>` pin in "
        "[project.optional-dependencies] dev -- add one, or this script cannot "
        "tell which ruff is the right one"
    )


def _version_of(argv: list[str]) -> str | None:
    """`ruff --version`'s version token for a candidate argv, or None."""
    try:
        result = subprocess.run(
            [*argv, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    parts = (result.stdout or "").split()
    return parts[1] if len(parts) >= 2 and parts[0] == "ruff" else None


def resolve_ruff(pin: str) -> list[str]:
    """The argv prefix that runs exactly `ruff==pin`, or raise `PinError`."""
    on_path = shutil.which("ruff")
    if on_path and _version_of([on_path]) == pin:
        return [on_path]
    uvx = shutil.which("uvx")
    if uvx and _version_of([uvx, f"ruff@{pin}"]) == pin:
        return [uvx, f"ruff@{pin}"]
    found = _version_of([on_path]) if on_path else None
    raise PinError(
        f"no ruff {pin} available (this repo's pin). "
        + (f"`ruff` on PATH is {found}. " if found else "`ruff` is not on PATH. ")
        + 'Install the pin with `pip install -e ".[dev]"`, or install uv '
        "(https://docs.astral.sh/uv/) so this script can run `uvx "
        f"ruff@{pin}` without touching your global install. Refusing to run a "
        "different ruff: its findings would not match CI's."
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        pin = read_pin()
        prefix = resolve_ruff(pin)
    except PinError as exc:
        print(f"ruff_pinned: {exc}", file=sys.stderr)
        return 2
    return subprocess.run([*prefix, *args], check=False, cwd=os.getcwd()).returncode


if __name__ == "__main__":
    sys.exit(main())
