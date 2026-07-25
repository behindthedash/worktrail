# Sample live-run target — URL shortener

A minimal TypeScript project that the parallel-orchestrator's sample spec
(`docs/specs/001-url-shortener/`) implements against during a **live** recorded
run.

This is a committed **template**, not a live repo. The live run:

1. instantiates this template into a fresh standalone git repo (so worktrees,
   branches, and PRs are isolated from the kit),
2. spawns a worker agent per frontier task into a per-task worktree,
3. records each worker's report-back into `../cassette.json`,
4. and the deterministic **replay** of that cassette is the committed golden
   (`../orchestrate.golden.txt`) — so a non-deterministic live run becomes a
   repeatable regression fixture.

## Layout

- `docs/specs/001-url-shortener/` — the spec + task files (the orchestrator input)
- `src/`, `test/` — where worker agents write the implementation
- `package.json` / `tsconfig.json` — toolchain (worker runs `npm install` in its
  worktree as the setup step, then `npm test`)

## Commands (inside a worktree)

```bash
npm install
npm test     # vitest
```
