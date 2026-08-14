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

Guardrails against becoming a laziness escape hatch: `ask` refuses records
missing any structured field or offering fewer than two options, refuses a
second open decision for the same brief, and the drain still counts a
`blocked_product_decision` one-shot that did NOT file a decision toward its
circuit breaker.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ..shared.brief_frontmatter import split_frontmatter
from . import work_queue

STATUSES = ("open", "answered", "resolved")

_PENDING_ANSWER = "_Pending — a human replaces this line with the decision._"


def decisions_dir(queue_base: Optional[Path] = None) -> Path:
    return Path(queue_base or work_queue.base_dir()).expanduser() / "decisions"


def _slugify(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())[:5]
    return "-".join(words) or "decision"


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
    if brief:
        for existing_id in open_decision_ids(base):
            existing = find_decision(existing_id, base)
            if existing and str(existing["fm"].get("brief") or "") == brief:
                raise ValueError(
                    f"brief {brief} already has an open decision "
                    f"({existing_id}); answer or resolve it first")

    open_dir = decisions_dir(base) / "open"
    open_dir.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now().astimezone()
    stem = f"{now:%Y%m%d-%H%M%S}-{_slugify(question)}"
    path = open_dir / f"{stem}.md"
    suffix = 2
    while path.exists():
        path = open_dir / f"{stem}-{suffix}.md"
        suffix += 1

    frontmatter: Dict[str, Any] = {
        "id": path.stem,
        "created": now.isoformat(timespec="seconds"),
        "status": "open",
    }
    for key, value in (("repo", repo), ("brief", brief),
                       ("run-record", run_record)):
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

    result: Dict[str, Any] = {"status": "created", "id": path.stem,
                              "path": str(path), "brief": brief,
                              "released": False, "error": None}
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
        path = _brief_path(brief_id, base)
        if path is not None:
            try:
                work_queue._remove_fm_field(path, "awaiting-decision")
                brief_cleared = True
            except (OSError, ValueError):
                pass
    work_queue._git_backup(f"decision resolve {dst.stem}")
    return {"status": "resolved", "id": dst.stem, "path": str(dst),
            "brief": brief_id or None, "brief_cleared": brief_cleared}


# ---------------------------------------------------------------------------
# list / show


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
            m = re.search(r"^##\s+Question\s*$\r?\n\r?\n?(.+)$", body, re.MULTILINE)
            rows.append({
                "id": md.stem, "status": st, "path": str(md),
                "repo": fm.get("repo"), "brief": fm.get("brief"),
                "created": fm.get("created"),
                "answered_at": fm.get("answered-at"),
                "question": (m.group(1).strip() if m else ""),
            })
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

    for p in (ap, lp, sp, anp, rp):
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
            print(found["path"].read_text(encoding="utf-8"))
            return 0
        elif args.cmd == "answer":
            result = answer(args.identifier, args.answer, queue_base=qb)
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
        result.get("status") in ("not-found", "still-open")
        or (args.cmd == "ask" and result.get("error")))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
