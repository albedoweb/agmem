<!-- agmem:start -->
## agmem — project memory

Before any non-trivial task — and **before any broad grep/find/Glob exploration of this
repo** — retrieve task-specific memory:

```bash
agmem context "<task, phrased for BM25>" -n 8 --session
```

agmem is your first lookup. Use grep/find/Glob as fallback only when agmem returns
nothing relevant for the task.

### Phrase the query for BM25

agmem uses BM25 — short identifier-like tokens rank better than full sentences. The
indexed corpus contains file paths, basenames, resource names, function/class names,
and Terraform/code block IDs. The closer your query is to that vocabulary, the better
the result. Before calling, rewrite the query:

- Drop articles, prepositions, wh-words (`the`, `for`, `in`, `how`, `where`).
- Use noun-phrase fragments, 3-7 tokens.
- Prefer **identifiers**: file basenames (`waf-alb-external`), resource names
  (`rds_proxy`, `my-truv`), module dirs, ticket IDs (`INF-3728`). Keep snake_case
  / kebab-case as they appear in code.
- If you can guess a likely filename or module, include the basename token.

Examples:

  ✗ `agmem context "where are the Grafana Slack contact points and templates"`
  ✓ `agmem context "grafana-contact-points slack notification templates"`

  ✗ `agmem context "Enable WAF in monitoring mode for mytruv public ALB"`
  ✓ `agmem context "waf-alb-external mytruv istio-gateway-external count"`

  ✗ `agmem context "how are secrets loaded and the config refresh endpoint"`
  ✓ `agmem context "secrets config refresh endpoint"`

If the first call returns weak results, **refine the tokens — don't retype the
natural-language form.** Try different basenames, snake_case variants, or the
identifiers you noticed in the top-K text.

### Treating the output

Treat the output's **Constraints** section as project rules — do not contradict them
without explicit user override. Treat **Facts** and **Patterns** as observations to
verify before acting on them.

### Saving memories

To save a new memory after learning something durable about this repo:

```bash
agmem remember "<the fact>" --kind fact --tag <topic>
agmem remember "<the rule>" --kind rule --tag <topic>
```
<!-- agmem:end -->
