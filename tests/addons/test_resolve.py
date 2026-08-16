"""The add-on dispatch seam: `addon_for` resolves a known name, fails closed
on an unknown one, per design D5 ("Unknown add-on names fail closed at
config-resolution time").
"""

from __future__ import annotations

import pytest

from worktrail.addons import resolve
from worktrail.addons.aspens import AspensAddOn


@pytest.mark.parametrize("name", ["aspen", "not-a-real-addon", "", "aspens "])
def test_addon_for_raises_on_an_unknown_name(name):
    with pytest.raises(ValueError, match=r"Unknown add-on"):
        resolve.addon_for(name)


@pytest.mark.parametrize("name", ["aspen", "not-a-real-addon", "typo-name"])
def test_addon_for_error_names_the_unresolved_addon(name):
    with pytest.raises(ValueError) as excinfo:
        resolve.addon_for(name)

    assert name in str(excinfo.value)


def test_addon_for_resolves_a_known_name():
    addon = resolve.addon_for("aspens")

    assert addon.name == "aspens"
    assert isinstance(addon, AspensAddOn)
