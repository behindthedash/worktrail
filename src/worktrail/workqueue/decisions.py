"""Human decision queue: let an unattended agent hand a genuine product
decision to a human and resume cleanly once it is answered.

Before this module, an auto-mode one-shot that hit `blocked_product_decision`
had exactly one move: leave the brief stranded in `picked/` and hope a human
eventually opened an interactive session to notice it. Nothing surfaced the
question, nothing carried the answer back, and the drain counted the block
toward its circuit breaker.

The decision queue closes that loop with three directories under the
work-queue root (sibling of `queue/` and `picked/`, so the same private git
backup covers them):

    decisions/open/       questions awaiting a human
    decisions/answered/   answered by a human, not yet consumed by an agent
    decisions/resolved/   consumed; kept as the audit trail

Lifecycle:

1. **ask** (agent): create `open/<id>.md` with a structured record — question,
   why this is a product decision rather than an engineering call, what was
   attempted, at least two concrete options, and a recommendation. With
   `--brief --release`, the source brief is stamped `awaiting-decision: <id>`
   and released back to `queue/`, where `work_queue.list` reports it blocked
   until the decision is answered. The one-shot then terminates instead of
   lingering.
2. **answer** (human): `worktrail-decision answer <id> --answer "..."` writes
   the `## Answer` section, stamps `status: answered`, and moves the file to
   `answered/`. (Editing the markdown by hand and moving the file works too —
   the directory, not the status field, is the arbiter.)
3. The brief unblocks automatically (`work_queue.list` sees the decision is no
   longer open), so the next drain pass claims it; the picking session reads
   the answer (`show`), continues from the blocked point, and **resolve**s the
   decision, which archives it to `resolved/` and strips the brief's
   `awaiting-decision` field.

Machine contract on top of that lifecycle (provider-neutral, versioned):
guards file decisions with a deterministic `decision_identity()` id so a
re-run converges on the existing record instead of duplicating it, carry
provenance (`source`, `subject`, `run-id`, `dispatch-mode`) in the record's
frontmatter, and pass a versioned JSON envelope (`pending_decision_envelope`,
schema `worktrail.pending-decision`) to their caller. The resuming side loads
it with `load_decision_envelope()`, gates on `validate_decision_answer()`
(provenance match, not superseded, answered-after-asked, freshness window),
applies it once via `consume_answer()` (stamps `consumed-by`, refuses a
second consume), and retires changed questions with `supersede()`.

Guardrails against becoming a laziness escape hatch: `ask` refuses records
missing any structured field or offering fewer than two options, refuses a
second open decision for the same brief, and the drain still counts a
`blocked_product_decision` one-shot that did NOT file a decision toward its
circuit breaker.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ..shared.brief_frontmatter import split_frontmatter
from . import work_queue
from .slug import fallback_slugify

STATUSES = ("open", "answered", "resolved")

_PENDING_ANSWER = "_Pending — a human replaces this line with the decision._"


# ---------------------------------------------------------------------------
# Versioned pending-decision envelope
#
# Guards and resume surfaces exchange decisions as a provider-neutral JSON
# envelope, never as ad-hoc dicts or prose: a guard (spec-collision,
# related-brief-claims) builds one with
# `pending_decision_envelope()`, files it via `ask(decision_id=...)`, and an
# attended host answers it; the resuming run loads it back with
# `load_decision_envelope()` and must validate it with
# `validate_decision_answer()` before acting on the answer. `version` makes
# the contract evolvable: readers reject versions they don't understand
# instead of misreading fields.


DECISION_ENVELOPE_SCHEMA = "worktrail.pending-decision"
DECISION_ENVELOPE_VERSION = 1

_ENVELOPE_STATUSES = ("pending",) + STATUSES

_REQUIRED_ENVELOPE_FIELDS = (
    "schema", "version", "decision_id", "status", "question", "options",
    "created_at", "provenance",
)


class DecisionEnvelopeError(ValueError):
    """An envelope is not a valid worktrail pending-decision envelope."""


def decision_identity(source: str, repo: str, subject: str,
                      question: str = "") -> str:
    """Deterministic decision id for one logical pending decision.

    Same inputs -> same id on every re-run, so a guard that fires twice on
    unchanged facts files ONE decision record (idempotent ask) instead of a
    growing pile of duplicates. Different subject/question -> different id.
    The slug keeps ids readable in listings; the digest guarantees uniqueness.
    """
    for name, value in (("source", source), ("repo", repo),
                        ("subject", subject)):
        if not value or not str(value).strip():
            raise ValueError(
                f"decision_identity {name} is required: an id derived from "
                "blank provenance would collide across unrelated decisions")
    key = "\x00".join((str(source), str(repo), str(subject), question or ""))
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    slug = _slugify(str(subject))
    return f"dec-{slug[:40]}-{digest}" if slug != "decision" else f"dec-{digest}"


def pending_decision_envelope(
    *,
    decision_id: str,
    question: str,
    options: List[str],
    source: str,
    repo: Optional[str] = None,
    subject: Optional[str] = None,
    brief: Optional[str] = None,
    run_id: Optional[str] = None,
    dispatch_mode: Optional[str] = None,
    supersedes: Optional[str] = None,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the versioned envelope a guard hands to its caller (and prints).

    Provider-neutral by construction: no field names a specific agent harness.
    Provenance (`source`/`repo`/`subject`/`brief`/`run_id`/`dispatch_mode`)
    travels inside the envelope so the resuming side can verify the answer it
    found actually belongs to the question it asked.
    """
    if not decision_id or not decision_id.strip():
        raise ValueError("envelope decision_id is required")
    if not question or not question.strip():
        raise ValueError("envelope question is required")
    clean_options = [o.strip() for o in (options or []) if o and o.strip()]
    if not clean_options:
        raise ValueError("envelope options must be a non-empty list")
    if not source or not str(source).strip():
        raise ValueError("envelope provenance source is required")
    provenance: Dict[str, Any] = {"source": str(source).strip()}
    for key, value in (("repo", repo), ("subject", subject), ("brief", brief),
                       ("run_id", run_id), ("dispatch_mode", dispatch_mode)):
        if value:
            provenance[key] = str(value)
    return {
        "schema": DECISION_ENVELOPE_SCHEMA,
        "version": DECISION_ENVELOPE_VERSION,
        "decision_id": decision_id.strip(),
        "status": "pending",
        "question": question.strip(),
        "options": clean_options,
        "supersedes": supersedes,
        "created_at": created_at or _now_iso(),
        "provenance": provenance,
    }


