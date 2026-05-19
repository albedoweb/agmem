---
description: Pull project memory relevant to a task from agmem
argument-hint: <task description>
allowed-tools: Bash(agmem:*)
---

Run `agmem context "$ARGUMENTS" -n 8` and treat the output as project context for the next response.

The output is grouped into Constraints (rules), Facts, and Patterns. Constraints are project rules — do not contradict them without explicit user override. Facts and Patterns are observations to verify before acting on them.

!`agmem context "$ARGUMENTS" -n 8`
