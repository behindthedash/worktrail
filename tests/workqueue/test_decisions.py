"""Tests for workqueue/decisions.py — the human decision queue."""

import datetime as dt
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
    defaults = {
        "background": "Exports were added before archiving existed; both behaviors "
        "now have users depending on them.",
        "why": "Two mutually exclusive user-facing behaviors are both defensible.",
        "context": "Read the spec, the epic, and the last three PRs; no precedent.",
        "options": ["Option A: strict — tradeoff X", "Option B: lenient — tradeoff Y"],
        "queue_base": qbase,
    }
    defaults.update(kw)
    return decisions.ask("Should exports include archived rows?", **defaults)


def _mk_picked_brief(qbase, brief_id="20260813-120000-export-scope"):
    path = qbase / "picked" / f"{brief_id}.md"
    path.write_text(
        f"---\nid: {brief_id}\nstatus: picked\nfocus: export scope\n---\n\n"
        "## Focus\n\nexport scope\n",
        encoding="utf-8",
    )
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
    for heading in (
        "## Question",
        "## Background",
        "## Why a human decision is needed",
        "## Context (what was attempted)",
        "## Options",
        "## Answer",
    ):
        assert heading in body
    assert "1. Option A" in body and "2. Option B" in body
    assert "In priority order" in body
    assert "before archiving existed" in body  # the background story


def test_ask_option_costs_render_per_option(qbase):
    result = _ask(
        qbase,
        options=["Ship the config toggle", "Refactor the export pipeline"],
        option_costs=[
            "low -- config only, ships today",
            "high -- better long-term architecture, ~3 days",
        ],
        recommendation="Quick to production: option 1; "
        "long-term architecture: option 2.",
    )
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


