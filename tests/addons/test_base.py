"""The `AddOn` interface: `AddOnResult` shape and the Protocol's required members.

`worktrail.addons.runner` (a later task) only ever calls `install`/`configure`/`run`
through this seam, so a conforming add-on must expose exactly this surface.
"""

from __future__ import annotations

from pathlib import Path

from worktrail.addons.base import AddOn, AddOnResult


def test_addon_result_defaults_paths_to_an_empty_list():
    result = AddOnResult(changed=False, detail="nothing to do")

    assert result.changed is False
    assert result.detail == "nothing to do"
    assert result.paths == []


def test_addon_result_paths_default_is_not_shared_between_instances():
    # `field(default_factory=list)` guards against the classic mutable-default
    # bug; assert the guard actually works rather than trusting the annotation.
    first = AddOnResult(changed=False, detail="a")
    second = AddOnResult(changed=False, detail="b")

    first.paths.append(Path("docs/generated.md"))

    assert first.paths == [Path("docs/generated.md")]
    assert second.paths == []


def test_addon_result_carries_changed_paths():
    paths = [Path("docs/a.md"), Path("docs/b.md")]

    result = AddOnResult(changed=True, detail="synced 2 files", paths=paths)

    assert result.changed is True
    assert result.paths == paths


def test_addon_protocol_declares_the_expected_members():
    # `AddOn` is a plain (non-runtime-checkable) Protocol, so structural
    # conformance can't be asserted with isinstance(); assert its declared
    # surface instead, which is what `resolve.addon_for`'s callers rely on.
    # `base.py` uses `from __future__ import annotations`, so annotations are
    # unevaluated strings here.
    assert AddOn.__annotations__["name"] == "str"
    for member in ("install", "configure", "run"):
        assert hasattr(AddOn, member), f"AddOn Protocol is missing {member!r}"


def test_a_conforming_class_satisfies_the_addon_surface():
    class _FakeAddOn:
        name = "fake"

        def install(self, ctx) -> None:
            pass

        def configure(self, ctx) -> None:
            pass

        def run(self, ctx) -> AddOnResult:
            return AddOnResult(changed=False, detail="ok")

    addon: AddOn = _FakeAddOn()

    assert addon.name == "fake"
    assert addon.install(None) is None
    assert addon.configure(None) is None
    assert addon.run(None) == AddOnResult(changed=False, detail="ok")
