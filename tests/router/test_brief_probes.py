"""Tests for `worktrail.router.brief_probes.extract_probes`.

The extractor is deliberately repo-blind: it decides only whether a token is
path-shaped, never whether the path exists. `claude/codex/opencode` and
`stub/disable` are still extracted here -- dropping them is `premise_check`'s
job (see tests/workqueue/test_premise_check.py).
"""

from __future__ import annotations

from worktrail.router.brief_probes import extract_probes


def test_plain_node_id_emits_file_portion() -> None:
    probes = extract_probes(
        "Reproduce with `tests/router/test_brief_probes.py::test_plain`."
    )
    assert probes["paths"] == ["tests/router/test_brief_probes.py"]


def test_class_qualified_node_id_emits_file_portion() -> None:
    probes = extract_probes(
        "See `tests/router/test_brief_probes.py::TestFoo::test_bar`."
    )
    assert probes["paths"] == ["tests/router/test_brief_probes.py"]


def test_unquoted_node_id_emits_file_portion() -> None:
    probes = extract_probes("tests/router/test_brief_probes.py::test_plain fails.")
    assert probes["paths"] == ["tests/router/test_brief_probes.py"]


def test_symbol_shaped_node_id_emits_no_path_probe() -> None:
    """`Foo::bar`'s pre-`::` portion fails the path test, so it emits nothing."""
    probes = extract_probes("`Foo::bar` is the call site.")
    assert probes["paths"] == []


def test_harness_and_mode_lists_are_still_extracted() -> None:
    """Repo-blindness guard: these pass extraction and are dropped downstream."""
    probes = extract_probes("Install for `claude/codex/opencode` with `stub/disable`.")
    assert "claude/codex/opencode" in probes["paths"]
    assert "stub/disable" in probes["paths"]
