"""Where the retro agent's per-repo learning state lives (design D2)."""

from __future__ import annotations

from pathlib import Path

from worktrail.router.gitnexus_preflight import canonical_repo_root
from worktrail.shared.homedir import worktrail_home

RETRO_AGENT_NAME = "worktrail-retro"


def learning_dir(repo: Path) -> Path:
    """`worktrail_home()/learning/<canonical-checkout-name>/`.

    Keyed by the canonical checkout so every linked worktree of one repo shares
    one memory; a non-git path falls back to its own resolved name.
    """
    resolved = Path(repo).resolve()
    root = canonical_repo_root(resolved) or resolved
    return worktrail_home() / "learning" / root.name


def retro_memory_path(repo: Path) -> Path:
    """The `memory: project` file Claude loads when the agent runs in `learning_dir`."""
    return (
        learning_dir(repo) / ".claude" / "agent-memory" / RETRO_AGENT_NAME / "MEMORY.md"
    )
