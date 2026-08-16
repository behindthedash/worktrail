# Answer a decision (Route picker action `answer-decision`)

The dashboard's `decisions` category surfaces open human-decision-queue records
(`references/decision-queue.md`) as picker items so a human answers one from the same `/go`
session instead of running `worktrail-decision` by hand. This is purely the interactive front
end for the existing `worktrail-decision answer` behavior — it does not change what filing or
resolving a decision means.

The dispatched item carries the decision's `id` directly (`category_items.decisions[n].id`) —
use it as-is, do not re-derive it from the label.

## 1. Read the record

```bash
worktrail-decision show "$ID"
```

This prints the record's raw markdown. Read the `## Question`, `## Background`, and `## Options`
sections directly as text — do not re-summarize or re-derive them. `## Options` is a numbered
list in the filer's priority order; an option may carry an indented `- Cost: ...` sub-line
(the cost/effort label from `--option-cost`) directly under it — show that alongside the option
when present.

## 2. Present the choice

```
AskUserQuestion(
  questions=[{
    question: "<the record's Question, followed by its Background for context>",
    header: "Answer decision",
    options: [
      # one per Options entry, in the record's priority order — never resorted
      {label: "<option 1, truncated for the button>",
       description: "<option 1's full text, plus its Cost line when present>"},
      {label: "<option 2, truncated for the button>",
       description: "<option 2's full text, plus its Cost line when present>"},
      ...
    ]
  }]
)
```

The tool's own "Other" free-text field is the fallback for a direction not among the listed
options — do not add a synthetic "something else" option to the list.

## 3. Record the answer

- **A listed option was chosen** — answer with that option's full text (not the truncated
  button label, and not its `Cost:` line):

  ```bash
  worktrail-decision answer "$ID" --answer "<the chosen option's full option text>"
  ```

- **Free text was typed instead** — answer with exactly what the human typed, verbatim, no
  paraphrasing:

  ```bash
  worktrail-decision answer "$ID" --answer "<typed text>"
  ```

This is the same `answer` command a human would run by hand — it writes the `## Answer` section,
stamps `status: answered`, moves the record to `decisions/answered/`, and unblocks the linked
brief automatically (`work_queue.list` stops reporting it blocked the moment the record leaves
`open/`). Nothing about that mechanism changes here.

## 4. Confirm, then stop

Tell the human what was recorded (e.g. `Recorded: brief <brief-id> unblocks automatically`) and
return to the dashboard/picker. Do **not** run `worktrail-decision resolve` from this flow —
resolving is the consuming agent's job when it later resumes the blocked brief and reads the
answer at its original block site (`references/decision-queue.md#resume-from-decision`), not a
step of answering. Resolving here would archive the record before that agent ever reads it.
