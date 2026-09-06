"""An exhausted summariser spawn must never name a brief.

`_semantic_slug_summary()` reads `result.text` as a summary. When a spawn gave
up without a verdict that text is the provider's usage-limit notice, so the
slug would be built from the error message. Capture must fall through to the
deterministic `fallback_slugify()` path instead (design D5).
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from worktrail.orchestrator.spawnlib import SpawnResult
from worktrail.workqueue import create_handoff as create_handoff_mod
from worktrail.workqueue.create_handoff import _semantic_slug_summary, create_handoff

_LIMIT_TEXT = (
    "Claude usage limit reached. Your limit will reset at 3pm (America/Chicago)."
)


def _patch_routing():
    """Make `_semantic_slug_summary` reach its spawn."""
    return (
        mock.patch.object(create_handoff_mod, "load_policy", return_value={}),
        mock.patch.object(
            create_handoff_mod, "resolve_routing", return_value={"default_tier": "B"}
        ),
    )


def test_exhausted_spawn_yields_no_summary(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    policy_patch, routing_patch = _patch_routing()
    exhausted = SpawnResult(
        text=_LIMIT_TEXT, usage={}, exhausted=True, failure_class="billing"
    )
    with (
        policy_patch,
        routing_patch,
        mock.patch.object(
            create_handoff_mod, "spawn_agent", return_value=exhausted
        ) as spawn,
    ):
        assert _semantic_slug_summary("Fix the flaky drain loop", str(repo)) is None
    assert spawn.call_count == 1


def test_non_exhausted_spawn_summary_is_used(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    policy_patch, routing_patch = _patch_routing()
    with (
        policy_patch,
        routing_patch,
        mock.patch.object(
            create_handoff_mod,
            "spawn_agent",
            return_value=SpawnResult(text="  flaky drain loop  ", usage={}),
        ),
    ):
        assert (
            _semantic_slug_summary("Fix the flaky drain loop", str(repo))
            == "flaky drain loop"
        )


def test_capture_names_brief_from_focus_when_spawn_exhausted(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    policy_patch, routing_patch = _patch_routing()
    exhausted = SpawnResult(
        text=_LIMIT_TEXT, usage={}, exhausted=True, failure_class="billing"
    )
    with (
        policy_patch,
        routing_patch,
        mock.patch.object(create_handoff_mod, "spawn_agent", return_value=exhausted),
    ):
        result = create_handoff(
            "Fix the flaky drain loop",
            queue_base=tmp_path / "queue-base",
            repo=str(repo),
        )

    stem = Path(result["path"]).stem
    assert stem.endswith("-fix-the-flaky-drain-loop")
    for token in ("usage", "limit", "reset", "claude"):
        assert token not in stem
