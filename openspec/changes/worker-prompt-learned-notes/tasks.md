## 1. Learned-notes loader (`worker-prompt-learned-notes`)

- [ ] 1.1 In `src/worktrail/learning/notes.py`, add `load_learned_notes(repo) -> str | None` per design D3. It reads `worktrail.learning.paths.retro_memory_path(repo)` (from `run-outcome-retro-agent`) and returns the `## Notes for workers` bullets up to the next `## ` heading.
      Cap the result at 20 bullets and 4,000 characters, truncating only between bullets. Return `None` for an absent file, a missing section, no bullets, `OSError`, or `UnicodeDecodeError`.
      Write the failing tests first in `tests/learning/test_notes.py`, using a temporary `WORKTRAIL_HOME`. Cover: absent file, missing section, 25 bullets capped to 20, the 4,000-character whole-bullet cap, a section ending at the next heading, and undecodable bytes.
      files: src/worktrail/learning/notes.py, tests/learning/test_notes.py
      Covers: Learned Notes Are Loaded From The Retro Memory Contract

## 2. Prompt rendering (`worker-prompt-learned-notes`)

- [ ] 2.1 In `src/worktrail/orchestrator/dispatch.py`, add `learned_notes: str | None = None` to `WorkerPromptCtx`. When it is non-empty, render the learned-notes heading from the spec plus the notes text immediately before `"Hard rules:"` in `build_worker_prompt`. Render nothing otherwise.
      Write the failing tests first in `tests/orchestrator/test_dispatch.py`. Cover:
      - every role in `ROLES` renders the heading between the `Task:` line and `Hard rules:`;
      - a `codex` `default_agent` still renders it;
      - `learned_notes=None`, `learned_notes=""`, and a context dict without the key all produce byte-identical prompts.
      files: src/worktrail/orchestrator/dispatch.py, tests/orchestrator/test_dispatch.py
      Covers: Worker Prompts Render Learned Notes For Every Role And Harness; Absent Notes Leave Worker Prompts Byte-Identical

## 3. Once-per-run snapshot and journal record (`worker-prompt-learned-notes`)

- [ ] 3.1 In `src/worktrail/orchestrator/live.py`, add a lazy `LiveSpawn.learned_notes` property that mirrors `pre_commit_cmd`.
      On first access it returns `load_learned_notes(self.repo)` only when `_load_policy(self.repo).get("agent_learning")` is true; otherwise it returns `None` without reading the file. Pass the value as `"learned_notes"` in the ctx `LiveSpawn.__call__` builds.
      On the first prompt built with non-empty notes, append one `{"event": "learned_notes", "sha256": <hex>, "bullets": <n>}` marker through `progress.append_safety_net_events`.
      Write the failing tests first in `tests/orchestrator/test_live_extras.py`. Cover:
      - a disabled policy with a memory file present yields no heading and no file read;
      - notes stay snapshotted after the file is rewritten mid-run;
      - three spawns with notes append exactly one `learned_notes` marker;
      - `None` notes append none.
      files: src/worktrail/orchestrator/live.py, tests/orchestrator/test_live_extras.py
      Covers: Notes Are Injected Only For Opted-In Repos And Snapshotted Once Per Run; Injected Notes Are Recorded In The Run Journal
      depends: 1.1, 2.1

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src pytest -q`, `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` with no learning directory under `WORKTRAIL_HOME`, and `openspec validate worker-prompt-learned-notes --strict`.
      Then, with a temporary `WORKTRAIL_HOME` holding a seeded `MEMORY.md` for a scratch repo whose `.worktrail/policy.yaml` sets `agent_learning: true`, build implement and review prompts through `LiveSpawn`. Confirm each prompt carries the notes block before `Hard rules:`, and that the journal holds one `learned_notes` marker whose `sha256` matches the notes text.
      depends: 3.1
