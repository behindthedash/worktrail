"""Resolve a routing target's `auth.profile` into the environment variables a
harness worker is spawned with.

`env_profiles` exists because Worktrail removes the settings-file channel that
would otherwise carry a worker's environment: `_with_default_setting_sources`
(`orchestrator/spawnlib.py`) appends `--setting-sources project,local` to every
claude spawn so the operator's USER-level `~/.claude/settings.json` is excluded
-- deliberately, because a user-level Stop hook fires on every worker that
commits or writes and its continuation turn's text becomes the final message
the orchestrator parses for report-back JSON (investigation 20260711-130900).

The exclusion is right; the collateral damage is that anything the operator
configures in that file's `env` block -- an alternate `ANTHROPIC_BASE_URL`, its
token, the model aliases -- never reaches a worker. A spawn whose endpoint is
configured only there makes no API call at all and still exits 0, which is
indistinguishable from an empty-but-successful run.

A profile therefore declares *where* the values live and *which* of them to
copy, without storing any value itself -- only key names and an optional
non-secret `expect` assertion. A worker gets its endpoint back; a misconfigured
one fails loudly before launch instead of silently doing nothing.

This module deliberately does not live in `spawnlib` or `routing_cli`:
`routing_cli` imports `..orchestrator.agent_capacity` and `spawnlib` imports
`..router.routing_cli`, so a module-level `routing_cli -> spawnlib` edge would
be an import cycle. It does not live in `policy` either, because the validator
there must never open a profile file -- reads belong at spawn and `--check`
time, not at every `load_policy()` call.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .policy import OperatorConfigError

__all__ = ["resolve_env_profile"]


def _config_error(
    *,
    target: str,
    profile_name: str,
    declared_in: Path | None,
    detail: str,
) -> OperatorConfigError:
    """Build the one error shape every profile failure uses: what is wrong,
    for which target and profile, and which file to edit -- naming the routing
    file the operator actually has in effect, not a bare default."""
    where = f" in {declared_in}" if declared_in is not None else ""
    return OperatorConfigError(
        f"routing target {target!r} env profile {profile_name!r}: {detail}{where}"
    )


def resolve_env_profile(
    profile_name: str,
    profile: Mapping[str, Any],
    *,
    target: str,
    declared_in: Path | None = None,
) -> dict[str, str]:
    """Return the `{key: value}` environment a target's `auth.profile` supplies.

    `profile` is the validated `env_profiles.<name>` entry -- `{from, keys,
    expect}`. `keys` is what gets *copied*; `expect` is what gets *asserted
    against the file*. They are independent on purpose: a key may be asserted
    without being copied (provenance checking), and a key may be copied without
    ever being asserted on (so its value is never printable).

    Every failure raises `OperatorConfigError` naming the target, the profile,
    the file, and where applicable the offending key. **No message ever
    includes a value for a key that is not declared in `expect`** -- the
    operator opted into naming that key, and only that key, when they wrote the
    assertion.
    """
    raw_from = profile.get("from")
    if not isinstance(raw_from, str) or not raw_from.strip():
        raise _config_error(
            target=target,
            profile_name=profile_name,
            declared_in=declared_in,
            detail="`from` must be a non-empty path string",
        )

    path = Path(raw_from).expanduser()
    if not path.is_absolute():
        # Deliberately refused rather than resolved relative to the routing
        # file: `explicit_cell_override` points WORKTRAIL_ROUTING_FILE at a
        # mkstemp file under /tmp, so "relative to the routing file" would
        # silently mean a different directory exactly when a `--model-map` /
        # `--effort` override is in play.
        raise _config_error(
            target=target,
            profile_name=profile_name,
            declared_in=declared_in,
            detail=(
                f"`from` path {raw_from!r} is not absolute -- use an absolute "
                "path or one starting with ~"
            ),
        )

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise _config_error(
            target=target,
            profile_name=profile_name,
            declared_in=declared_in,
            detail=f"{path} does not exist or is unreadable",
        ) from None

    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        document = None
    if not isinstance(document, Mapping):
        raise _config_error(
            target=target,
            profile_name=profile_name,
            declared_in=declared_in,
            detail=f'{path} is not valid JSON with an "env" object '
            '-- expected {"env": {...}}',
        )

    file_env = document.get("env")
    if not isinstance(file_env, Mapping):
        raise _config_error(
            target=target,
            profile_name=profile_name,
            declared_in=declared_in,
            detail=f'{path} has no "env" object -- expected {{"env": {{...}}}}',
        )

    expect = profile.get("expect") or {}
    for key, expected in expect.items():
        actual = file_env.get(key)
        if actual != expected:
            # Both sides may be printed here: the operator wrote `expected`,
            # so this key -- and only this key -- is declared non-secret.
            raise _config_error(
                target=target,
                profile_name=profile_name,
                declared_in=declared_in,
                detail=(f"expected {key} == {expected!r} but {path} has {actual!r}"),
            )

    resolved: dict[str, str] = {}
    for key in profile.get("keys") or []:
        value = file_env.get(key)
        if value is None:
            raise _config_error(
                target=target,
                profile_name=profile_name,
                declared_in=declared_in,
                detail=(
                    f"key {key!r} is not set in {path}{_key_hint(profile_name, key)}"
                ),
            )
        if not isinstance(value, str):
            # Name the type, never the value.
            raise _config_error(
                target=target,
                profile_name=profile_name,
                declared_in=declared_in,
                detail=(
                    f"key {key!r} must be a string in {path}; "
                    f"got a {type(value).__name__}"
                ),
            )
        if not value:
            # Mirrors `build_child_env`'s non-empty rule for `auth.env`: a
            # present-but-empty credential is a misconfiguration, not a value.
            raise _config_error(
                target=target,
                profile_name=profile_name,
                declared_in=declared_in,
                detail=(
                    f"key {key!r} is empty in {path}{_key_hint(profile_name, key)}"
                ),
            )
        resolved[key] = value
    return resolved


def _key_hint(profile_name: str, key: str) -> str:
    """The one-line remediation shared by the "not set" and "is empty" cases."""
    return f" -- add it there or drop it from env_profiles.{profile_name}.keys"
