"""Pin the front door's invocation grammar.

Every form in `worktrail-help`'s reference block and every compatibility
spelling in `ALIASES` gets a case here. That correspondence is the point: the
grammar lived only as prose, so a form could be documented and never parsed
(`fix <request>`) or parsed and never documented, with no test able to notice
either way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from worktrail.router.parse_invocation import (
    ALIASES,
    FORMS,
    MODES,
    NOUNS,
    V1_INTENTS,
    parse,
    render_forms,
)

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


def _dispatch_fields(result: dict) -> dict:
    """Everything a caller acts on -- i.e. the result minus how it was spelled."""
    return {k: v for k, v in result.items() if k not in ("raw", "reason")}


# --------------------------------------------------------------------------- #
# The read-only exceptions and the two bare shortcuts
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


def test_repo_only_is_that_repos_dashboard():
    r = parse("datalena", known_repos=REPOS)
    assert r["mode"] == "dashboard"
    assert r["repo"] == "datalena"


def test_bare_brief_id_resolves(queue: Path):
    r = parse("20260823-083210", queue_folder=queue)
    assert r["mode"] == "brief"
    assert r["brief_path"].endswith("20260823-083210-fix-datalena-web-unit-ci.md")
    assert r["canonical"] == "handoff start 20260823-083210"


# --------------------------------------------------------------------------- #
# handoff <verb>
# --------------------------------------------------------------------------- #


def test_handoff_new_captures_its_focus_text():
    r = parse('handoff new "the uploader swallows timeouts"')
    assert r["mode"] == "capture"
    assert r["free_text"] == "the uploader swallows timeouts"


def test_handoff_new_without_focus_still_captures():
    """The handoff skill infers focus from the conversation when none is given."""
    r = parse("handoff new")
    assert r["mode"] == "capture"
    assert r["free_text"] is None


def test_handoff_new_is_never_read_as_the_v1_new_intent():
    """Collision fact 1: `new` is also the plan-a-feature intent keyword, so the
    noun-verb match must win before anything looks at the bare word."""
    r = parse("handoff new add retries", known_repos=REPOS)
    assert r["mode"] == "capture"
    assert r["intent"] is None
    assert r["repo"] is None


def test_handoff_list_is_its_own_mode():
    assert parse("handoff list")["mode"] == "list"


def test_handoff_start_resolves(queue: Path):
    r = parse("handoff start 20260823-083210", queue_folder=queue)
    assert r["mode"] == "brief"
    assert r["brief_status"] == "match"
    assert r["brief_path"].endswith("20260823-083210-fix-datalena-web-unit-ci.md")


def test_handoff_start_that_misses_is_still_a_brief_not_a_request(queue: Path):
    """An explicit `handoff start` was declared to be a brief id.

    Silently reinterpreting it as free text would dispatch a route instead of
    reporting that the id is wrong.
    """
    r = parse("handoff start 99999999-000000", queue_folder=queue)
    assert r["mode"] == "brief"
    assert r["brief_status"] == "none"


def test_ambiguous_prefix_is_reported_not_guessed(queue: Path):
    r = parse("handoff start 2026", queue_folder=queue)
    assert r["brief_status"] == "ambiguous"
    assert len(r["brief_candidates"]) == 2


def test_handoff_auto_is_a_mode_and_a_modifier():
    r = parse("handoff auto")
    assert r["mode"] == "auto"
    assert r["auto"] is True


def test_handoff_auto_combines_with_a_leading_repo():
    r = parse("datalena handoff auto", known_repos=REPOS)
    assert r["mode"] == "auto"
    assert r["repo"] == "datalena"
    assert r["auto"] is True


@pytest.mark.parametrize(
    "raw,max_items,drain_repo",
    [
        ("handoff drain", None, None),
        ("handoff drain 4", 4, None),
        ("handoff drain 4 datalena", 4, "datalena"),
        ("handoff drain datalena", None, "datalena"),
    ],
)
def test_handoff_drain_takes_optional_max_items_then_repo(raw, max_items, drain_repo):
    r = parse(raw)
    assert r["mode"] == "drain"
    assert r["drain_max_items"] == max_items
    assert r["drain_repo"] == drain_repo


# --------------------------------------------------------------------------- #
# spec <verb>
# --------------------------------------------------------------------------- #


def test_spec_new_keeps_its_request_for_downstream_planning():
    r = parse("spec new add a retry to the uploader")
    assert r["mode"] == "intent"
    assert r["intent"] == "new"
    assert r["free_text"] == "add a retry to the uploader"


def test_spec_implement_carries_the_spec_id():
    r = parse("spec implement 003-payments")
    assert (r["mode"], r["intent"], r["spec"]) == (
        "intent",
        "implement",
        "003-payments",
    )


def test_spec_implement_accepts_the_old_literal_spec_filler_token():
    """`implement spec <id>` was the documented v1 form; the filler stays legal."""
    assert parse("spec implement spec 003-payments")["spec"] == "003-payments"
    assert parse("implement spec 003-payments")["spec"] == "003-payments"


def test_repo_scoped_spec_implement():
    r = parse("datalena spec implement 052-mvp", known_repos=REPOS)
    assert (r["repo"], r["intent"], r["spec"]) == ("datalena", "implement", "052-mvp")


def test_spec_continue_takes_an_optional_id():
    assert parse("spec continue")["intent"] == "continue"
    assert parse("spec continue")["spec"] is None
    assert parse("spec continue 003-payments")["spec"] == "003-payments"


def test_spec_fix_is_a_deterministic_route_f():
    """`fix <request>` was advertised for years and matched by nothing, so it
    reached classify.py as free text. It is a parsed form now: Route F, with
    the request carried for the executor. The executor has no `fix` intent,
    which is why this is a route override rather than an intent."""
    r = parse("spec fix the flaky web unit CI")
    assert r["mode"] == "route"
    assert r["route"] == "F"
    assert r["intent"] is None
    assert r["spec"] is None
    assert r["free_text"] == "the flaky web unit CI"


def test_spec_explore_is_the_v1_brainstorm_intent():
    r = parse("spec explore a plugin marketplace")
    assert r["mode"] == "intent"
    assert r["intent"] == "brainstorm"
    assert r["free_text"] == "a plugin marketplace"


@pytest.mark.parametrize("letter", list("ABCDEFGHIJ"))
def test_spec_route_accepts_every_route(letter):
    r = parse(f"spec route {letter}")
    assert r["mode"] == "route"
    assert r["route"] == letter


def test_spec_route_is_case_insensitive_and_takes_a_spec():
    r = parse("spec route d 003-payments")
    assert r["route"] == "D"
    assert r["spec"] == "003-payments"


def test_repo_scoped_spec_route_with_spec():
    r = parse("datalena spec route G 072-policy", known_repos=REPOS)
    assert (r["repo"], r["route"], r["spec"]) == ("datalena", "G", "072-policy")


def test_spec_route_with_a_non_route_letter_is_help_not_a_dispatch():
    r = parse("spec route Q 003")
    assert r["mode"] == "help"
    assert r["route"] is None


# --------------------------------------------------------------------------- #
# pr <verb>
# --------------------------------------------------------------------------- #


def test_pr_fix_is_the_v1_pr_intent():
    r = parse("pr fix")
    assert r["mode"] == "intent"
    assert r["intent"] == "pr"


# --------------------------------------------------------------------------- #
# A noun with no verb is a help request, never free text
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("noun", [n for n in NOUNS if n != "pr"])
def test_a_bare_noun_asks_for_that_nouns_forms(noun):
    """Collision fact 2: free text containing `handoff` scores Route E at high
    confidence in classify.py, so a bare noun that fell through would be routed
    somewhere confident and wrong with no ambiguity prompt to catch it."""
    r = parse(noun)
    assert r["mode"] == "help"
    assert r["help_topic"] == noun
    assert r["free_text"] is None


def test_a_bare_pr_is_the_old_bare_intent_not_help():
    """`pr` alone predates the grammar and still means `pr fix`."""
    assert parse("pr")["intent"] == "pr"


@pytest.mark.parametrize("raw", ["handoff frobnicate", "spec 003-payments"])
def test_a_noun_with_an_unknown_verb_asks_for_that_nouns_forms(raw):
    r = parse(raw)
    assert r["mode"] == "help"
    assert r["help_topic"] == raw.split()[0]


def test_pr_with_trailing_text_keeps_the_old_pr_intent_shape():
    """`pr <text>` predates the grammar (intent `pr`, text carried), so `pr`
    has no unknown-verb branch: anything after it is the request."""
    r = parse("pr the release workflow is red")
    assert r["intent"] == "pr"
    assert r["free_text"] == "the release workflow is red"


def test_handoff_start_without_an_id_is_help():
    assert parse("handoff start")["mode"] == "help"


# --------------------------------------------------------------------------- #
# Compatibility spellings -- every old form still works and means the new one
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "old,new",
    [
        ("auto", "handoff auto"),
        ("drain 4 datalena", "handoff drain 4 datalena"),
        ("handoff:20260823-083210", "handoff start 20260823-083210"),
        ("new add retries", "spec new add retries"),
        ("implement spec 003-payments", "spec implement 003-payments"),
        ("implement 003-payments", "spec implement 003-payments"),
        ("continue", "spec continue"),
        ("continue 003-payments", "spec continue 003-payments"),
        ("fix the flaky CI", "spec fix the flaky CI"),
        ("brainstorm a marketplace", "spec explore a marketplace"),
        ("spec brainstorm a marketplace", "spec explore a marketplace"),
        ("route:d 003-payments", "spec route D 003-payments"),
        ("ROUTE:F", "spec route F"),
        ("pr", "pr fix"),
    ],
)
def test_every_alias_parses_identically_to_its_canonical_form(old, new, queue):
    old_r = parse(old, queue_folder=queue)
    new_r = parse(new, queue_folder=queue)
    assert _dispatch_fields(old_r) == _dispatch_fields(new_r)
    assert old_r["canonical"] == new_r["canonical"]


def test_aliases_combine_with_a_leading_repo():
    r = parse("datalena auto", known_repos=REPOS)
    assert (r["mode"], r["repo"], r["canonical"]) == (
        "auto",
        "datalena",
        "datalena handoff auto",
    )


@pytest.mark.parametrize("intent", V1_INTENTS)
def test_every_v1_intent_keyword_still_dispatches_that_intent(intent):
    """The executor's vocabulary is unchanged; the old bare words still reach it."""
    r = parse(intent)
    assert r["mode"] == "intent"
    assert r["intent"] == intent