def parse_pending_decision_envelope(raw: Any) -> Dict[str, Any]:
    """Validate + normalize an envelope received over any boundary.

    Accepts a JSON string or an already-parsed dict. Unknown extra fields are
    ignored (forward-compatible readers); schema mismatch, unsupported
    version, or missing required fields raise DecisionEnvelopeError -- a
    consumer must never act on an envelope it cannot fully read.
    """
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DecisionEnvelopeError(
                f"pending-decision envelope is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise DecisionEnvelopeError(
            f"pending-decision envelope must be a JSON object, got "
            f"{type(raw).__name__}")
    missing = [f for f in _REQUIRED_ENVELOPE_FIELDS if f not in raw]
    if missing:
        raise DecisionEnvelopeError(
            f"pending-decision envelope missing required field(s): "
            f"{', '.join(missing)}")
    if raw["schema"] != DECISION_ENVELOPE_SCHEMA:
        raise DecisionEnvelopeError(
            f"expected schema {DECISION_ENVELOPE_SCHEMA!r}, got {raw['schema']!r}")
    if raw["version"] != DECISION_ENVELOPE_VERSION:
        raise DecisionEnvelopeError(
            f"unsupported envelope version {raw['version']!r} "
            f"(this reader understands {DECISION_ENVELOPE_VERSION})")
    if raw.get("status") not in _ENVELOPE_STATUSES:
        raise DecisionEnvelopeError(
            f"invalid envelope status {raw.get('status')!r}; expected one of "
            f"{', '.join(_ENVELOPE_STATUSES)}")
    if not isinstance(raw.get("question"), str) or not raw["question"].strip():
        raise DecisionEnvelopeError("envelope question must be a non-empty string")
    opts = raw.get("options")
    if not isinstance(opts, list) or not opts or not all(
            isinstance(o, str) and o.strip() for o in opts):
        raise DecisionEnvelopeError(
            "envelope options must be a non-empty list of non-empty strings")
    prov = raw.get("provenance")
    if not isinstance(prov, dict) or not str(prov.get("source") or "").strip():
        raise DecisionEnvelopeError(
            "envelope provenance must be an object with a non-empty 'source'")
    return raw


def decisions_dir(queue_base: Optional[Path] = None) -> Path:
    return Path(queue_base or work_queue.base_dir()).expanduser() / "decisions"


def _slugify(text: str) -> str:
    return fallback_slugify(text, default="decision")


def _now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _read_fm(path: Path) -> Dict[str, Any]:
    try:
        fm, _body = split_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    return fm


