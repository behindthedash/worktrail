"""Pick the right `AddOn` for a configured name.

`worktrail.addons.runner` is handed add-on names out of a repo's policy config
and must not care which module implements each one. This module is the single
place that decides, so the runner never imports a concrete add-on.
"""

from __future__ import annotations

from worktrail.addons.base import AddOn


def addon_for(name: str) -> AddOn:
    """Construct the `AddOn` registered under *name*.

    Raises `ValueError` naming the unresolved add-on so a typo or unimplemented
    entry in `policy["add_ons"]` fails closed instead of silently no-op'ing.
    """
    raise ValueError(f"Unknown add-on: {name!r}")
