# Public repository benchmark protocol

Status: proposed pilot protocol. No runs or performance claims yet. Freeze the
repository revisions, task manifest, checks, model, and budget before execution.

## Question

Does agmem reduce the tokens, time, or repeated source reading needed to solve
repository tasks without reducing success? Retrieval scores alone do not answer
this question. Use agmem itself for integration smoke checks, not as headline
evidence about other projects.

## Pilot size and tasks

Start with two public Python repositories with runnable tests and distinct
structures; Python matches an existing parser. Broaden languages only after
the harness works. Record clone URLs, licenses, and exact commit SHAs.

Choose six tasks per repository before running agents:

1. Locate a symbol and explain a behavior, with exact source evidence.
2. Trace behavior across files, with an answer rubric.
3. Implement a small behavior change, with an external acceptance test.
4. Repair an injected bug, with regression and existing tests.
5. Locate current behavior after changing or removing an indexed source.
6. Continue a task in a fresh session after a prescribed earlier phase.

Use three paired repetitions per task: 2 repositories × 6 tasks × 3 repeats
× 2 conditions = 72 attempts. First run a four-attempt smoke test (one lookup
and one code task, both conditions) to verify instrumentation; exclude smoke
runs from the pilot results. These are exploratory results, not population-wide
claims. Publish failures and cases where the baseline wins.

Create tasks from repository behavior independently of retrieval output. Keep
answer keys, injected-bug patches, and grading tests outside agent-visible
checkouts and outside the agmem index. Distinguish synthetic mutations from
historical issues. Do not choose tasks because agmem already answers them well.

## Controls

- Pin the same model identifier, agent runtime, options, prompt, tools, and
  stopping budget for each pair. Record resolved versions and configuration.
- Use separate clones, not linked worktrees: agmem currently shares memory
  between linked worktrees. Start a fresh agent session per attempt.
- OFF gets ordinary source-inspection and editing tools. Remove agmem hooks,
  indexes, and instructions from its environment. Do not restrict it to a weak
  search strategy or hide public source documentation.
- ON gets the same tools plus a prebuilt agmem index and a short retrieval
  instruction. Use one-shot context calls to avoid shared session state.
- Do not inject task answers or hand-authored task-specific rules into ON.
- Randomize ON/OFF order with a recorded seed. Avoid concurrent timed attempts
  on one machine. Record cache and model-service effects that remain uncontrolled.
- Measure source mutation after initial indexing explicitly. Record the refresh
  policy; do not silently repair a stale index mid-run. Separate full-refresh
  and watcher scenarios if both are tested.
- Grade code with tests outside the agent's control and run regression checks.
  Grade explanations with a fixed evidence rubric; blind the grader to condition.

## Measurements and artifacts

Each attempt records task ID, repetition, condition, repository SHA, model,
runtime, timestamps, exit status, timeout, and grading result. Preserve the raw
agent transcript, tool events, code diff, test output, and final text report.

Report actual provider usage when available: input, output, cached input,
reasoning tokens, and cost, with their provider-specific definitions. Do not
double-count cached tokens or replace missing usage with estimates. Capture
terminal replies as well as tool-call turns; validate event completeness during
the smoke test.

Measure wall time to completion or timeout. Report installation, initial index,
refresh, and optional embedding warmup separately, plus the total cost of using
the tool. Show both cold and warm scenarios rather than hiding indexing cost.

Count repeated reads by normalized file and overlapping line ranges within an
attempt. Report counts and bytes where observable. Include repeated context
entries from agmem; document what the event format cannot measure.

Show per-task ON/OFF results and paired differences, then aggregates and spread.
Include failed and timed-out attempts; success rate is a primary outcome, not a
filter for the efficiency table. Do not label absent metrics as zero.

Before the main pilot, agree on a practical effect threshold and success
criterion. A possible starting criterion is at least a 10% reduction in median
paired token use with no observed success-rate loss; a small pilot cannot
establish non-inferiority. Freeze the criterion before seeing pilot outcomes.

## Freshness gate

Run the incremental-update and watcher recovery regressions before a pilot.
The fixed paths cover Markdown replacement/deletion, directory summaries,
offline changes at watcher startup, and retry after a failed poll. Record the
actual refresh policy and polling interval: those tests do not imply instantly
fresh retrieval or isolated memory across worktrees. Do not generalize static
lookup wins to always-current memory.
