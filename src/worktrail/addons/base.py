"""The AddOn interface.

Everything under `worktrail.orchestrator.integrate` and `worktrail.router.preflight`
finishes a task by committing whatever a worker produced and handing it off to a
PR path. An `AddOn` is the one seam for optional, opt-in tooling that wants to run
alongside that hand-off — e.g. keeping generated docs in sync — without either PR
path needing to know what the tool is or how it works. `worktrail.addons.runner`
is the only thing that ever calls `install`/`configure`/`run`; nothing in
`orchestrator/` or `router/` should invoke an add-on directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class AddOnResult:
    """What one `AddOn.run()` call did, for the runner to stage and commit."""

    changed: bool
    detail: str
    paths: list[Path] = field(default_factory=list)


class AddOn(Protocol):
    """A pluggable, opt-in tool run alongside a task's stage-and-commit hand-off."""

    name: str

    def install(self, ctx: Any) -> None:
        """Ensure the add-on's underlying tool is present, installing it if not."""
        ...

    def configure(self, ctx: Any) -> None:
        """Ensure the add-on is configured for this repo, without overwriting existing config."""
        ...

    def run(self, ctx: Any) -> AddOnResult:
        """Do the add-on's work and report what changed for the runner to commit."""
        ...