def _probe(syntax: str) -> str:
    """Turn a documented syntax line into a concrete invocation."""
    words = [w for w in syntax.split() if not w.startswith("[")]
    return " ".join(
        w.replace("<focus>", "x")
        .replace("<request>", "x")
        .replace("<idea>", "x")
        .replace("<id>", "20260823-083210")
        .replace("<A-J>", "F")
        for w in words
    )


def test_every_documented_alias_is_actually_rewritten_to_its_canonical_form():
    """`ALIASES` is the published list; each entry must really be rewritten to
    the form shown, or the doc advertises a compatibility that isn't there."""
    for alias in ALIASES:
        r = parse(_probe(alias.old))
        assert r["canonical"] == _probe(alias.new), alias


# --------------------------------------------------------------------------- #
# Bare integer and brief-id fallthrough
# --------------------------------------------------------------------------- #


def test_bare_integer_is_a_picker_index_only_while_a_picker_is_open():
    assert parse("3", picker_active=True)["mode"] == "picker_index"
    assert parse("3", picker_active=True)["picker_index"] == 3


def test_standalone_bare_integer_is_free_text():
    """SKILL.md: no global numbered list exists."""
    assert parse("3")["mode"] == "free_text"


def test_hyphenated_brief_id_prefix_resolves(queue: Path):
    """A unique leading prefix is a brief id -- same resolution `claim` uses."""
    r = parse("20260823-0832", queue_folder=queue)
    assert r["mode"] == "brief"


