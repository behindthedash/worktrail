"""Tests for workqueue/decisions.py — the human decision queue."""

import json

import pytest

from worktrail.shared.brief_frontmatter import split_frontmatter
from worktrail.workqueue import decisions, work_queue


@pytest.fixture()
def qbase(tmp_path, monkeypatch):
    base = tmp_path / "wq"
    (base / "queue").mkdir(parents=True)
    (base / "picked").mkdir()
    monkeypatch.setenv("WORK_QUEUE_DIR", str(base))
    return base


def _ask(qbase, **kw):
    defaults = dict(
        background="Exports were added before archiving existed; both behaviors "
                   "now have users depending on them.",
        why="Two mutually exclusive user-facing behaviors are both defensible.",
        context="Read the spec, the epic, and the last three PRs; no precedent.",
        options=["Option A: strict — tradeoff X", "Option B: lenient — tradeoff Y"],
        queue_base=qbase,
    )
    defaults.update(kw)
    return decisions.ask("Should exports include archived rows?", **defaults)


def _mk_picked_brief(qbase, brief_id="20260813-120000-export-scope"):
    path = qbase / "picked" / f"{brief_id}.md"
    path.write_text(
        f"---\nid: {brief_id}\nstatus: picked\nfocus: export scope\n---\n\n"
        "## Focus\n\nexport scope\n",
        encoding="utf-8")
    return brief_id


# ---------------------------------------------------------------------------
# ask


def test_ask_creates_structured_open_record(qbase):
    result = _ask(qbase, repo="repo-a")
    assert result["status"] == "created"
    open_files = list((qbase / "decisions" / "open").glob("*.md"))
    assert len(open_files) == 1
    fm, body = split_frontmatter(open_files[0].read_text(encoding="utf-8"))
    assert fm["status"] == "open"
    assert fm["repo"] == "repo-a"
    for heading in ("## Question", "## Background",
                    "## Why a human decision is needed",
                    "## Context (what was attempted)", "## Options", "## Answer"):
        assert heading in body
    assert "1. Option A" in body and "2. Option B" in body
    assert "In priority order" in body
    assert "before archiving existed" in body  # the background story


def test_ask_option_costs_render_per_option(qbase):
    result = _ask(
        qbase,
        options=["Ship the config toggle", "Refactor the export pipeline"],
        option_costs=["low -- config only, ships today",
                      "high -- better long-term architecture, ~3 days"],
        recommendation="Quick to production: option 1; "
                       "long-term architecture: option 2.")
    assert result["status"] == "created"
    open_files = list((qbase / "decisions" / "open").glob("*.md"))
    _fm, body = split_frontmatter(open_files[0].read_text(encoding="utf-8"))
    assert "1. Ship the config toggle\n   - Cost: low -- config only" in body
    assert "2. Refactor the export pipeline\n   - Cost: high -- better" in body
    assert "Quick to production: option 1" in body


def test_ask_option_cost_count_mismatch_refused(qbase):
    with pytest.raises(ValueError, match="once per --option"):
        _ask(qbase, option_costs=["low"])


@pytest.mark.parametrize("field", ["background", "why", "context"])
def test_ask_refuses_empty_structured_fields(qbase, field):
    with pytest.raises(ValueError, match="required and must be non-empty"):
        _ask(qbase, **{field: "  "})


def test_ask_refuses_fewer_than_two_options(qbase):
    with pytest.raises(ValueError, match="at least two --option"):
        _ask(qbase, options=["only one"])


def test_ask_refuses_second_open_decision_for_same_brief(qbase):
    brief_id = _mk_picked_brief(qbase)
    _ask(qbase, brief=brief_id)
    with pytest.raises(ValueError, match="already has an open decision"):
        _ask(qbase, brief=brief_id)


def test_ask_release_requires_brief(qbase):
    with pytest.raises(ValueError, match="--release requires --brief"):
        _ask(qbase, release_brief=True)