def test_ask_with_queued_brief_no_release_stamps_in_place(qbase):
    """queue_triage.py's `needs-decision` apply action (3.4) never claims the
    brief -- it is already sitting in `queue/`, not `picked/` -- so `ask()`
    with `release_brief=False` must stamp it there directly rather than
    requiring (or performing) a claim/release round-trip."""
    brief_id = "20260901-000000-queued-brief"
    path = qbase / "queue" / f"{brief_id}.md"
    path.write_text(
        f"---\nid: {brief_id}\nstatus: queued\nfocus: some brief\n---\n\n"
        "## Focus\n\nsome brief\n",
        encoding="utf-8",
    )
    result = _ask(qbase, brief=brief_id, release_brief=False)
    assert result["status"] == "created"
    assert result["brief_stamped"] is True
    assert result["released"] is False
    assert path.is_file()
    fm, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    assert fm["awaiting-decision"] == result["id"]
    assert fm["status"] == "queued"


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
    assert any(result["id"] in w and "still open" in w for w in claimed["warnings"])


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
        queue_base=qbase,
    )
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
    rc = decisions.main(
        [
            "--queue-dir",
            str(qbase),
            "ask",
            "--question",
            "Should exports include archived rows?",
            "--background",
            "Exports predate archiving; both behaviors have users.",
            "--why",
            "Two defensible user-facing behaviors.",
            "--context",
            "Read spec + PRs; no precedent.",
            "--option",
            "A — strict",
            "--option-cost",
            "low -- ships today",
            "--option",
            "B — lenient",
            "--option-cost",
            "high -- pipeline refactor",
            "--json",
        ]
    )
    assert rc == 0
    dec_id = json.loads(capsys.readouterr().out)["id"]

    rc = decisions.main(["--queue-dir", str(qbase), "list", "--json"])
    assert rc == 0
    assert len(json.loads(capsys.readouterr().out)["decisions"]) == 1

    rc = decisions.main(
        ["--queue-dir", str(qbase), "answer", dec_id, "--answer", "B.", "--json"]
    )
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["status"] == "answered"

    rc = decisions.main(["--queue-dir", str(qbase), "resolve", dec_id, "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["status"] == "resolved"


def test_cli_ask_missing_options_fails(qbase, capsys):
    rc = decisions.main(
        [
            "--queue-dir",
            str(qbase),
            "ask",
            "--question",
            "Q?",
            "--background",
            "b",
            "--why",
            "w",
            "--context",
            "c",
            "--option",
            "only one",
        ]
    )
    assert rc == 1
    assert "at least two --option" in capsys.readouterr().err


def test_cli_show_unknown_fails(qbase, capsys):
    rc = decisions.main(["--queue-dir", str(qbase), "show", "nope"])
    assert rc == 1


def test_cli_show_json_emits_structured_object(qbase, capsys):
    result = _ask(qbase, repo="repo-a")
    dec_id = result["id"]
    capsys.readouterr()

    rc = decisions.main(["--queue-dir", str(qbase), "show", dec_id, "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["id"] == dec_id
    assert out["status"] == "open"
    assert out["repo"] == "repo-a"
    assert out["question"] == "Should exports include archived rows?"


# ---------------------------------------------------------------------------
# versioned pending-decision envelope + idempotent identity


def _identity(source="check_spec_collision", repo="repo-a", subject="change-x"):
    return decisions.decision_identity(source, repo, subject)


def test_decision_identity_is_deterministic_and_distinct():
    a = _identity()
    b = _identity()
    other_subject = decisions.decision_identity(
        "check_spec_collision", "repo-a", "change-y"
    )
    other_source = decisions.decision_identity(
        "check_brief_staleness", "repo-a", "change-x"
    )
    assert a == b
    assert a.startswith("dec-")
    assert len({a, other_subject, other_source}) == 3


def test_decision_identity_refuses_blank_provenance():
    with pytest.raises(ValueError, match="subject is required"):
        decisions.decision_identity("guard", "repo-a", "   ")
    with pytest.raises(ValueError, match="source is required"):
        decisions.decision_identity("", "repo-a", "change-x")


def test_decision_slugify_matches_handoff_normalization():
    assert decisions._slugify(
        "ci-watch-loop.md's review-thread gate is unreachable"
    ) == ("ci-watch-loop-md-review")


def test_pending_decision_envelope_requires_core_fields():
    with pytest.raises(ValueError, match="decision_id is required"):
        decisions.pending_decision_envelope(
            decision_id=" ", question="Q?", options=["a", "b"], source="g"
        )
    with pytest.raises(ValueError, match="options must be a non-empty list"):
        decisions.pending_decision_envelope(
            decision_id="d1", question="Q?", options=[" ", ""], source="g"
        )
    env = decisions.pending_decision_envelope(
        decision_id="d1",
        question=" Q? ",
        options=["a", " b "],
        source=" g ",
        subject="s1",
        run_id="go-1",
        dispatch_mode="adapter",
    )
    assert env["schema"] == decisions.DECISION_ENVELOPE_SCHEMA
    assert env["version"] == decisions.DECISION_ENVELOPE_VERSION
    assert env["status"] == "pending"
    assert env["options"] == ["a", "b"]
    # provenance keeps only what was actually recorded — never fabricated fields
    assert env["provenance"] == {
        "source": "g",
        "subject": "s1",
        "run_id": "go-1",
        "dispatch_mode": "adapter",
    }


def test_parse_envelope_accepts_dict_and_json_string_roundtrip():
    env = decisions.pending_decision_envelope(
        decision_id="d1",
        question="Q?",
        options=["a", "b"],
        source="guard",
        created_at="2026-08-25T10:00:00+00:00",
    )
    parsed = decisions.parse_pending_decision_envelope(json.dumps(env))
    assert parsed["decision_id"] == "d1"
    assert decisions.parse_pending_decision_envelope(env)["question"] == "Q?"
    extra = {**env, "future_field": {"nested": True}}
    assert decisions.parse_pending_decision_envelope(extra)["decision_id"] == "d1"


def test_parse_envelope_rejects_bad_json_wrong_schema_version_missing_fields():
    good = decisions.pending_decision_envelope(
        decision_id="d1", question="Q?", options=["a", "b"], source="guard"
    )
    with pytest.raises(decisions.DecisionEnvelopeError, match="not valid JSON"):
        decisions.parse_pending_decision_envelope("{nope")
    with pytest.raises(decisions.DecisionEnvelopeError, match="must be a JSON object"):
        decisions.parse_pending_decision_envelope("[1, 2]")
    with pytest.raises(decisions.DecisionEnvelopeError, match="expected schema"):
        decisions.parse_pending_decision_envelope({**good, "schema": "other"})
    with pytest.raises(
        decisions.DecisionEnvelopeError, match="unsupported envelope version"
    ):
        decisions.parse_pending_decision_envelope({**good, "version": 99})
    missing_prov = {k: v for k, v in good.items() if k != "provenance"}
    with pytest.raises(
        decisions.DecisionEnvelopeError,
        match="missing required field\\(s\\): provenance",
    ):
        decisions.parse_pending_decision_envelope(missing_prov)
    with pytest.raises(
        decisions.DecisionEnvelopeError,
        match="provenance must be an object with a non-empty 'source'",
    ):
        decisions.parse_pending_decision_envelope(
            {**good, "provenance": {"source": "  "}}
        )


def test_ask_with_explicit_decision_id_records_provenance_and_envelope(qbase):
    did = _identity(subject="change-x vs spec-y")
    result = _ask(
        qbase,
        repo="repo-a",
        decision_id=did,
        source="check_spec_collision",
        subject="change-x vs spec-y",
        run_id="go-20260825-101010",
        dispatch_mode="in-session-resume",
    )
    assert result["status"] == "created"
    fm, _body = split_frontmatter(
        (qbase / "decisions" / "open" / f"{did}.md").read_text(encoding="utf-8")
    )
    for key, value in (
        ("source", "check_spec_collision"),
        ("subject", "change-x vs spec-y"),
        ("run-id", "go-20260825-101010"),
        ("dispatch-mode", "in-session-resume"),
    ):
        assert fm[key] == value
    env = decisions.parse_pending_decision_envelope(result["envelope"])
    assert env["schema"] == decisions.DECISION_ENVELOPE_SCHEMA
    assert env["version"] == decisions.DECISION_ENVELOPE_VERSION
    assert env["decision_id"] == did
    assert env["status"] in ("pending", "open")
    assert env["provenance"]["source"] == "check_spec_collision"
    assert len(env["options"]) == 2
    assert env["created_at"] == fm["created"]


def test_ask_without_identity_keeps_timestamped_ids_and_still_emits_envelope(qbase):
    first = _ask(qbase)
    second = _ask(qbase)
    assert first["id"] != second["id"]
    for result in (first, second):
        env = decisions.parse_pending_decision_envelope(result["envelope"])
        assert env["decision_id"] == result["id"]
        assert env["provenance"]["source"] == "manual"


def test_ask_same_decision_id_converges_on_existing_open_record(qbase):
    did = _identity()
    first = _ask(qbase, decision_id=did)
    second = _ask(qbase, decision_id=did, background="re-run, unchanged facts")
    assert first["status"] == "created"
    assert second["status"] == "existing"
    assert second["id"] == did
    assert len(list((qbase / "decisions" / "open").glob("*.md"))) == 1


def test_ask_idempotent_replay_skips_duplicate_brief_guard(qbase):
    brief_id = _mk_picked_brief(qbase)
    did = _identity(source="manual", subject=brief_id)
    _ask(qbase, brief=brief_id, decision_id=did)
    # a DIFFERENT decision for the same brief is still refused...
    with pytest.raises(ValueError, match="already has an open decision"):
        _ask(qbase, brief=brief_id)
    # ...but the idempotent replay of the same logical decision converges.
    replay = _ask(qbase, brief=brief_id, decision_id=did)
    assert replay["status"] == "existing"


def test_ask_idempotent_after_answer_returns_existing_answered_record(qbase):
    did = _identity()
    _ask(qbase, decision_id=did)
    decisions.answer(did, "Option B.", queue_base=qbase)
    replay = _ask(qbase, decision_id=did)
    assert replay["status"] == "existing"
    assert replay["envelope"]["status"] == "answered"
    assert replay["envelope"]["answer"] == "Option B."
    assert len(list((qbase / "decisions" / "answered").glob("*.md"))) == 1


def test_ask_idempotent_after_resolve_reports_already_resolved_no_duplicate(qbase):
    did = _identity()
    _ask(qbase, decision_id=did)
    decisions.answer(did, "Option A.", queue_base=qbase)
    decisions.consume_answer(did, queue_base=qbase)
    replay = _ask(qbase, decision_id=did)
    assert replay["status"] == "already-resolved"
    assert not list((qbase / "decisions" / "open").glob("*.md"))
    assert len(list((qbase / "decisions" / "resolved").glob("*.md"))) == 1


def test_load_decision_envelope_roundtrips_record_content(qbase):
    opts = ["A — strict: tradeoff X", "B — lenient: tradeoff Y"]
    result = _ask(
        qbase,
        options=opts,
        repo="repo-a",
        source="check_brief_staleness",
        subject="brief-123",
        run_id="go-1",
        dispatch_mode="adapter",
    )
    env = decisions.load_decision_envelope(result["id"], qbase)
    assert env["status"] == "open"
    assert env["answer"] == ""
    assert env["options"] == opts
    assert env["question"].startswith("Should exports include archived rows?")
    assert env["created_at"]
    assert env["superseded_by"] is None
    assert env["provenance"] == {
        "source": "check_brief_staleness",
        "repo": "repo-a",
        "subject": "brief-123",
        "run_id": "go-1",
        "dispatch_mode": "adapter",
    }
    # the loaded shape satisfies the same contract as the built one
    decisions.parse_pending_decision_envelope(env)
    decisions.answer(result["id"], "Take option 2.", queue_base=qbase)
    answered_env = decisions.load_decision_envelope(result["id"], qbase)
    assert answered_env["status"] == "answered"
    assert answered_env["answer"] == "Take option 2."
    assert answered_env["answered_at"]
    assert decisions.load_decision_envelope("nope", qbase) is None


def _answered_env(qbase, **kw):
    result = _ask(qbase, **kw)
    decisions.answer(result["id"], "Ship Option B.", queue_base=qbase)
    return decisions.load_decision_envelope(result["id"], qbase)


def test_validate_accepts_answer_with_matching_provenance(qbase):
    env = _answered_env(qbase, source="guard-a", repo="/repos/a", subject="subj-1")
    out = decisions.validate_decision_answer(
        env,
        expected_source="guard-a",
        expected_repo="/repos/a",
        expected_subject="subj-1",
    )
    assert out["valid"] is True
    assert out["reasons"] == []


def test_validate_reports_every_provenance_mismatch_at_once(qbase):
    env = _answered_env(qbase, source="guard-a", subject="subj-1")
    out = decisions.validate_decision_answer(
        env, expected_source="guard-b", expected_subject="subj-2"
    )
    assert out["valid"] is False
    joined = "\n".join(out["reasons"])
    assert "source mismatch" in joined and "subject mismatch" in joined


def test_validate_refuses_open_decision_and_superseded_answer(qbase):
    result = _ask(qbase)
    open_env = decisions.load_decision_envelope(result["id"], qbase)
    out = decisions.validate_decision_answer(open_env)
    assert out["valid"] is False
    assert any("not 'answered'" in r for r in out["reasons"])

    answered = dict(
        open_env,
        status="answered",
        answer="x",
        answered_at=dt.datetime.now().astimezone().isoformat(),
    )
    superseded = dict(answered, superseded_by="dec-replacement")
    out = decisions.validate_decision_answer(superseded)
    assert out["valid"] is False
    assert any("superseded by 'dec-replacement'" in r for r in out["reasons"])


def test_validate_freshness_window_rejects_stale_answer(qbase):
    env = _answered_env(qbase)
    fresh_now = dt.datetime.now().astimezone() + dt.timedelta(minutes=1)
    out = decisions.validate_decision_answer(env, max_age_seconds=3600, now=fresh_now)
    assert out["valid"] is True
    stale_now = dt.datetime.now().astimezone() + dt.timedelta(hours=5)
    out = decisions.validate_decision_answer(env, max_age_seconds=3600, now=stale_now)
    assert out["valid"] is False
    assert any("stale" in r for r in out["reasons"])


def test_validate_flags_unparsable_and_time_traveling_answers(qbase, monkeypatch):
    result = _ask(qbase)
    decisions.answer(result["id"], "B.", queue_base=qbase)
    rec = qbase / "decisions" / "answered" / f"{result['id']}.md"
    work_queue._set_fm_fields(rec, {"answered-at": "not-a-timestamp"})
    env = decisions.load_decision_envelope(result["id"], qbase)
    out = decisions.validate_decision_answer(env)
    assert out["valid"] is False
    assert any("unparsable" in r for r in out["reasons"])

    work_queue._set_fm_fields(
        rec,
        {
            "created": "2099-01-01T00:00:00+00:00",
            "answered-at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        },
    )
    env = decisions.load_decision_envelope(result["id"], qbase)
    out = decisions.validate_decision_answer(env)
    assert out["valid"] is False
    assert any("precedes created_at" in r for r in out["reasons"])


# ---------------------------------------------------------------------------
# consume: apply an answered decision exactly once


def test_consume_archives_once_stamps_consumer_and_returns_text(qbase):
    result = _ask(qbase)
    decisions.answer(result["id"], "Ship Option B.", queue_base=qbase)
    out = decisions.consume_answer(
        result["id"], consumed_by="dispatch-42", queue_base=qbase
    )
    assert out["status"] == "consumed"
    assert out["answer"] == "Ship Option B."
    assert out["consumed_by"] == "dispatch-42"
    resolved = qbase / "decisions" / "resolved" / f"{result['id']}.md"
    assert resolved.is_file()
    fm, body = split_frontmatter(resolved.read_text(encoding="utf-8"))
    assert fm["consumed-by"] == "dispatch-42"
    assert fm["consumed-at"] and fm["resolved-at"]
    assert "Ship Option B." in body
    # a second consume of the same answer refuses -- it was applied once
    again = decisions.consume_answer(result["id"], queue_base=qbase)
    assert again["status"] == "already-consumed"
    assert again["consumed_by"] == "dispatch-42"


def test_consume_refuses_unknown_open_and_textless_records(qbase):
    assert decisions.consume_answer("nope", queue_base=qbase)["status"] == "not-found"
    result = _ask(qbase)
    assert (
        decisions.consume_answer(result["id"], queue_base=qbase)["status"]
        == "still-open"
    )
    # hand-moved into answered/ without ever writing an answer: fail closed
    src = qbase / "decisions" / "open" / f"{result['id']}.md"
    dst_dir = qbase / "decisions" / "answered"
    dst_dir.mkdir(parents=True, exist_ok=True)
    src.rename(dst_dir / src.name)
    out = decisions.consume_answer(result["id"], queue_base=qbase)
    assert out["status"] == "unanswered"


def test_consume_clears_only_its_own_brief_stamp(qbase):
    brief_id = _mk_picked_brief(qbase)
    result = _ask(qbase, brief=brief_id)
    decisions.answer(result["id"], "B.", queue_base=qbase)
    out = decisions.consume_answer(result["id"], queue_base=qbase)
    assert out["brief_cleared"] is True
    fm, _ = split_frontmatter(
        (qbase / "picked" / f"{brief_id}.md").read_text(encoding="utf-8")
    )
    assert "awaiting-decision" not in fm

    # a stamp pointing at a different decision must survive consumption
    other = _ask(qbase)
    work_queue._set_fm_fields(
        qbase / "picked" / f"{brief_id}.md", {"awaiting-decision": other["id"]}
    )
    decisions.answer(other["id"], "A.", queue_base=qbase)
    out = decisions.consume_answer(other["id"], queue_base=qbase)
    assert out["brief_cleared"] is False


# ---------------------------------------------------------------------------
# supersede: retire an unresolved decision when facts changed


def test_supersede_retires_open_decision_links_replacement_clears_brief(qbase):
    brief_id = _mk_picked_brief(qbase)
    old = _ask(qbase, brief=brief_id, source="check_spec_collision", subject="change-x")
    replacement = decisions.decision_identity(
        "check_spec_collision", "repo-a", "change-x moved to spec-z"
    )

    out = decisions.supersede(
        old["id"], replacement, reason="target moved", queue_base=qbase
    )
    assert out["status"] == "superseded"
    assert out["superseded_by"] == replacement
    assert out["brief_cleared"] is True

    retired = qbase / "decisions" / "resolved" / f"{old['id']}.md"
    assert retired.is_file()
    fm, _body = split_frontmatter(retired.read_text(encoding="utf-8"))
    assert fm["superseded-by"] == replacement
    assert fm["superseded-reason"] == "target moved"
    assert fm["superseded-at"]
    brief_fm, _ = split_frontmatter(
        (qbase / "picked" / f"{brief_id}.md").read_text(encoding="utf-8")
    )
    assert "awaiting-decision" not in brief_fm

    env = decisions.load_decision_envelope(old["id"], qbase)
    assert env["status"] == "resolved"
    assert env["superseded_by"] == replacement
    verdict = decisions.validate_decision_answer(dict(env, status="answered"))
    assert verdict["valid"] is False
    assert any(replacement in r for r in verdict["reasons"])
    # the brief is free to get a fresh decision under the replacement id
    new = _ask(qbase, brief=brief_id, decision_id=replacement, supersedes=old["id"])
    assert new["status"] == "created"
    new_fm, _ = split_frontmatter(
        (qbase / "decisions" / "open" / f"{replacement}.md").read_text(encoding="utf-8")
    )
    assert new_fm["supersedes"] == old["id"]


def test_supersede_requires_new_id_and_reports_terminal_states(qbase):
    with pytest.raises(ValueError, match="--new-decision-id is required"):
        decisions.supersede("whatever", "  ", queue_base=qbase)
    assert (
        decisions.supersede("nope", "dec-x", queue_base=qbase)["status"] == "not-found"
    )
    result = _ask(qbase)
    decisions.answer(result["id"], "A.", queue_base=qbase)
    decisions.resolve_decision(result["id"], queue_base=qbase)
    out = decisions.supersede(result["id"], "dec-x", queue_base=qbase)
    assert out["status"] == "already-resolved"


# ---------------------------------------------------------------------------
# CLI: consume / supersede verbs


def test_cli_consume_and_supersede_roundtrip(qbase, capsys):
    result = _ask(qbase)
    capsys.readouterr()

    rc = decisions.main(["--queue-dir", str(qbase), "consume", result["id"], "--json"])
    assert rc == 1  # still-open refuses
    capsys.readouterr()

    decisions.answer(result["id"], "B.", queue_base=qbase)
    rc = decisions.main(
        [
            "--queue-dir",
            str(qbase),
            "consume",
            result["id"],
            "--consumed-by",
            "cli-test",
            "--json",
        ]
    )
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["status"] == "consumed"

    other = _ask(qbase)
    rc = decisions.main(
        [
            "--queue-dir",
            str(qbase),
            "supersede",
            other["id"],
            "--new-decision-id",
            "dec-replacement",
            "--reason",
            "facts changed",
            "--json",
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "superseded"
    assert out["id"] == other["id"]
    assert out["superseded_by"] == "dec-replacement"
    retired = qbase / "decisions" / "resolved" / f"{other['id']}.md"
    fm, _ = split_frontmatter(retired.read_text(encoding="utf-8"))
    assert fm["superseded-by"] == "dec-replacement"