def test_an_all_digit_token_is_never_a_brief_id(queue: Path):
    """A brief id is `YYYYMMDD-HHMMSS` -- 8 digits, a dash, then 6 more.

    So a bare date like `20260823` is not an id, not even a partial one, and
    must not resolve to that day's brief: it would silently pick one of
    potentially several same-day briefs. The bare-integer check sitting above
    the brief-id check is therefore correct, not an ordering accident.

    A partial id keeps the dash (`20260823-0832`) and is covered above.
    """
    r = parse("20260823", queue_folder=queue)
    assert r["mode"] == "free_text"
    assert r["brief_status"] is None


def test_full_filename_resolves(queue: Path):
    r = parse("20260823-083210-fix-datalena-web-unit-ci.md", queue_folder=queue)
    assert r["mode"] == "brief"


def test_unresolvable_bare_token_falls_through_to_free_text(queue: Path):
    """`none` means it was never a brief id."""
    r = parse("refactor", queue_folder=queue)
    assert r["mode"] == "free_text"
    assert r["free_text"] == "refactor"


def test_no_queue_folder_reports_the_candidate_without_guessing():
    r = parse("20260823-083210")
    assert r["brief_status"] is None
    assert r["mode"] == "free_text"


# --------------------------------------------------------------------------- #
# Free text
# --------------------------------------------------------------------------- #