def test_ask_with_brief_release_stamps_and_requeues(qbase):
    brief_id = _mk_picked_brief(qbase)
    result = _ask(qbase, brief=brief_id, release_brief=True)
    assert result["brief_stamped"] is True
    assert result["released"] is True
    requeued = qbase / "queue" / f"{brief_id}.md"
    assert requeued.is_file()
    fm, _ = split_frontmatter(requeued.read_text(encoding="utf-8"))
    assert fm["awaiting-decision"] == result["id"]
    assert fm["status"] == "queued"


def test_ask_with_brief_as_absolute_path_release_stamps_and_requeues(qbase):
    """`--brief` is commonly a full claimed-brief path, not a bare id -- the go
    skill's own `$BRIEF_ID`/`decision-queue.md#file-a-decision` convention holds
    the resolved path from `work_queue.py resolve`'s own `candidates[0]`, and
    sibling CLIs (`worktrail-check-brief-staleness --brief`, etc.) already accept
    a path. Reproduced live 2026-08-14: `ask --release` silently returned
    `brief_stamped: false, released: false` with no error for exactly this case."""
    brief_id = _mk_picked_brief(qbase)
    brief_path = str(qbase / "picked" / f"{brief_id}.md")
    result = _ask(qbase, brief=brief_path, release_brief=True)
    assert result["brief_stamped"] is True
    assert result["released"] is True
    assert result["error"] is None
    requeued = qbase / "queue" / f"{brief_id}.md"
    assert requeued.is_file()
    fm, _ = split_frontmatter(requeued.read_text(encoding="utf-8"))
    assert fm["awaiting-decision"] == result["id"]
    assert fm["status"] == "queued"


def test_ask_release_with_unresolvable_brief_reports_error_loudly(qbase):
    """A brief that cannot be resolved at all must fail loudly -- not return
    `released: false` with no explanation and exit 0."""
    result = _ask(qbase, brief="/nonexistent/nope.md", release_brief=True)
    assert result["brief_stamped"] is False
    assert result["released"] is False
    assert result["error"]
    assert "nope.md" in result["error"]


# ---------------------------------------------------------------------------
# blocking through work_queue.list


def test_brief_awaiting_open_decision_is_blocked(qbase):
    brief_id = _mk_picked_brief(qbase)
    _ask(qbase, brief=brief_id, release_brief=True)
    listing = work_queue.list_queue()
    (brief,) = listing["briefs"]
    assert brief["blocked"] is True
    assert brief["decision_status"] == "open"
    assert brief["awaiting_decision"]


def test_brief_unblocks_when_decision_answered(qbase):
    brief_id = _mk_picked_brief(qbase)
    result = _ask(qbase, brief=brief_id, release_brief=True)
    decisions.answer(result["id"], "Ship Option B.", queue_base=qbase)
    (brief,) = work_queue.list_queue()["briefs"]
    assert brief["blocked"] is False
    assert brief["decision_status"] == "answered"


def test_missing_decision_record_never_wedges_brief(qbase):
    brief_id = _mk_picked_brief(qbase)
    result = _ask(qbase, brief=brief_id, release_brief=True)
    (qbase / "decisions" / "open" / f"{result['id']}.md").unlink()
    (brief,) = work_queue.list_queue()["briefs"]
    assert brief["blocked"] is False
    assert brief["decision_status"] is None


def test_claim_of_awaiting_brief_warns(qbase):
    brief_id = _mk_picked_brief(qbase)
    result = _ask(qbase, brief=brief_id, release_brief=True)
    claimed = work_queue.claim(brief_id)
    assert claimed["status"] == "claimed"
    assert any(result["id"] in w and "still open" in w
               for w in claimed["warnings"])


# ---------------------------------------------------------------------------
# answer / resolve lifecycle


def test_answer_moves_to_answered_and_records_text(qbase):
    result = _ask(qbase)
    out = decisions.answer(result["id"], "Ship Option B.", queue_base=qbase)
    assert out["status"] == "answered"
    answered = qbase / "decisions" / "answered" / f"{result['id']}.md"
    assert answered.is_file()
    fm, body = split_frontmatter(answered.read_text(encoding="utf-8"))
    assert fm["status"] == "answered"
    assert fm["answered-at"]
    assert "Ship Option B." in body
    assert decisions._PENDING_ANSWER not in body


