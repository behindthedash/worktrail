# Tasks: spawnlib-cross-hop-argv-invariant

## 1. Invariant harness

- [x] 1.1 In `tests/orchestrator/test_spawnlib.py`, add a `CrossHopArgvInvariant` unittest class with the FallbackChain-style hermetic setUp/tearDown (tmp-dir `GO_AGENT_CAPACITY_CACHE`, save/restore of `subprocess.run` and any `default_model_for_agent` override), a module-level `CLAUDE_ONLY_ARGV_TOKENS` constant grounded in live.py's `_LEAN_WORKER_FLAGS` plus `--append-system-prompt`, `--permission-mode`/`bypassPermissions`, `--output-format`/`stream-json`/`--verbose`, `--setting-sources`/`project,local`, `--effort`, `--resume`, `--fork-session` (provenance noted in a comment), an argv-capturing scripted `subprocess.run` helper, and an `assert_no_claude_only_flags(cmds)` helper that fails naming the offending argv element for any codex/opencode argv containing a forbidden token as an exact element.

## 2. Cross-path scenario sweep

- [x] 2.1 In `tests/orchestrator/test_spawnlib.py` (same class), add the sweep tests passing the full `CLAUDE_ONLY_ARGV_TOKENS`-derived extra_args through every scenario: capacity-gate first hop with claude gated → codex selected and claude gated → opencode selected; session-limit hop claude→codex and claude→opencode; multi-hop chain claude→codex→opencode hitting the limit twice (sweeps the second hop); assert `assert_no_claude_only_flags` over ALL captured argvs per scenario; plus the positive control that an ungated claude primary still receives every extra_args token.

## 3. Verification

- [x] 3.1 [e2e] Run `PYTHONPATH=src pytest tests/orchestrator/test_spawnlib.py tests/orchestrator/test_agent_capacity.py -q` and then the full `PYTHONPATH=src pytest -q` plus `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`; confirm all green with no production-code diff (`git status` shows only test changes).