def test_free_text_falls_through(queue: Path):
    r = parse("the auth middleware swallows errors", queue_folder=queue)
    assert r["mode"] == "free_text"
    assert r["free_text"] == "the auth middleware swallows errors"
    assert r["canonical"] is None


def test_unknown_leading_token_is_not_lifted_as_a_repo(queue: Path):
    """Repo lifting only fires on a known name; otherwise the token stays put."""
    r = parse("notarepo auto", queue_folder=queue)
    assert r["repo"] is None
    assert r["mode"] == "free_text"


def test_unbalanced_quote_still_parses():
    """A typo must not traceback out of the front door."""
    r = parse('fix the "flaky test')
    assert r["mode"] == "route"
    assert r["route"] == "F"


# --------------------------------------------------------------------------- #
# Result contract
# --------------------------------------------------------------------------- #


def test_every_result_carries_the_full_key_set():
    """Consumers read fields in shell; a conditionally-present key would force
    existence checks on every caller."""
    expected = {
        "raw",
        "canonical",
        "mode",
        "repo",
        "auto",
        "help_topic",
        "drain_max_items",
        "drain_repo",
        "route",
        "intent",
        "spec",
        "brief_id",
        "brief_path",
        "brief_status",
        "brief_candidates",
        "picker_index",
        "free_text",
        "reason",
    }
    for raw in [
        "",
        "help",
        "handoff new x",
        "handoff list",
        "handoff drain 2",
        "handoff auto",
        "spec route D",
        "spec fix x",
        "spec new x",
        "handoff start 1",
        "handoff",
        "3",
        "hello",
    ]:
        assert set(parse(raw).keys()) == expected, raw


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
    for form in FORMS:
        assert form.mode in MODES, form.syntax


def test_every_noun_verb_form_in_the_registry_actually_parses():
    """Each advertised `<noun> <verb>` line must reach its declared mode."""
    for form in FORMS:
        words = form.syntax.split()[1:]
        if not words or words[0] not in NOUNS:
            continue
        assert parse(_probe(" ".join(words)))["mode"] == form.mode, form.syntax


def test_the_only_unparsed_form_is_free_text():
    """`parsed=False` means "reads like a form, classified as free text"."""
    unparsed = {f.syntax for f in FORMS if not f.parsed}
    assert unparsed == {"<front-door> <free text>"}