def test_hand_moved_file_is_honored_by_directory_not_status_field(qbase):
    """A human may answer by editing the markdown and moving the file; the
    directory is the arbiter even when the status field is stale."""
    result = _ask(qbase)
    src = qbase / "decisions" / "open" / f"{result['id']}.md"
    dst_dir = qbase / "decisions" / "answered"
    dst_dir.mkdir(parents=True, exist_ok=True)
    src.rename(dst_dir / src.name)  # status field still says open
    assert decisions.decision_status(result["id"], qbase) == "answered"


def test_resolve_requires_answered(qbase):
    result = _ask(qbase)
    out = decisions.resolve_decision(result["id"], queue_base=qbase)
    assert out["status"] == "still-open"


def test_resolve_archives_and_clears_brief_field(qbase):
    brief_id = _mk_picked_brief(qbase)
    result = _ask(qbase, brief=brief_id, release_brief=True)
    decisions.answer(result["id"], "Ship Option B.", queue_base=qbase)
    # simulate the resuming session having re-claimed the brief
    work_queue.claim(brief_id)
    out = decisions.resolve_decision(result["id"], queue_base=qbase)
    assert out["status"] == "resolved"
    assert out["brief_cleared"] is True
    resolved = qbase / "decisions" / "resolved" / f"{result['id']}.md"
    assert resolved.is_file()
    picked = qbase / "picked" / f"{brief_id}.md"
    fm, _ = split_frontmatter(picked.read_text(encoding="utf-8"))
    assert "awaiting-decision" not in fm


def test_answer_unknown_id_reports_not_found(qbase):
    assert decisions.answer("nope", "x", queue_base=qbase)["status"] == "not-found"


# ---------------------------------------------------------------------------
# list / open ids


def test_list_and_open_ids(qbase):
    a = _ask(qbase)
    b = decisions.ask(
        "Second question entirely?",
        background="Different feature, same day.",
        why="Also a real product call.",
        context="Evidence gathered.",
        options=["A — x", "B — y"],
        queue_base=qbase)
    decisions.answer(b["id"], "B.", queue_base=qbase)

    assert decisions.open_decision_ids(qbase) == [a["id"]]
    rows = decisions.list_decisions(queue_base=qbase)["decisions"]
    statuses = {r["id"]: r["status"] for r in rows}
    assert statuses[a["id"]] == "open"
    assert statuses[b["id"]] == "answered"
    open_only = decisions.list_decisions("open", queue_base=qbase)["decisions"]
    assert [r["id"] for r in open_only] == [a["id"]]
    assert open_only[0]["question"]


# ---------------------------------------------------------------------------
# CLI


def test_cli_ask_answer_resolve_roundtrip(qbase, capsys):
    rc = decisions.main([
        "--queue-dir", str(qbase), "ask",
        "--question", "Should exports include archived rows?",
        "--background", "Exports predate archiving; both behaviors have users.",
        "--why", "Two defensible user-facing behaviors.",
        "--context", "Read spec + PRs; no precedent.",
        "--option", "A — strict", "--option-cost", "low -- ships today",
        "--option", "B — lenient", "--option-cost", "high -- pipeline refactor",
        "--json",
    ])
    assert rc == 0
    dec_id = json.loads(capsys.readouterr().out)["id"]

    rc = decisions.main(["--queue-dir", str(qbase), "list", "--json"])
    assert rc == 0
    assert len(json.loads(capsys.readouterr().out)["decisions"]) == 1

    rc = decisions.main([
        "--queue-dir", str(qbase), "answer", dec_id, "--answer", "B.", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["status"] == "answered"

    rc = decisions.main(["--queue-dir", str(qbase), "resolve", dec_id, "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["status"] == "resolved"


def test_cli_ask_missing_options_fails(qbase, capsys):
    rc = decisions.main([
        "--queue-dir", str(qbase), "ask",
        "--question", "Q?", "--background", "b", "--why", "w", "--context", "c",
        "--option", "only one",
    ])
    assert rc == 1
    assert "at least two --option" in capsys.readouterr().err


def test_cli_show_unknown_fails(qbase, capsys):
    rc = decisions.main(["--queue-dir", str(qbase), "show", "nope"])
    assert rc == 1
