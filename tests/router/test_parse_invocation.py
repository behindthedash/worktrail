"""Pin the front door's invocation grammar.

Every form advertised in `skills/worktrail-help/SKILL.md` and every bullet in
`skills/worktrail-go/SKILL.md`'s Phase 1 gets a case here. That correspondence is
the point: the grammar lived only as prose, so a form could be documented and
never parsed (`fix <request>`) or parsed and never documented, with no test able
to notice either way.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from worktrail.router.parse_invocation import FORMS, V1_INTENTS, parse, render_forms

REPO_ROOT = Path(__file__).resolve().parents[2]
HELP_SKILL = REPO_ROOT / "skills" / "worktrail-help" / "SKILL.md"

REPOS = ["datalena", "worktrail", "gracefully-giving-back"]


@pytest.fixture()
def queue(tmp_path: Path) -> Path:
    """A queue/ folder holding two briefs, for brief-id resolution."""
    folder = tmp_path / "queue"
    folder.mkdir()
    (folder / "20260823-083210-fix-datalena-web-unit-ci.md").write_text(
        "---\nid: 20260823-083210\nstatus: queued\n---\n\nfocus\n", encoding="utf-8"
    )
    (folder / "20260714-101500-ggb-caption-backfill.md").write_text(
        "---\nid: 20260714-101500\nstatus: queued\n---\n\nfocus\n", encoding="utf-8"
    )
    return folder


# --------------------------------------------------------------------------- #
# Bullet order -- the precedence itself
# --------------------------------------------------------------------------- #


def test_empty_argument_is_the_dashboard():
    assert parse("")["mode"] == "dashboard"
    assert parse("   ")["mode"] == "dashboard"


def test_help_never_reaches_the_dashboard():
    r = parse("help")
    assert r["mode"] == "help"
    assert r["help_topic"] is None


def test_help_carries_a_remaining_topic():
    assert parse("help drain")["help_topic"] == "drain"


@pytest.mark.parametrize(
    "raw,max_items,drain_repo",
    [
        ("drain", None, None),
        ("drain 4", 4, None),
        ("drain 4 datalena", 4, "datalena"),
        ("drain datalena", None, "datalena"),
    ],
)
def test_drain_takes_optional_max_items_then_repo(raw, max_items, drain_repo):
    r = parse(raw)
    assert r["mode"] == "drain"
    assert r["drain_max_items"] == max_items
    assert r["drain_repo"] == drain_repo


def test_auto_is_a_mode_and_a_modifier():
    r = parse("auto")
    assert r["mode"] == "auto"
    assert r["auto"] is True


def test_auto_combines_with_a_leading_repo():
    r = parse("datalena auto", known_repos=REPOS)
    assert r["mode"] == "auto"
    assert r["repo"] == "datalena"
    assert r["auto"] is True


@pytest.mark.parametrize("letter", list("ABCDEFGHIJ"))
def test_route_override_accepts_every_route(letter):
    r = parse(f"route:{letter}")
    assert r["mode"] == "route"
    assert r["route"] == letter


def test_route_override_is_case_insensitive_and_takes_a_spec():
    r = parse("route:d 003-payments")
    assert r["route"] == "D"
    assert r["spec"] == "003-payments"


def test_repo_scoped_route_with_spec():
    r = parse("datalena route:G 072-policy", known_repos=REPOS)
    assert (r["repo"], r["route"], r["spec"]) == ("datalena", "G", "072-policy")


@pytest.mark.parametrize("intent", V1_INTENTS)
def test_every_v1_intent_keyword_is_recognized(intent):
    r = parse(intent)
    assert r["mode"] == "intent"
    assert r["intent"] == intent


def test_implement_accepts_the_literal_spec_filler_token():
    """`implement spec <id>` and `implement <id>` are both documented."""
    with_filler = parse("implement spec 003-payments")
    without = parse("implement 003-payments")
    assert with_filler["spec"] == "003-payments"
    assert without["spec"] == "003-payments"


def test_repo_scoped_implement():
    r = parse("datalena implement spec 052-mvp", known_repos=REPOS)
    assert (r["repo"], r["intent"], r["spec"]) == ("datalena", "implement", "052-mvp")


def test_new_keeps_its_free_text_for_downstream_planning():
    r = parse("new add a retry to the uploader")
    assert r["intent"] == "new"
    assert r["free_text"] == "add a retry to the uploader"


# --------------------------------------------------------------------------- #
# Brief ids -- bullets 7 and 9
# --------------------------------------------------------------------------- #


def test_explicit_handoff_id_resolves(queue: Path):
    r = parse("handoff:20260823-083210", queue_folder=queue)
    assert r["mode"] == "brief"
    assert r["brief_status"] == "match"
    assert r["brief_path"].endswith("20260823-083210-fix-datalena-web-unit-ci.md")


def test_bare_brief_id_resolves(queue: Path):
    r = parse("20260823-083210", queue_folder=queue)
    assert r["mode"] == "brief"
    assert r["brief_path"].endswith("20260823-083210-fix-datalena-web-unit-ci.md")


def test_hyphenated_brief_id_prefix_resolves(queue: Path):
    """A unique leading prefix is a brief id -- same resolution `claim` uses."""
    r = parse("20260823-0832", queue_folder=queue)
    assert r["mode"] == "brief"


def test_an_all_digit_token_is_never_a_brief_id(queue: Path):
    """A brief id is `YYYYMMDD-HHMMSS` -- 8 digits, a dash, then 6 more.

    So a bare date like `20260823` is not an id, not even a partial one, and
    must not resolve to that day's brief: it would silently pick one of
    potentially several same-day briefs. The bare-integer bullet sitting above
    the brief-id bullet is therefore correct, not an ordering accident.

    A partial id keeps the dash (`20260823-0832`) and is covered above.
    """
    r = parse("20260823", queue_folder=queue)
    assert r["mode"] == "free_text"
    assert r["brief_status"] is None


def test_full_filename_resolves(queue: Path):
    r = parse("20260823-083210-fix-datalena-web-unit-ci.md", queue_folder=queue)
    assert r["mode"] == "brief"


def test_unresolvable_bare_token_falls_through_to_free_text(queue: Path):
    """Bullet 9: `none` means it was never a brief id."""
    r = parse("refactor", queue_folder=queue)
    assert r["mode"] == "free_text"
    assert r["free_text"] == "refactor"


def test_explicit_handoff_id_that_misses_is_still_a_brief_not_a_request(queue: Path):
    """An explicit `handoff:` was declared to be a brief id.

    Silently reinterpreting it as free text would dispatch a route instead of
    reporting that the id is wrong.
    """
    r = parse("handoff:99999999-000000", queue_folder=queue)
    assert r["mode"] == "brief"
    assert r["brief_status"] == "none"


def test_ambiguous_prefix_is_reported_not_guessed(queue: Path):
    r = parse("handoff:2026", queue_folder=queue)
    assert r["brief_status"] == "ambiguous"
    assert len(r["brief_candidates"]) == 2


# --------------------------------------------------------------------------- #
# Bare integer -- bullet 8
# --------------------------------------------------------------------------- #


def test_bare_integer_is_a_picker_index_only_while_a_picker_is_open():
    assert parse("3", picker_active=True)["mode"] == "picker_index"
    assert parse("3", picker_active=True)["picker_index"] == 3


def test_standalone_bare_integer_is_free_text():
    """SKILL.md: no global numbered list exists."""
    assert parse("3")["mode"] == "free_text"


# --------------------------------------------------------------------------- #
# Free text -- bullet 10
# --------------------------------------------------------------------------- #


def test_free_text_falls_through(queue: Path):
    r = parse("the auth middleware swallows errors", queue_folder=queue)
    assert r["mode"] == "free_text"
    assert r["free_text"] == "the auth middleware swallows errors"


def test_fix_request_is_a_documented_form_that_reaches_free_text(queue: Path):
    """`fix <request>` is advertised in worktrail-help but matched by no bullet.

    Pinning today's real behavior (free text -> classify.py, which scores it
    Route F) rather than the documentation's implication that it is a parsed
    form. Regularizing this is a later change; this test is what makes that
    change visible when it happens.
    """
    r = parse("fix the flaky web unit CI", queue_folder=queue)
    assert r["mode"] == "free_text"
    assert r["free_text"] == "fix the flaky web unit CI"


def test_repo_only_is_that_repos_dashboard():
    r = parse("datalena", known_repos=REPOS)
    assert r["mode"] == "dashboard"
    assert r["repo"] == "datalena"


def test_unknown_leading_token_is_not_lifted_as_a_repo(queue: Path):
    """Repo lifting only fires on a known name; otherwise the token stays put."""
    r = parse("notarepo auto", queue_folder=queue)
    assert r["repo"] is None
    assert r["mode"] == "free_text"


# --------------------------------------------------------------------------- #
# Result contract
# --------------------------------------------------------------------------- #


def test_every_result_carries_the_full_key_set():
    """Consumers read fields in shell; a conditionally-present key would force
    existence checks on every caller."""
    expected = {
        "raw", "mode", "repo", "auto", "help_topic", "drain_max_items",
        "drain_repo", "route", "intent", "spec", "brief_id", "brief_path",
        "brief_status", "brief_candidates", "picker_index", "free_text", "reason",
    }
    for raw in ["", "help", "drain 2", "auto", "route:D", "new x", "handoff:1", "3", "hello"]:
        assert set(parse(raw).keys()) == expected, raw


def test_unbalanced_quote_still_parses():
    """A typo must not traceback out of the front door."""
    r = parse('fix the "flaky test')
    assert r["mode"] == "free_text"


def test_no_queue_folder_reports_the_candidate_without_guessing():
    r = parse("20260823-083210")
    assert r["brief_status"] is None
    assert r["mode"] == "free_text"


# --------------------------------------------------------------------------- #
# Documentation conformance -- the anti-drift gate
# --------------------------------------------------------------------------- #


def _help_forms_block() -> str:
    """Extract the fenced ```text block under worktrail-help's Accepted forms."""
    text = HELP_SKILL.read_text(encoding="utf-8")
    _, _, after = text.partition("## Accepted forms")
    _, _, body = after.partition("```text\n")
    block, _, _ = body.partition("```")
    return block.strip("\n")


def test_help_forms_block_matches_the_parser_registry():
    """worktrail-help's reference block must equal `--forms` output exactly.

    This is the gate the grammar never had. `fix <request>` was advertised and
    unparsed, and `new`/`continue`/`pr`/`brainstorm`/`handoff:<id>` were parsed
    and unadvertised -- in both directions, for years, because prose beside code
    has nothing holding it in place.
    """
    assert _help_forms_block() == render_forms()


def test_every_form_declares_a_mode_the_parser_can_return():
    from worktrail.router.parse_invocation import MODES

    for form in FORMS:
        assert form.mode in MODES, form.syntax


def test_the_forms_marked_unparsed_are_exactly_the_free_text_ones():
    """`parsed=False` means "reads like a form, classified as free text"."""
    unparsed = {f.syntax for f in FORMS if not f.parsed}
    assert unparsed == {"<front-door> fix <request>", "<front-door> <free text>"}
