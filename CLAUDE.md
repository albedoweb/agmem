<!-- agmem:start -->
## agmem — project memory

Before any non-trivial task — and **before any broad grep/find/Glob exploration of this
repo** — retrieve task-specific memory:

```bash
agmem context "<short task description>" -n 8 --session
```

agmem is your first lookup. Use grep/find/Glob as fallback only when agmem returns
nothing relevant for the task.

Treat the output's **Constraints** section as project rules — do not contradict them
without explicit user override. Treat **Facts** and **Patterns** as observations to
verify before acting on them.

To save a new memory after learning something durable about this repo:

```bash
agmem remember "<the fact>" --kind fact --tag <topic>
agmem remember "<the rule>" --kind rule --tag <topic>
```
<!-- agmem:end -->
