# Go Skill — CLI Traps & Gotchas

## `gh api -f` does NOT expand `@file`

`-f`/`--raw-field` sets the value as a literal string. Unlike curl, `-f body=@file.md`
sets the field to the literal text `@file.md`, not the file's contents.

Use **`-F`/`--field`** when you need `@file` expansion:

```bash
gh api repos/{owner}/{repo}/pulls/{n} -F 'body=@body.md'
```

The capital-`F` variant supports `@`-prefixed file reads, typed conversion (true/false/null,
integers), and `{owner}`/`{repo}`/`{branch}` placeholder expansion (`gh help api` for
full docs).

### Why not `gh pr edit --body-file`?

`gh pr edit --body-file <path>` uses GraphQL and aborts on any pre-existing deprecated
field in the response — specifically Projects (classic) deprecation notices. If that error
fires, the workaround is:

```bash
python3 -c "import json; print(json.dumps({'body': open('file.md').read()}))" > payload.json
gh api -X PATCH repos/{owner}/{repo}/pulls/{n} --input payload.json
```