def find_decision(identifier: str,
                  queue_base: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Locate a decision by id (exact stem or unique prefix) across all three
    status directories. The directory a record lives in — not its `status:`
    field — is the arbiter, so a hand-moved file is honored.

    Returns {"path", "status", "fm"} or None.
    """
    base = decisions_dir(queue_base)
    hits: List[Dict[str, Any]] = []
    for status in STATUSES:
        d = base / status
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            if md.stem == identifier or md.stem.startswith(identifier):
                hits.append({"path": md, "status": status, "fm": _read_fm(md)})
    exact = [h for h in hits if h["path"].stem == identifier]
    if exact:
        return exact[0]
    return hits[0] if len(hits) == 1 else None


def open_decision_ids(queue_base: Optional[Path] = None) -> List[str]:
    d = decisions_dir(queue_base) / "open"
    if not d.is_dir():
        return []
    return sorted(md.stem for md in d.glob("*.md"))


def decision_status(decision_id: str,
                    queue_base: Optional[Path] = None) -> Optional[str]:
    """`open`/`answered`/`resolved` by directory, or None when the id does not
    resolve (lenient: a deleted record must never wedge its brief)."""
    found = find_decision(decision_id, queue_base)
    return found["status"] if found else None


# ---------------------------------------------------------------------------
# ask


def _require(value: Optional[str], name: str) -> str:
    if not value or not value.strip():
        raise ValueError(
            f"--{name} is required and must be non-empty: a decision record "
            f"without it is not reviewable by a human and will be refused")
    return value.strip()


def ask(
    question: str,
    *,
    background: str,
    why: str,
    context: str,
    options: List[str],
    option_costs: Optional[List[str]] = None,
    recommendation: Optional[str] = None,
    repo: Optional[str] = None,
    brief: Optional[str] = None,
    run_record: Optional[str] = None,
    release_brief: bool = False,
    queue_base: Optional[Path] = None,
    decision_id: Optional[str] = None,
    source: Optional[str] = None,
    subject: Optional[str] = None,
    run_id: Optional[str] = None,
    dispatch_mode: Optional[str] = None,
    supersedes: Optional[str] = None,
) -> Dict[str, Any]:
    """Create an open decision record; optionally block+release its brief.

    Structured fields are mandatory by design (see module docstring): the
    record is the entire interface the human gets, and a lazy one-liner
    punts the real work to them. `background` is the plain-English story —
    what the problem is, why it exists, and how the run got here — written
    for a reader with no context. `options` are listed in the agent's
    priority order; `option_costs`, when given, labels each option with its
    cost/effort tradeoff (index-matched, so the count must agree) so the
    human can weigh quick-to-production against the better long-term
    architecture without reading the code.

    Idempotent identity: pass a deterministic id (`decision_identity()`) plus
    its provenance (`source`, `subject`, `run_id`, `dispatch_mode`,
    `supersedes`) and re-running the same guard on unchanged facts returns
    the existing open/answered record instead of filing a duplicate; only a
    resolved record reports "already-resolved" without recreating (supersede
    it explicitly when circumstances genuinely changed). The result carries
    the versioned envelope under `"envelope"` either way.
    """
    question = _require(question, "question")
    background = _require(background, "background")
    why = _require(why, "why")
    context = _require(context, "context")
    options = [o.strip() for o in options if o and o.strip()]
    if len(options) < 2:
        raise ValueError(
            "at least two --option entries are required: a decision with one "
            "option is not a decision, it is a notification")
    option_costs = [c.strip() for c in (option_costs or []) if c and c.strip()]
    if option_costs and len(option_costs) != len(options):
        raise ValueError(
            f"--option-cost must be given once per --option in the same "
            f"order ({len(options)} option(s), {len(option_costs)} cost(s))")
    if release_brief and not brief:
        raise ValueError("--release requires --brief")

    base = Path(queue_base or work_queue.base_dir()).expanduser()

    # Idempotency first: a re-run whose derived id already has a record must
    # converge on it -- including past the duplicate-open-per-brief guard,
    # which would otherwise refuse the replay.
    existing: Optional[Dict[str, Any]] = None
    if decision_id and decision_id.strip():
        decision_id = decision_id.strip()
        found = find_decision(decision_id, base)
        if found is not None and found["path"].stem == decision_id:
            existing = found

    if existing is not None:
        path = existing["path"]
        status = ("already-resolved" if existing["status"] == "resolved"
                  else "existing")
        envelope = load_decision_envelope(decision_id, base) or {}
    else:
        if brief:
            for existing_id in open_decision_ids(base):
                rec = find_decision(existing_id, base)
                if rec and str(rec["fm"].get("brief") or "") == brief:
                    raise ValueError(
                        f"brief {brief} already has an open decision "
                        f"({existing_id}); answer or resolve it first")

        open_dir = decisions_dir(base) / "open"
        open_dir.mkdir(parents=True, exist_ok=True)
        now = dt.datetime.now().astimezone()
        stem = decision_id or f"{now:%Y%m%d-%H%M%S}-{_slugify(question)}"
        path = open_dir / f"{stem}.md"
        suffix = 2
        while path.exists():
            path = open_dir / f"{stem}-{suffix}.md"
            suffix += 1
        decision_id = path.stem

        frontmatter: Dict[str, Any] = {
            "id": path.stem,
            "created": now.isoformat(timespec="seconds"),
            "status": "open",
        }
        for key, value in (("repo", repo), ("brief", brief),
                           ("run-record", run_record), ("source", source),
                           ("subject", subject), ("run-id", run_id),
                           ("dispatch-mode", dispatch_mode),
                           ("supersedes", supersedes)):
            if value:
                frontmatter[key] = value

        option_lines = "\n".join(
            f"{n}. {opt}"
            + (f"\n   - Cost: {option_costs[n - 1]}" if option_costs else "")
            for n, opt in enumerate(options, start=1))
        body = (
            f"## Question\n\n{question}\n\n"
            f"## Background\n\n{background}\n\n"
            f"## Why a human decision is needed\n\n{why}\n\n"
            f"## Context (what was attempted)\n\n{context}\n\n"
            f"## Options\n\n"
            f"_In priority order (the agent's preference first). Answer with a "
            f"number, or write your own direction._\n\n{option_lines}\n"
            + (f"\n## Recommendation\n\n{recommendation.strip()}\n"
               if recommendation and recommendation.strip() else "")
            + f"\n## Answer\n\n{_PENDING_ANSWER}\n"
        )
        path.write_text(
            "---\n" + yaml.safe_dump(frontmatter, sort_keys=False,
                                     default_flow_style=False, allow_unicode=True)
            + "---\n\n" + body,
            encoding="utf-8")
        status = "created"
        envelope = pending_decision_envelope(
            decision_id=decision_id, question=question, options=options,
            source=source or "manual", repo=repo, subject=subject, brief=brief,
            run_id=run_id, dispatch_mode=dispatch_mode, supersedes=supersedes,
            created_at=frontmatter["created"])

    result: Dict[str, Any] = {"status": status, "id": decision_id,
                              "path": str(path), "brief": brief,
                              "released": False, "error": None,
                              "envelope": envelope}
    if brief:
        stamped = _stamp_brief(brief, path.stem, base)
        result["brief_stamped"] = stamped
        if release_brief:
            if not stamped:
                result["error"] = (
                    f"could not stamp brief {brief!r} with awaiting-decision: "
                    f"{path.stem} (not found under queue/ or picked/, or "
                    f"unwritable) -- the decision record was created at "
                    f"{path}, but the brief was NOT released and stays "
                    f"claimed; release it manually or re-file with the "
                    f"correct --brief value")
            else:
                prev = os.environ.get("WORK_QUEUE_DIR")
                os.environ["WORK_QUEUE_DIR"] = str(base)
                try:
                    released = work_queue.release(brief)
                finally:
                    if prev is None:
                        os.environ.pop("WORK_QUEUE_DIR", None)
                    else:
                        os.environ["WORK_QUEUE_DIR"] = prev
                result["released"] = released.get("status") == "released"
                result["release_detail"] = released.get("status")
                if not result["released"]:
                    result["error"] = (
                        f"brief {brief!r} was stamped with awaiting-decision: "
                        f"{path.stem} but release failed "
                        f"(status={released.get('status')!r}) -- it remains "
                        f"in picked/ instead of being requeued blocked; "
                        f"release it manually with `worktrail-work-queue "
                        f"release`")
    work_queue._git_backup(f"decision ask {path.stem}")
    return result


def _brief_path(brief_id: str, base: Path) -> Optional[Path]:
    for folder in (base / "picked", base / "queue"):
        res = work_queue.resolve(brief_id, folder)
        if res["status"] == "match":
            return Path(res["candidates"][0])
    return None


def _stamp_brief(brief_id: str, decision_id: str, base: Path) -> bool:
    """Best-effort `awaiting-decision:` stamp on the brief, wherever it lives."""
    path = _brief_path(brief_id, base)
    if path is None:
        return False
    try:
        work_queue._set_fm_fields(path, {"awaiting-decision": decision_id})
    except (OSError, ValueError):
        return False
    return True


def _clear_brief_stamp(brief_id: str, decision_id: str, base: Path) -> bool:
    """Remove the brief's `awaiting-decision:` stamp — only when it points at
    this decision id, never at an unrelated link some other flow just wrote."""
    path = _brief_path(brief_id, base)
    if path is None:
        return False
    try:
        fm, _body = split_frontmatter(path.read_text(encoding="utf-8"))
        if str(fm.get("awaiting-decision") or "") != decision_id:
            return False
        work_queue._remove_fm_field(path, "awaiting-decision")
    except (OSError, ValueError):
        return False
    return True


# ---------------------------------------------------------------------------
# answer / resolve


def _move_with_status(found: Dict[str, Any], new_status: str,
                      stamp_key: str, queue_base: Optional[Path]) -> Path:
    base = decisions_dir(queue_base)
    dst_dir = base / new_status
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / found["path"].name
    os.rename(found["path"], dst)
    work_queue._set_fm_fields(dst, {"status": new_status, stamp_key: _now_iso()})
    return dst


def answer(identifier: str, answer_text: str,
           queue_base: Optional[Path] = None) -> Dict[str, Any]:
    """Record the human's answer and move the record to answered/."""
    if not answer_text or not answer_text.strip():
        raise ValueError("--answer must be non-empty")
    found = find_decision(identifier, queue_base)
    if found is None:
        return {"status": "not-found", "id": identifier}
    if found["status"] == "resolved":
        return {"status": "already-resolved", "id": found["path"].stem}
    content = found["path"].read_text(encoding="utf-8")
    marker = "## Answer"
    idx = content.rfind(marker)
    if idx == -1:
        content = content.rstrip() + f"\n\n{marker}\n\n{answer_text.strip()}\n"
    else:
        content = (content[: idx + len(marker)] + "\n\n"
                   + answer_text.strip() + "\n")
    found["path"].write_text(content, encoding="utf-8")
    if found["status"] == "open":
        dst = _move_with_status(found, "answered", "answered-at", queue_base)
    else:  # already in answered/: update the text in place
        dst = found["path"]
        work_queue._set_fm_fields(dst, {"answered-at": _now_iso()})
    work_queue._git_backup(f"decision answer {dst.stem}")
    return {"status": "answered", "id": dst.stem, "path": str(dst)}


def resolve_decision(identifier: str,
                     queue_base: Optional[Path] = None) -> Dict[str, Any]:
    """Archive a consumed decision and unblock its brief's frontmatter.

    Only an answered decision can be resolved — resolving an open one would
    silently discard the human's pending question.
    """
    found = find_decision(identifier, queue_base)
    if found is None:
        return {"status": "not-found", "id": identifier}
    if found["status"] == "open":
        return {"status": "still-open", "id": found["path"].stem}
    if found["status"] == "resolved":
        return {"status": "already-resolved", "id": found["path"].stem}
    dst = _move_with_status(found, "resolved", "resolved-at", queue_base)
    brief_id = str(found["fm"].get("brief") or "")
    brief_cleared = False
    if brief_id:
        base = Path(queue_base or work_queue.base_dir()).expanduser()
        brief_cleared = _clear_brief_stamp(brief_id, dst.stem, base)
    work_queue._git_backup(f"decision resolve {dst.stem}")
    return {"status": "resolved", "id": dst.stem, "path": str(dst),
            "brief": brief_id or None, "brief_cleared": brief_cleared}


# ---------------------------------------------------------------------------
# envelope round-trip, answer validation/consumption, supersession


def _extract_section(body: str, heading: str) -> Optional[str]:
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$\r?\n(.*?)(?=^##\s|\Z)",
                  body, re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else None


def _extract_options(body: str) -> List[str]:
    """The option lines a rendered record carries back as plain strings."""
    section = _extract_section(body, "Options") or ""
    return [m.group(1).strip()
            for m in re.finditer(r"^\d+\.\s+(.+)$", section, re.MULTILINE)]


def _extract_answer(body: str) -> str:
    section = _extract_section(body, "Answer")
    if section is None:
        return ""
    return "" if section.strip() == _PENDING_ANSWER.strip() else section.strip()


def load_decision_envelope(identifier: str,
                           queue_base: Optional[Path] = None
                           ) -> Optional[Dict[str, Any]]:
    """Rebuild the versioned envelope from a decision record on disk.

    `status` is the directory the record lives in (never the frontmatter
    field); `answer` is empty until a human wrote one; `superseded_by` is set
    when the record was retired by `supersede()`. Returns None for an id that
    does not resolve.
    """
    found = find_decision(identifier, queue_base)
    if found is None:
        return None
    try:
        fm, body = split_frontmatter(found["path"].read_text(encoding="utf-8"))
    except OSError:
        fm, body = found["fm"], ""
    provenance: Dict[str, Any] = {}
    for key, value in (("source", "source"), ("repo", "repo"),
                       ("subject", "subject"), ("brief", "brief"),
                       ("run_id", "run-id"),
                       ("dispatch_mode", "dispatch-mode")):
        if fm.get(value):
            provenance[key] = str(fm[value])
    created = fm.get("created")
    answered_at = fm.get("answered-at")
    envelope: Dict[str, Any] = {
        "schema": DECISION_ENVELOPE_SCHEMA,
        "version": DECISION_ENVELOPE_VERSION,
        "decision_id": found["path"].stem,
        "status": found["status"],
        "question": (_extract_section(body, "Question") or "").splitlines()[0]
                    if _extract_section(body, "Question") else "",
        "options": _extract_options(body),
        "supersedes": fm.get("supersedes"),
        "created_at": str(created) if created else None,
        "answered_at": str(answered_at) if answered_at else None,
        "answer": _extract_answer(body),
        "superseded_by": fm.get("superseded-by"),
        "provenance": provenance,
    }
    return envelope


def validate_decision_answer(
    envelope: Dict[str, Any],
    *,
    expected_source: Optional[str] = None,
    expected_repo: Optional[str] = None,
    expected_subject: Optional[str] = None,
    max_age_seconds: Optional[float] = None,
    now: Optional[dt.datetime] = None,
) -> Dict[str, Any]:
    """Resume-side gate: may this run act on this decision's answer?

    Checks provenance (the recorded source/repo/subject match what the
    resuming run expects), liveness (the record was not superseded after the
    human answered it), and freshness (`answered_at` parses as an aware
    timestamp, is not before the question's `created_at`, and sits within
    `max_age_seconds` of `now` when a window is imposed). Non-raising: the
    result is {"valid": bool, "reasons": [str], ...} so a caller reports every
    failed expectation at once instead of the first.
    """
    reasons: List[str] = []
    if envelope.get("status") != "answered":
        reasons.append(
            f"decision status is {envelope.get('status')!r}, not 'answered'")
    if envelope.get("superseded_by"):
        reasons.append(
            f"decision was superseded by {envelope['superseded_by']!r}")
    prov = envelope.get("provenance") or {}
    for label, expected in (("source", expected_source),
                            ("repo", expected_repo),
                            ("subject", expected_subject)):
        if expected is not None and prov.get(label) != expected:
            reasons.append(
                f"provenance {label} mismatch: recorded "
                f"{prov.get(label)!r} != expected {expected!r}")

    def _parse(value: Any) -> Optional[dt.datetime]:
        if not value or not isinstance(value, str):
            return None
        try:
            parsed = dt.datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None

    answered = _parse(envelope.get("answered_at"))
    if envelope.get("answered_at") and answered is None:
        reasons.append(
            f"answered_at {envelope.get('answered_at')!r} is missing, "
            "unparsable, or timezone-naive")
    created = _parse(envelope.get("created_at"))
    if answered is not None and created is not None and answered < created:
        reasons.append("answered_at precedes created_at (clock skew or tamper)")
    if answered is not None and max_age_seconds is not None:
        current = now or dt.datetime.now().astimezone()
        age = (current - answered).total_seconds()
        if age > max_age_seconds:
            reasons.append(
                f"answer is stale: age {age:.0f}s exceeds the "
                f"{max_age_seconds:.0f}s freshness window")
    return {"valid": not reasons, "reasons": reasons,
            "answered_at": envelope.get("answered_at")}


def consume_answer(identifier: str, consumed_by: Optional[str] = None,
                   queue_base: Optional[Path] = None) -> Dict[str, Any]:
    """Consume an answered decision exactly once.

    The resuming agent's primitive (distinct from the manual-archive
    `resolve_decision`): verifies there IS an answer, archives the record to
    resolved/ stamped with who consumed it and when, unblocks its brief, and
    returns the answer text plus the validated-shape envelope. A second
    consume of the same id refuses with already-consumed -- an answer is
    applied to one continuation, never replayed into another.
    """
    found = find_decision(identifier, queue_base)
    if found is None:
        return {"status": "not-found", "id": identifier}
    if found["status"] == "open":
        return {"status": "still-open", "id": found["path"].stem}
    if found["status"] == "resolved":
        return {"status": "already-consumed", "id": found["path"].stem,
                "consumed_by": found["fm"].get("consumed-by")}
    try:
        _fm, body = split_frontmatter(found["path"].read_text(encoding="utf-8"))
    except OSError:
        body = ""
    answer_text = _extract_answer(body)
    if not answer_text:
        # In answered/ by directory but no actual answer text was written --
        # consuming would invent a decision the human never made.
        return {"status": "unanswered", "id": found["path"].stem}
    stamps: Dict[str, str] = {"consumed-at": _now_iso()}
    if consumed_by:
        stamps["consumed-by"] = consumed_by
    dst = _move_with_status(found, "resolved", "resolved-at", queue_base)
    work_queue._set_fm_fields(dst, stamps)
    brief_id = str(found["fm"].get("brief") or "")
    brief_cleared = False
    if brief_id:
        base = Path(queue_base or work_queue.base_dir()).expanduser()
        brief_cleared = _clear_brief_stamp(brief_id, dst.stem, base)
    work_queue._git_backup(f"decision consume {dst.stem}")
    return {"status": "consumed", "id": dst.stem, "path": str(dst),
            "answer": answer_text, "consumed_by": consumed_by or None,
            "brief": brief_id or None, "brief_cleared": brief_cleared}


def supersede(old_identifier: str, new_decision_id: str,
              reason: Optional[str] = None,
              queue_base: Optional[Path] = None) -> Dict[str, Any]:
    """Retire an unresolved decision in favor of a newer one.

    Used when facts changed after a decision was filed (guard re-run on a
    moved target, brief edited between ask and answer): the old record moves
    to resolved/ stamped `superseded-by`/`superseded-at`, its brief stops
    waiting on it, and any answer later found on it fails
    `validate_decision_answer`. The replacement itself must still be filed
    via `ask(decision_id=..., supersedes=...)`.
    """
    if not new_decision_id or not new_decision_id.strip():
        raise ValueError("--new-decision-id is required and must be non-empty")
    new_decision_id = new_decision_id.strip()
    found = find_decision(old_identifier, queue_base)
    if found is None:
        return {"status": "not-found", "id": old_identifier}
    if found["status"] == "resolved":
        return {"status": "already-resolved", "id": found["path"].stem}
    dst = _move_with_status(found, "resolved", "superseded-at", queue_base)
    stamps: Dict[str, str] = {"superseded-by": new_decision_id}
    if reason and reason.strip():
        stamps["superseded-reason"] = reason.strip()
    work_queue._set_fm_fields(dst, stamps)
    brief_id = str(found["fm"].get("brief") or "")
    brief_cleared = False
    if brief_id:
        base = Path(queue_base or work_queue.base_dir()).expanduser()
        brief_cleared = _clear_brief_stamp(brief_id, dst.stem, base)
    work_queue._git_backup(f"decision supersede {dst.stem}")
    return {"status": "superseded", "id": dst.stem, "path": str(dst),
            "superseded_by": new_decision_id, "brief": brief_id or None,
            "brief_cleared": brief_cleared}


# ---------------------------------------------------------------------------
# list / show


def _decision_row(md: Path, status: str, fm: Dict[str, Any],
                  body: str) -> Dict[str, Any]:
    m = re.search(r"^##\s+Question\s*$\r?\n\r?\n?(.+)$", body, re.MULTILINE)
    return {
        "id": md.stem, "status": status, "path": str(md),
        "repo": fm.get("repo"), "brief": fm.get("brief"),
        "created": fm.get("created"),
        "answered_at": fm.get("answered-at"),
        "question": (m.group(1).strip() if m else ""),
    }


def list_decisions(status: Optional[str] = None,
                   queue_base: Optional[Path] = None) -> Dict[str, Any]:
    base = decisions_dir(queue_base)
    rows: List[Dict[str, Any]] = []
    for st in STATUSES:
        if status and st != status:
            continue
        d = base / st
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            fm = _read_fm(md)
            try:
                _fm, body = split_frontmatter(md.read_text(encoding="utf-8"))
            except OSError:
                body = ""
            rows.append(_decision_row(md, st, fm, body))
    return {"decisions": rows}


# ---------------------------------------------------------------------------
# CLI


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Queue product decisions for a human; resume agents once "
                    "answered.")
    parser.add_argument("--queue-dir", type=Path, default=None,
                        help="work-queue base (default: $WORK_QUEUE_DIR)")
    subs = parser.add_subparsers(dest="cmd", required=True)

    ap = subs.add_parser("ask", help="file a decision for a human to answer")
    ap.add_argument("--question", required=True)
    ap.add_argument("--background", required=True,
                    help="plain-English story for a reader with no context: what "
                         "the problem is, why it exists, how we got here")
    ap.add_argument("--why", required=True,
                    help="why this is a product decision, not an engineering call")
    ap.add_argument("--context", required=True,
                    help="what was attempted and the evidence gathered")
    ap.add_argument("--option", action="append", default=[], dest="options",
                    help="a concrete option with its tradeoff, in priority order "
                         "(repeat; >= 2 required)")
    ap.add_argument("--option-cost", action="append", default=[],
                    dest="option_costs",
                    help="cost/effort label for the corresponding --option, in the "
                         "same order (e.g. 'low -- config only, ships today' vs "
                         "'high -- better long-term architecture, ~3 days'); when "
                         "used, give exactly one per --option")
    ap.add_argument("--recommendation",
                    help="which option to take -- conditioned on product priority "
                         "when it genuinely depends (e.g. 'quick to production: "
                         "option 1; long-term architecture: option 2')")
    ap.add_argument("--repo")
    ap.add_argument("--brief", help="source brief id to stamp awaiting-decision")
    ap.add_argument("--run-record")
    ap.add_argument("--release", action="store_true",
                    help="release the --brief back to the queue (blocked until answered)")

    lp = subs.add_parser("list", help="list decision records")
    lp.add_argument("--status", choices=STATUSES)

    sp = subs.add_parser("show", help="print a decision record")
    sp.add_argument("identifier")

    anp = subs.add_parser("answer", help="record the human's answer")
    anp.add_argument("identifier")
    anp.add_argument("--answer", required=True)

    rp = subs.add_parser("resolve", help="archive a consumed decision")
    rp.add_argument("identifier")

    cp = subs.add_parser("consume",
                         help="consume an answered decision exactly once")
    cp.add_argument("identifier")
    cp.add_argument("--consumed-by", default=None,
                    help="who is applying this answer (agent/dispatch id)")

    sup = subs.add_parser("supersede",
                          help="retire an unresolved decision for a newer one")
    sup.add_argument("identifier")
    sup.add_argument("--new-decision-id", required=True,
                     help="the replacement decision's id")
    sup.add_argument("--reason", default=None)

    for p in (ap, lp, sp, anp, rp, cp, sup):
        p.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    qb = args.queue_dir
    try:
        if args.cmd == "ask":
            result = ask(args.question, background=args.background,
                         why=args.why, context=args.context,
                         options=args.options, option_costs=args.option_costs,
                         recommendation=args.recommendation, repo=args.repo,
                         brief=args.brief, run_record=args.run_record,
                         release_brief=args.release, queue_base=qb)
        elif args.cmd == "list":
            result = list_decisions(args.status, queue_base=qb)
        elif args.cmd == "show":
            found = find_decision(args.identifier, qb)
            if found is None:
                print(f"error: no decision matches {args.identifier!r}",
                      file=sys.stderr)
                return 1
            content = found["path"].read_text(encoding="utf-8")
            if args.as_json:
                try:
                    _fm, body = split_frontmatter(content)
                except OSError:
                    body = ""
                row = _decision_row(found["path"], found["status"], found["fm"], body)
                row["content"] = content
                print(json.dumps(row, indent=2))
                return 0
            print(content)
            return 0
        elif args.cmd == "answer":
            result = answer(args.identifier, args.answer, queue_base=qb)
        elif args.cmd == "consume":
            result = consume_answer(args.identifier, args.consumed_by,
                                    queue_base=qb)
        elif args.cmd == "supersede":
            result = supersede(args.identifier, args.new_decision_id,
                               reason=args.reason, queue_base=qb)
        else:
            result = resolve_decision(args.identifier, queue_base=qb)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.cmd == "ask" and result.get("error"):
        print(f"warning: {result['error']}", file=sys.stderr)

    if args.as_json:
        print(json.dumps(result, indent=2))
    elif args.cmd == "list":
        rows = result["decisions"]
        if not rows:
            print("no decisions")
        for row in rows:
            print(f"[{row['status']}] {row['id']}"
                  + (f" (brief {row['brief']})" if row["brief"] else "")
                  + (f" — {row['question']}" if row["question"] else ""))
    else:
        print(result.get("path") or result.get("status"))
    failed = isinstance(result, dict) and (
        result.get("status") in ("not-found", "still-open", "unanswered")
        or (args.cmd == "ask" and result.get("error")))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
