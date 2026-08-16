## 1. `dashboard.py` — decisions input + category wiring

- [ ] 1.1 Add a `--decisions-json` CLI argument to `dashboard.py`'s `main()` argparse (JSON
      string from `worktrail-decision list --status open --json`), and parse it into an
      `open_decisions: List[Dict[str, Any]]` list, mirroring the existing `--queue-json` /
      `queue_briefs` parse block exactly (same `parsed.get("decisions", [])` pattern, same
      degrade-to-`[]`-on-parse-failure behavior).
- [ ] 1.2 Add a `"decisions"` entry to `_CATEGORY_DESC` describing the category (e.g. "Answer a
      blocked product decision — unblocks the brief automatically.").
- [x] 1.3 Extend `build_category_actions` with a new `open_decisions: Optional[List[Dict[str,
      Any]]] = None` parameter. When `len(open_decisions or [])` is nonzero, append a `decisions`
      category (`label: "Open decisions (N)"`) to `categories` **before** the existing
      `ready`/`needs-tasks`/`workqueue`/`new-work` appends, so it is the first entry and — under
      the existing `categories[:4]` truncation — the last one dropped, not the first. (Requirement:
      Open decisions surface as an interactive dashboard picker category)
- [x] 1.4 Extend `build_category_items` with the same `open_decisions` parameter. Build a
      `"decisions"` entry in the returned dict: for each open decision (capped at 4, matching
      every other category's own cap — no dedicated overflow item, per design.md), a
      `type: "decision"` item with `action: "answer-decision"`, `id` (the decision id), `label`
      (the record's `question` field, truncated to 60 chars, falling back to `id`),
      `description`, and `repo`/`brief` passed through when present. Omit the `"decisions"` key
      entirely (not an empty list) when there are no open decisions, matching how every other
      category key is already always-present-but-populated today, so existing callers that never
      pass `open_decisions` see byte-identical output. (Requirement: Open decisions surface as an
      interactive dashboard picker category)
- [x] 1.5 Wire `open_decisions` through `main()`: parse `--decisions-json` per 1.1, pass
      `open_decisions=open_decisions` to both `build_category_actions` calls and both
      `build_category_items` calls (the `--repos` multi-repo branch and the `--root` single-repo
      branch), and echo `"open_decisions": open_decisions` in both branches' JSON output dict
      alongside the existing `"clusters"` field.

## 2. Tests

- [x] 2.1 `tests/router/test_dashboard.py`: cover `build_category_actions` with open decisions —
      the `decisions` category appears with the correct count and is omitted when
      `open_decisions` is `None`/`[]`; it is ranked ahead of `ready` in the returned list; and
      when decisions + ready + needs-tasks + workqueue are all simultaneously populated, the
      returned categories are exactly `decisions`, `ready`, `needs-tasks`, `workqueue` (four
      entries, `new-work` omitted) — never dropping any of the three pre-existing categories.
      (Requirement: Open decisions surface as an interactive dashboard picker category)
- [x] 2.2 `tests/router/test_dashboard.py`: cover `build_category_items` with open decisions —
      returned `"decisions"` items carry `type`, `action: "answer-decision"`, `id`, `label`
      (derived from `question`), `repo`, `brief`; capped at 4 when more than 4 are open; and the
      `"decisions"` key is absent from the result when there are no open decisions. (Requirement:
      Open decisions surface as an interactive dashboard picker category)
- [x] 2.3 `tests/router/test_dashboard.py`: cover `main()`'s `--decisions-json` CLI flag
      end-to-end — valid JSON surfaces in both the `category_actions`/`category_items` picker
      data and the echoed `open_decisions` field of `--json` output; malformed JSON degrades to
      an empty list (no crash), matching `--queue-json`'s existing malformed-input behavior.

## 3. `worktrail-go` skill wiring

- [ ] 3.1 `skills/worktrail-go/SKILL.md` Phase 1: alongside the existing `QUEUE_JSON=$(worktrail-
      work-queue list --json ...)` fetch, add `DECISIONS_JSON=$(worktrail-decision list --status
      open --json ...)` and pass `--decisions-json "$DECISIONS_JSON"` to both `worktrail-
      dashboard` invocations (the `in-repo` and multi-repo branches).
- [ ] 3.2 `skills/worktrail-go/SKILL.md` Phase 2's action → dispatch table: add an
      `answer-decision` row pointing at the new reference doc from task 3.3. (Requirement:
      Selecting an open decision answers it interactively without a manual CLI call)
- [ ] 3.3 Create `skills/worktrail-go/references/answer-decision.md` documenting the interactive
      procedure: run `worktrail-decision show <id>` and read its Question/Background/Options
      (with per-option costs when present) sections directly as text; present them via an
      interactive choice prompt in the record's priority order, with a free-text fallback for a
      direction not among the listed options; on a choice, run `worktrail-decision answer <id>
      --answer "<full option text>"` (or the typed free text verbatim), which unblocks the
      linked brief per the unchanged existing `human-decision-queue` behavior; confirm the
      outcome to the human; and explicitly do **not** call `worktrail-decision resolve` from this
      flow — resolution stays the consuming agent's job when it later resumes the brief, per
      `decision-queue.md`'s unchanged "Resuming from an answered decision" procedure. (Requirement:
      Selecting an open decision answers it interactively without a manual CLI call)
- [x] 3.4 `skills/worktrail-go/references/dashboard-render.md`: document the new `decisions`
      category in the `category_actions`/`category_items` field contract (category ordering,
      the `type: "decision"` item shape, the `answer-decision` action, and the "new-work is the
      one that yields its slot" truncation note).
- [ ] 3.5 `skills/worktrail-help/SKILL.md`: update the "Answering a filed decision" section to
      note the dashboard now surfaces open decisions directly as an interactive picker category,
      keeping the existing `worktrail-decision list`/`answer` CLI pointer as the non-interactive
      alternative.

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`; both must be green, including the new dashboard
      coverage from section 2 and the existing `tests/test_plugin_surface.py` cross-skill
      reference-link checks against the new `answer-decision.md` file.
