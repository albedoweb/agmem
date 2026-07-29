---
name: agmem
description: >
  Query and maintain agmem — the repo's persistent project memory. Use before
  any broad grep/find/Glob exploration of an agmem-initialized repo (a .agmem/
  directory exists), when the task mentions unfamiliar modules/resources, or
  after learning something durable about the repo that future sessions should
  know. Also use when the user asks to "remember" a project fact or rule.
---

# agmem — project memory

agmem answers task-scoped questions from a local index of this repo plus
manually saved rules and facts. Retrieval is BM25 — millisecond-fast, offline,
no API calls.

## Retrieve before you explore

Before any broad repo exploration (grep, find, Glob, walking directories),
query memory first:

```bash
agmem context "<task, phrased for BM25>" -n 8 --session
```

If the answer is already in memory you skip the search entirely. Fall back to
grep/find only when agmem returns nothing relevant. Keep `--session` on
follow-up queries in the same task: it demotes results you've already seen and
points at unexplored sections.

An auto-injected `<agmem-context>` block may already be present in the
conversation — check it before making your own call.

## Phrase the query for BM25

The index contains file paths, basenames, resource names, function/class
names, and code block IDs. Short identifier-like tokens rank better than
sentences:

- Use noun-phrase fragments, 3-7 tokens; drop articles and wh-words.
- Prefer identifiers: file basenames (`waf-alb-public`), resource names
  (`rds_proxy`), ticket IDs (`PROJ-1234`). Keep snake_case/kebab-case as
  they appear in code.
- **Query the noun, not the verb.** Tokens describing what you're *doing*
  (`review`, `investigate`, `fix`) or the task wrapper (`pull request`,
  `code`, `changes`) match half the repo. Query the *content*: for a PR
  review, list the changed files/resources and query those; for an alert or
  stack trace, query the function names and exception classes in it.

```text
✗ agmem context "review infrastructure pull request 2026"
✓ agmem context "waf-alb-public storefront ingress-gateway-external count"
✓ agmem context "PROJ-1234 rds_proxy"
```

If agmem prints `[agmem] warning: query is meta-word only`, rewrite around
identifiers instead of retyping the sentence.

## Treat the output correctly

- **Constraints** are project rules — do not contradict them without explicit
  user override.
- **Facts** and **Patterns** are observations — verify before acting on them.

## Save what you learn

After learning something durable about the repo (a convention, a "don't do X",
a non-obvious relationship), save it:

```bash
agmem remember "<the fact>" --kind fact --tag <topic> --source-ref <file>
agmem remember "<the rule>" --kind rule --tag <topic>
```

Rules get a 4× retrieval boost. Don't save what the code already states
plainly — save what took effort to discover.

## Not initialized yet?

If `agmem context` says "Not initialized" (or the binary is missing), offer
to run `/agmem:setup` — it installs the CLI and indexes the repo.
