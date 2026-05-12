<!-- agmem:start -->
## agmem — project memory

At the start of a new session, run `agmem hot` for an instant snapshot of project rules
and recent facts (no BM25, ≤500 tokens).

Before any non-trivial task, retrieve task-specific memory:

```bash
agmem context "<short task description>" -n 8
```

Treat the output's **Constraints** section as project rules — do not contradict them
without explicit user override. Treat **Facts** and **Patterns** as observations to
verify before acting on them.

To save a new memory after learning something durable about this repo:

```bash
agmem remember "<the fact>" --kind fact --tag <topic>
agmem remember "<the rule>" --kind rule --tag <topic>
```
<!-- agmem:end -->
