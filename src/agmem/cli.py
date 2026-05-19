"""CLI entry point for agmem using Typer."""

from __future__ import annotations

from typing import Optional

import typer

from datetime import datetime, timezone

from . import __version__
from .config import (
    emit_claude_md,
    init_config,
    install_claude_hook,
    install_git_hook,
    read_config,
)
from .hook import run_inject_hook
from .hot import DEFAULT_BUDGET_CHARS, hot_path, read_hot, run_refresh as run_hot_refresh
from .indexer import run_index, run_update
from .render import render_context, render_recall
from .search import _read_mmr_config, search_filtered
from .store import (
    VALID_KINDS,
    append_entry,
    create_entry,
    find_entries_by_id_prefix,
    read_all_entries,
    rewrite_entries,
)
from .review import run_review
from .stats import collect_stats
from .agmem_eval import (
    EvalReport,
    extract_eval_pairs,
    format_report,
    load_pairs,
    run_eval,
    run_sweep,
    save_pairs,
    write_csv,
    write_json,
)
from .testq import Snapshot, diff_against_snapshot, record_snapshot, run_testq
from .verify import run_verify

app = typer.Typer(
    name="agmem",
    help="CLI memory layer for coding agents.",
    no_args_is_help=True,
)

hook_app = typer.Typer(
    name="hook",
    help="Claude Code hook handlers (read payload from stdin).",
    no_args_is_help=True,
)
app.add_typer(hook_app)


@hook_app.command("inject")
def hook_inject():
    """UserPromptSubmit handler: emit <agmem-context> additionalContext block."""
    raise typer.Exit(code=run_inject_hook())


@app.command()
def init(
    project: Optional[str] = typer.Option(
        None, "--project", "-p", help="Project name"
    ),
    emit_claude_md_flag: bool = typer.Option(
        False, "--emit-claude-md",
        help="Write/update agmem instruction block in CLAUDE.md (idempotent)",
    ),
    install_hook: bool = typer.Option(
        False, "--install-hook",
        help="Install UserPromptSubmit hook in .claude/settings.json",
    ),
    install_git_hook_flag: bool = typer.Option(
        False, "--install-git-hook",
        help="Install post-commit/post-merge/post-rewrite git hooks "
             "that auto-reindex on commit/pull/rebase",
    ),
):
    """Initialize .agmem/ in repo and optionally wire Claude Code integration."""
    already = False
    try:
        cfg = read_config()
        already = bool(cfg)
    except Exception:
        cfg = {}

    if not already:
        cfg = init_config(project_name=project)
        typer.echo(f"Initialized agmem in .agmem/{' for project ' + project if project else ''}")
    else:
        typer.echo(f"agmem already initialized (project: {cfg.get('project', 'unknown')})")

    if emit_claude_md_flag:
        path, action = emit_claude_md()
        typer.echo(f"CLAUDE.md {action}: {path}")

    if install_hook:
        path, action = install_claude_hook()
        typer.echo(f"Claude Code hook {action}: {path}")

    if install_git_hook_flag:
        results = install_git_hook()
        if "_repo" in results:
            typer.echo("No .git directory found; cannot install git hooks.", err=True)
        else:
            for hook_name, (hook_path, hook_action) in results.items():
                typer.echo(f"Git {hook_name} hook {hook_action}: {hook_path}")

    if already and not (emit_claude_md_flag or install_hook or install_git_hook_flag):
        raise typer.Exit(code=1)


@app.command()
def remember(
    text: str = typer.Argument(..., help="The memory text to store"),
    tag: Optional[str] = typer.Option(
        None, "--tag", "-t", help="Comma-separated tags"
    ),
    source: str = typer.Option("manual", "--source", "-s", help="Source type"),
    source_ref: Optional[str] = typer.Option(
        None, "--source-ref", "-r", help="Source reference (file:line, commit:sha)"
    ),
    kind: str = typer.Option(
        "fact", "--kind", "-k",
        help=f"Memory kind: one of {', '.join(VALID_KINDS)} (default: fact)",
    ),
):
    """Store a new memory entry."""
    try:
        read_config()
    except Exception:
        typer.echo("Not initialized. Run `agmem init` first.", err=True)
        raise typer.Exit(code=1)

    if kind not in VALID_KINDS:
        typer.echo(f"Invalid --kind {kind!r}. Allowed: {', '.join(VALID_KINDS)}", err=True)
        raise typer.Exit(code=1)

    tags = [t.strip().lower() for t in tag.split(",")] if tag else []
    entry = create_entry(text=text, tags=tags, source=source, source_ref=source_ref, kind=kind)
    append_entry(entry)
    typer.echo(entry.id)


@app.command(name="list")
def list_entries(
    tag: Optional[str] = typer.Option(
        None, "--tag", "-t", help="Filter by tag"
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="Max entries to show"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON array"),
):
    """List all (or filtered) memory entries."""
    try:
        read_config()
    except Exception:
        typer.echo("Not initialized. Run `agmem init` first.", err=True)
        raise typer.Exit(code=1)

    entries = read_all_entries()
    if tag:
        entries = [e for e in entries if tag.lower() in [t.lower() for t in e.tags]]
    entries = entries[-limit:]

    if json_mode:
        import json
        typer.echo(json.dumps([e.to_dict() for e in entries], ensure_ascii=False, indent=2))
        return

    if not entries:
        typer.echo("No entries found.")
        return

    for e in entries:
        tags_str = f" [{', '.join(e.tags)}]" if e.tags else ""
        typer.echo(f"{e.id}  {e.ts[:10]}{tags_str}\n  {e.text}\n")


@app.command()
def recall(
    query: str = typer.Argument(..., help="Search query"),
    n: int = typer.Option(10, "--limit", "-n", help="Max results"),
    json_mode: bool = typer.Option(
        False, "--json", help="Output as JSON array"
    ),
    no_mmr: bool = typer.Option(
        False, "--no-mmr",
        help="Disable MMR diversity reranking for this query.",
    ),
    mmr_lambda: Optional[float] = typer.Option(
        None, "--mmr-lambda",
        help="MMR relevance-vs-diversity trade-off (0.0–1.0, default 0.7). "
             "Higher = more relevance, less diversity.",
    ),
):
    """Search memories by query and output markdown."""
    try:
        read_config()
    except Exception:
        typer.echo("Not initialized. Run `agmem init` first.", err=True)
        raise typer.Exit(code=1)

    mmr_on, mmr_lam = _read_mmr_config()
    if no_mmr:
        mmr_on = False
    if mmr_lambda is not None:
        mmr_lam = mmr_lambda

    results = search_filtered(query, limit=n, mmr_enabled=mmr_on, mmr_lambda=mmr_lam)
    if not json_mode:
        from .config import find_repo_root
        typer.echo(f"# Reading memories from {find_repo_root() / '.agmem'}\n")
    output = render_recall(query, results, json_mode=json_mode)
    typer.echo(output)


@app.command()
def context(
    task: Optional[str] = typer.Argument(None, help="Task description for context generation"),
    n: int = typer.Option(10, "--limit", "-n", help="Max results"),
    tag: Optional[str] = typer.Option(
        None, "--tag", "-t",
        help="Restrict retrieval to entries carrying this tag. Useful when the "
             "agent knows the relevant area (e.g. --tag mytruv) and wants to "
             "exclude tangentially-matching content from other subsystems.",
    ),
    session: bool = typer.Option(
        False, "--session", "-s",
        help="Session-aware retrieval: demote already-seen entries, boost sibling "
             "sections of seen files, append a 'Haven't seen yet' hint. State lives "
             "in .agmem/_ask_session.json and auto-expires after 30 min idle.",
    ),
    new: bool = typer.Option(
        False, "--new",
        help="With --session: start a fresh session, ignoring any existing one.",
    ),
    reset_session_flag: bool = typer.Option(
        False, "--reset-session",
        help="Clear the running session and exit (no query needed).",
    ),
    history: bool = typer.Option(
        False, "--history",
        help="Show the running session's queries and exit.",
    ),
    json_mode: bool = typer.Option(
        False, "--json", help="Output as JSON array"
    ),
    no_mmr: bool = typer.Option(
        False, "--no-mmr",
        help="Disable MMR diversity reranking for this query.",
    ),
    mmr_lambda: Optional[float] = typer.Option(
        None, "--mmr-lambda",
        help="MMR relevance-vs-diversity trade-off (0.0–1.0, default 0.7). "
             "Higher = more relevance, less diversity.",
    ),
):
    """Generate agent-oriented context for a task.

    By default returns a one-shot ranked retrieval grouped as
    Constraints / Facts / Patterns. Pass ``--session`` to enable session-aware
    behavior across multiple calls in the same workflow: follow-up queries
    skip chunks you've already seen, boost siblings of seen files, and end
    with a "Haven't seen yet" hint pointing at unexplored sections / tags.

    The UserPromptSubmit hook installed by ``agmem init --install-hook`` uses
    ``--session`` automatically; CI / one-shot uses can leave it off.
    """
    try:
        read_config()
    except Exception:
        typer.echo("Not initialized. Run `agmem init` first.", err=True)
        raise typer.Exit(code=1)

    if reset_session_flag:
        from .ask import reset_session as _reset
        wiped = _reset()
        typer.echo("session cleared." if wiped else "no active session.")
        return

    if history:
        from .ask import load_session, render_history
        typer.echo(render_history(load_session()))
        return

    if not task:
        typer.echo(
            "task required. Try: agmem context \"how does crawler work\" --session",
            err=True,
        )
        raise typer.Exit(code=1)

    mmr_on, mmr_lam = _read_mmr_config()
    if no_mmr:
        mmr_on = False
    if mmr_lambda is not None:
        mmr_lam = mmr_lambda

    if not json_mode:
        from .config import find_repo_root
        typer.echo(f"<!-- agmem source: {find_repo_root() / '.agmem'} -->")

    if session:
        from .ask import render_haven_seen_tail, run_ask
        result = run_ask(task, top_n=n, new_session=new, tag=tag,
                         mmr_enabled=mmr_on, mmr_lambda=mmr_lam)
        output = render_context(task, result.top, json_mode=json_mode)
        if not json_mode:
            tail = render_haven_seen_tail(result)
            if tail:
                output = output.rstrip() + "\n\n" + tail
        typer.echo(output)
        return

    results = search_filtered(task, limit=n, tag=tag,
                              mmr_enabled=mmr_on, mmr_lambda=mmr_lam)
    output = render_context(task, results, json_mode=json_mode)
    typer.echo(output)


@app.command()
def forget(
    entry_id: str = typer.Argument(..., help="Entry ID (or unique prefix) to soft-delete"),
    revive: bool = typer.Option(False, "--revive", help="Revive a previously forgotten entry"),
):
    """Soft-delete a memory entry (sets deleted_at; search/recall hide it)."""
    try:
        read_config()
    except Exception:
        typer.echo("Not initialized. Run `agmem init` first.", err=True)
        raise typer.Exit(code=1)

    matches = find_entries_by_id_prefix(entry_id)
    if not matches:
        typer.echo(f"No entry matches id prefix {entry_id!r}.", err=True)
        raise typer.Exit(code=1)
    if len(matches) > 1:
        typer.echo(
            f"Ambiguous id prefix {entry_id!r} matched {len(matches)} entries; use a longer prefix.",
            err=True,
        )
        raise typer.Exit(code=1)

    target = matches[0]
    if revive:
        if target.deleted_at is None:
            typer.echo(f"{target.id} is not deleted; nothing to revive.")
            return
        target.deleted_at = None
        action = "revived"
    else:
        if target.deleted_at is not None:
            typer.echo(f"{target.id} already deleted at {target.deleted_at}.")
            return
        target.deleted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        action = "forgotten"

    all_entries = read_all_entries(include_deleted=True)
    updated = [target if e.id == target.id else e for e in all_entries]
    rewrite_entries(updated)
    typer.echo(f"{target.id} {action}")


@app.command()
def hot(
    refresh: bool = typer.Option(
        False, "--refresh",
        help="Rebuild .agmem/_hot.md from current memories",
    ),
    budget: int = typer.Option(
        DEFAULT_BUDGET_CHARS, "--budget",
        help="Max chars in cache (~chars/4 = tokens)",
    ),
    json_mode: bool = typer.Option(
        False, "--json",
        help="Emit cache state + content as JSON (path, mtime, chars, content).",
    ),
):
    """Pre-computed memory snapshot for instant session-start context.

    Without flags: print .agmem/_hot.md (instant, no BM25). With --refresh: rebuild it
    from current memories. Designed to be regenerated on every commit by the post-commit
    git hook.
    """
    try:
        read_config()
    except Exception:
        typer.echo("Not initialized. Run `agmem init` first.", err=True)
        raise typer.Exit(code=1)

    if refresh:
        result = run_hot_refresh(budget_chars=budget)
        stats = result["stats"]
        if json_mode:
            import json
            typer.echo(json.dumps({
                "action": "refreshed",
                "path": str(result["path"]),
                "stats": stats,
            }, ensure_ascii=False, indent=2))
            return
        typer.echo(
            f"refreshed {result['path']}  "
            f"rules={stats['rules']} facts={stats['facts']} patterns={stats['patterns']} "
            f"chars={stats['chars']}"
        )
        return

    text = read_hot()
    if json_mode:
        import json
        from datetime import datetime, timezone
        path = hot_path()
        payload: dict = {"path": str(path), "exists": text is not None}
        if text is not None:
            payload["content"] = text
            payload["chars"] = len(text)
            try:
                payload["mtime"] = datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                ).isoformat(timespec="seconds")
            except OSError:
                payload["mtime"] = None
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if text is None:
        typer.echo(
            "No hot cache yet. Run `agmem hot --refresh` (or `agmem init --install-git-hook` "
            "to auto-refresh on commit).",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(text)


@app.command()
def review(
    stale_days: int = typer.Option(30, "--stale-days", help="Manual entries unverified longer than N days are stale"),
    json_mode: bool = typer.Option(False, "--json", help="Output report as JSON (counts + entry id lists)"),
):
    """Show drifted, missing-source, stale, and duplicate entries (read-only)."""
    try:
        read_config()
    except Exception:
        typer.echo("Not initialized. Run `agmem init` first.", err=True)
        raise typer.Exit(code=1)

    report = run_review(stale_days=stale_days)

    if json_mode:
        import json
        typer.echo(json.dumps({
            "total_live": report.total_live,
            "stale_days": stale_days,
            "drifted": [e.id for e in report.drifted],
            "missing_source": [e.id for e in report.missing_source],
            "stale": [e.id for e in report.stale],
            "duplicates": [
                {"a": a.id, "b": b.id, "score": round(score, 3)}
                for a, b, score in report.duplicates
            ],
        }, ensure_ascii=False, indent=2))
        return

    typer.echo(
        f"live entries: {report.total_live}  "
        f"drifted: {len(report.drifted)}  "
        f"missing source: {len(report.missing_source)}  "
        f"stale (>{stale_days}d, manual, unverified): {len(report.stale)}  "
        f"duplicate pairs: {len(report.duplicates)}"
    )

    if report.drifted:
        typer.echo("\n# Drifted")
        for e in report.drifted[:25]:
            typer.echo(f"  {e.id[:10]}  {e.source_ref}  (drifted: {e.drifted_at[:10] if e.drifted_at else '?'})")
    if report.missing_source:
        typer.echo("\n# Missing source files")
        for e in report.missing_source[:25]:
            typer.echo(f"  {e.id[:10]}  {e.source_ref}")
    if report.stale:
        typer.echo("\n# Stale (run `agmem verify <id>` after re-checking)")
        for e in report.stale[:25]:
            typer.echo(f"  {e.id[:10]}  {e.text[:80]}")
    if report.duplicates:
        typer.echo("\n# Duplicate pairs (Jaccard score)")
        for a, b, score in report.duplicates[:25]:
            typer.echo(f"  {score:.2f}  {a.id[:10]}  ↔  {b.id[:10]}")


@app.command()
def testq(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Also print PASSing questions"),
    record: Optional[str] = typer.Option(
        None, "--record",
        help="Record a snapshot of top-N results per fixture question. Pass '' for auto-name (timestamp).",
    ),
    diff: Optional[str] = typer.Option(
        None, "--diff",
        help="Diff current top-N against a saved snapshot (by name; default = latest)",
    ),
):
    """Run retrieval regression suite from .agmem/testq.yaml.

    With no flags: assert must_match constraints from fixture.
    With --record [<name>]: capture current top-N rankings to .agmem/testq-snapshots/.
    With --diff [<name>]: compare current rankings against a saved snapshot, surface drift.
    """
    try:
        read_config()
    except Exception:
        typer.echo("Not initialized. Run `agmem init` first.", err=True)
        raise typer.Exit(code=1)

    if record is not None:
        snap_path, snap_or_err = record_snapshot(record or None)
        if snap_path is None or not isinstance(snap_or_err, Snapshot):
            typer.echo(str(snap_or_err), err=True)
            raise typer.Exit(code=1)
        snap = snap_or_err
        n_questions = len(snap.questions)
        n_results = sum(len(q.results) for q in snap.questions)
        commit_str = f" @ {snap.commit}" if snap.commit else ""
        typer.echo(f"recorded {snap_path}{commit_str}: {n_questions} questions, {n_results} results")
        return

    if diff is not None:
        diff_result = diff_against_snapshot(diff or None)
        if diff_result.error:
            typer.echo(diff_result.error, err=True)
            raise typer.Exit(code=1)
        commit_str = f" @ {diff_result.snapshot_commit}" if diff_result.snapshot_commit else ""
        typer.echo(
            f"snapshot: {diff_result.snapshot_path.name}  "
            f"recorded {diff_result.snapshot_recorded_at[:19]}{commit_str}"
        )
        for d in diff_result.drifts:
            if not d.has_changes:
                continue
            typer.echo(f"\n⚠ {d.question}")
            for entry in d.dropped:
                typer.echo(f"  - dropped: rank {entry.rank}  {entry.id[:8]}  {entry.source_ref or '-'}")
            for entry in d.added:
                typer.echo(f"  + added:   rank {entry.rank}  {entry.id[:8]}  {entry.source_ref or '-'}")
            for entry_id, old_rank, new_rank in d.reordered:
                arrow = "↑" if new_rank < old_rank else "↓"
                typer.echo(f"  {arrow} reorder: {entry_id[:8]}  rank {old_rank} → {new_rank}")
        for q in diff_result.missing_in_snapshot:
            typer.echo(f"  ? new question (not in snapshot): {q}")
        for q in diff_result.missing_in_fixture:
            typer.echo(f"  ? snapshot question dropped from fixture: {q}")

        unchanged = sum(1 for d in diff_result.drifts if not d.has_changes)
        typer.echo(
            f"\n{unchanged}/{len(diff_result.drifts)} questions unchanged, "
            f"{diff_result.changed_count} drifted"
        )
        if diff_result.changed_count > 0:
            raise typer.Exit(code=1)
        return

    testq_result = run_testq()
    if testq_result.error:
        typer.echo(testq_result.error, err=True)
        raise typer.Exit(code=1)

    for failure in testq_result.failed:
        typer.echo(f"FAIL  {failure.question}")
        for c in failure.missing:
            typer.echo(f"      missing: {c}")
        if failure.top_results:
            typer.echo(f"      top hits: {', '.join(failure.top_results)}")

    if verbose:
        for question, top_n in testq_result.passed:
            typer.echo(f"PASS  {question}  (top_n={top_n})")

    if testq_result.total == 0:
        typer.echo("No questions in fixture.")
        return

    typer.echo(f"\n{len(testq_result.passed)}/{testq_result.total} passed")
    if testq_result.failed:
        raise typer.Exit(code=1)


@app.command(name="eval-agmem")
def eval_agmem(
    since: Optional[str] = typer.Option(
        "30d", "--since",
        help="Time window for session discovery (e.g. 30d, 7d, 90d). Use '' for all.",
    ),
    cwd_filter: Optional[str] = typer.Option(
        None, "--cwd",
        help="Only score sessions whose repo root matches this path.",
    ),
    out: Optional[str] = typer.Option(
        None, "--out", "-o",
        help="Base path for CSV and JSON output files (e.g. --out eval-results/report).",
    ),
    window: int = typer.Option(
        20, "--window", "-w",
        help="Tool-call window size after each agmem context call.",
    ),
    k_headline: int = typer.Option(
        5, "--k", "-k",
        help="Headline K for coverage and recall reporting (also reports at 3, 10, 20).",
    ),
    json_only: bool = typer.Option(
        False, "--json", help="Output full report as JSON to stdout instead of markdown summary.",
    ),
    collect: Optional[str] = typer.Option(
        None, "--collect",
        help="Extract pairs from agent-diff sessions and freeze to a JSON file, then exit "
             "(no scoring). Pass the filename, e.g. --collect .agmem/eval-pairs.json.",
    ),
    pairs_file: Optional[str] = typer.Option(
        None, "--pairs-file",
        help="Score against a frozen pairs file instead of extracting from agent-diff. "
             "Use after --collect to track drift. E.g. --pairs-file .agmem/eval-pairs.json.",
    ),
):
    """Evaluate agmem retrieval quality against real agent-diff session logs.

    Two-phase workflow for drift tracking:

    1. Freeze the dataset once:
       agmem eval-agmem --since '' --collect .agmem/eval-pairs.json

    2. Re-score against the frozen dataset after index/search changes:
       agmem eval-agmem --pairs-file .agmem/eval-pairs.json

    Without --collect or --pairs-file, extracts and scores in one shot (live).
    """
    try:
        read_config()
    except Exception:
        typer.echo("Not initialized. Run `agmem init` first.", err=True)
        raise typer.Exit(code=1)

    since_val = since if since else None
    ks = [3, 5, 10, 20]

    if collect:
        from pathlib import Path
        pairs = extract_eval_pairs(since=since_val, cwd_filter=cwd_filter, window_turns=window)
        collect_path = Path(collect)
        save_pairs(pairs, collect_path)
        typer.echo(f"Frozen {len(pairs)} pairs to {collect_path}")
        return

    if pairs_file:
        from pathlib import Path
        pairs = load_pairs(Path(pairs_file))
        report = run_eval(pairs=pairs, ks=ks)
    else:
        report = run_eval(
            since=since_val,
            cwd_filter=cwd_filter,
            window_turns=window,
            ks=ks,
        )

    if json_only:
        import json as _json
        typer.echo(_json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return

    typer.echo(format_report(report, k_headline=k_headline))

    if out:
        from pathlib import Path
        csv_path = Path(f"{out}.csv")
        json_path = Path(f"{out}.json")
        write_csv(report, csv_path)
        write_json(report, json_path)
        typer.echo(f"\nWrote {csv_path} ({report.n_pairs} rows)")
        typer.echo(f"Wrote {json_path}")


@app.command(name="eval-agmem-sweep")
def eval_agmem_sweep(
    param: Optional[list[str]] = typer.Option(
        None, "--param",
        help="Parameter to sweep: 'name=val1,val2,...'. Repeat for multiple params. "
             "Supported: kind_boost.rule, kind_boost.pattern, source_boost.manual, "
             "source_ref_weight, basename_weight, title_weight, b.",
    ),
    metric: str = typer.Option(
        "hit_at_5", "--metric",
        help="Metric to optimize: hit_at_3, hit_at_5, hit_at_10, hit_at_20, "
             "recall_at_5, mrr.",
    ),
    since: Optional[str] = typer.Option(
        "30d", "--since",
        help="Time window for session discovery (e.g. 30d, 7d, 90d).",
    ),
    out: Optional[str] = typer.Option(
        None, "--out", "-o",
        help="Base path for sweep CSV output.",
    ),
    pairs_file: Optional[str] = typer.Option(
        None, "--pairs-file",
        help="Score against a frozen pairs file instead of extracting from agent-diff.",
    ),
):
    """Grid-search agmem parameter values across the eval set.

    Monkey-patches search module constants, re-scores all pairs, and reports
    the best parameter combo per the chosen metric.
    """
    try:
        read_config()
    except Exception:
        typer.echo("Not initialized. Run `agmem init` first.", err=True)
        raise typer.Exit(code=1)

    if not param:
        typer.echo("Pass at least one --param (e.g. --param 'kind_boost.rule=2,3,4').", err=True)
        raise typer.Exit(code=1)

    since_val = since if since else None

    preloaded_pairs = None
    if pairs_file:
        from pathlib import Path
        preloaded_pairs = load_pairs(Path(pairs_file))

    result = run_sweep(param_specs=param, metric=metric, since=since_val, pairs=preloaded_pairs)

    if result.get("error"):
        typer.echo(result["error"], err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Sweep: {result['n_pairs']} pairs × {result['n_combos']} combos")
    typer.echo(f"Metric: {metric}")
    typer.echo()
    typer.echo(f"Best: {result['best_score']:.4f}")
    typer.echo(f"Params: {result['best_params']}")
    typer.echo()

    header = ["score"] + list(result["params_order"]) + ["kind_boost_rule", "kind_boost_pattern", "source_boost_manual", "source_ref_weight"]
    rows = [[f"{r['score']:.4f}"] + [str(r["params"].get(p, "")) for p in result["params_order"]] + [
        str(r["params"].get("kind_boost.rule", "")),
        str(r["params"].get("kind_boost.pattern", "")),
        str(r["params"].get("source_boost.manual", "")),
        str(r["params"].get("source_ref_weight", "")),
    ] for r in result["results"]]

    max_widths = [max(len(h), max((len(row[i]) for row in rows), default=0)) for i, h in enumerate(header)]
    fmt = "  ".join(f"{{:<{w}}}" for w in max_widths)
    typer.echo(fmt.format(*header))
    for row in rows:
        typer.echo(fmt.format(*row))

    if out:
        import csv as _csv
        from pathlib import Path
        csv_path = Path(f"{out}.csv")
        with open(csv_path, "w", newline="") as f:
            writer = _csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            for row_dict, row_vals in zip(result["results"], rows):
                writer.writerow(dict(zip(header, row_vals)))
        typer.echo(f"\nWrote {csv_path}")


@app.command()
def verify(
    id_prefix: Optional[str] = typer.Argument(
        None, help="Verify only entries whose id starts with this prefix"
    ),
    all_: bool = typer.Option(False, "--all", help="Verify every entry with a source_hash"),
    follow: bool = typer.Option(
        False, "--follow",
        help="Follow git renames: auto-update source_ref when content hash matches a renamed file",
    ),
):
    """Re-hash referenced files; mark entries as verified or drifted."""
    try:
        read_config()
    except Exception:
        typer.echo("Not initialized. Run `agmem init` first.", err=True)
        raise typer.Exit(code=1)

    if not id_prefix and not all_:
        typer.echo("Pass an id prefix or --all.", err=True)
        raise typer.Exit(code=1)

    result = run_verify(id_prefix=id_prefix, follow_renames=follow)
    counts = result.counts
    line = (
        f"verified={counts['verified']}  drifted={counts['drifted']}  "
        f"missing={counts['missing']}  skipped={counts['skipped']}"
    )
    if follow:
        line += f"  renamed={counts['renamed']}  rename_hints={counts['rename_hints']}"
    typer.echo(line)

    for renamed in result.renamed[:20]:
        typer.echo(f"  renamed  {renamed.entry.id[:8]}  {renamed.old_ref}  →  {renamed.new_ref}")
    for hint in result.rename_hints[:20]:
        typer.echo(
            f"  rename?  {hint.entry.id[:8]}  {hint.entry.source_ref}  →  "
            f"{hint.candidate_ref}  (content changed; not auto-applied)"
        )
    for entry in result.drifted[:20]:
        if any(h.entry.id == entry.id for h in result.rename_hints):
            continue
        typer.echo(f"  drifted  {entry.id[:8]}  {entry.source_ref}")
    for entry in result.missing[:20]:
        typer.echo(f"  missing  {entry.id[:8]}  {entry.source_ref}")


@app.command()
def index(
    path: Optional[str] = typer.Argument(
        None, help="Path to index (default: current directory)"
    ),
    scope: Optional[str] = typer.Option(
        None, "--scope", "-s",
        help="Only index files under this subpath of the agmem root. "
             "Preserves existing index entries for files outside the scope. "
             "Useful for workspace-level .agmem indexing a specific dir like 'plans/'.",
    ),
):
    """Index repository files into memory (deterministic, replaceable)."""
    try:
        read_config()
    except Exception:
        typer.echo("Not initialized. Run `agmem init` first.", err=True)
        raise typer.Exit(code=1)

    added, removed, files = run_index(cwd=path, scope=scope)
    suffix = f" (replaced {removed} old index entries)" if removed else ""
    scope_note = f" [scope: {scope}]" if scope else ""
    typer.echo(f"Indexed {files} files → {added} memories{scope_note}{suffix}")


@app.command(name="suggest-aliases")
def suggest_aliases(
    write: bool = typer.Option(
        False, "--write", "-w",
        help="Write the result to .agmem/aliases.auto.yaml (loaded by search alongside aliases.yaml).",
    ),
    min_synonyms: int = typer.Option(
        1, "--min-synonyms",
        help="Drop suggestions with fewer than N synonyms (filters very thin matches).",
    ),
):
    """Scan glossary-shaped markdown files in the repo and propose aliases.

    Looks for files whose name or top headers indicate a glossary, plus any
    markdown table whose first column is term-shaped. Reads ``| term | meaning |``
    rows and pulls a few significant tokens from each definition as alias candidates.

    Without ``--write`` the result is just printed in YAML form so you can review
    and copy what you want into ``.agmem/aliases.yaml``. With ``--write`` it goes
    to ``.agmem/aliases.auto.yaml`` (a separate file so your hand-curated aliases
    stay clean — search loads both).
    """
    import os
    from pathlib import Path

    import yaml as _yaml

    from . import config as _config
    from .indexer import _load_gitignore, _should_skip
    from .parsers.glossary import extract_aliases, is_glossary_file

    try:
        read_config()
    except Exception:
        typer.echo("Not initialized. Run `agmem init` first.", err=True)
        raise typer.Exit(code=1)

    root = _config.find_repo_root()
    spec = _load_gitignore(root)

    candidates: dict[str, list[str]] = {}
    scanned = 0
    glossary_files: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d not in {
                "node_modules", "__pycache__", "dist", "build",
                ".venv", ".tox", "site-packages",
            }
        ]
        for fname in filenames:
            if not fname.lower().endswith((".md", ".mdx")):
                continue
            full = Path(dirpath) / fname
            if _should_skip(full, root, spec):
                continue
            try:
                content = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            scanned += 1
            rel = str(full.relative_to(root))
            if not is_glossary_file(rel, content):
                continue
            new = extract_aliases(content)
            if not new:
                continue
            glossary_files.append(rel)
            for term, syns in new.items():
                existing = candidates.setdefault(term, [])
                for s in syns:
                    if s not in existing and s != term:
                        existing.append(s)

    candidates = {
        term: syns for term, syns in candidates.items()
        if len(syns) >= min_synonyms
    }

    if not candidates:
        typer.echo(
            f"Scanned {scanned} markdown files; no glossary-shaped tables found."
        )
        return

    typer.echo(f"# Found {len(candidates)} alias candidate(s) "
               f"in {len(glossary_files)} glossary file(s):")
    for f in glossary_files:
        typer.echo(f"#   - {f}")
    typer.echo("")
    typer.echo(_yaml.safe_dump(
        dict(sorted(candidates.items())),
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).rstrip())

    if write:
        agmem = _config.ensure_agmem_dir()
        target = agmem / "aliases.auto.yaml"
        header = (
            "# Auto-generated by `agmem suggest-aliases`. Do not hand-edit;\n"
            "# put curated aliases in aliases.yaml instead (search loads both).\n\n"
        )
        body = _yaml.safe_dump(
            dict(sorted(candidates.items())),
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
        target.write_text(header + body, encoding="utf-8")
        typer.echo(f"\nWrote {len(candidates)} aliases to {target}")


@app.command()
def stats(
    json_mode: bool = typer.Option(True, "--json/--text", help="JSON (default) or plain-text summary"),
):
    """Machine-readable snapshot of memory store + hot cache state.

    Designed for scripted loops (e.g., autoresearch-style "propose memory edit,
    measure, accept-or-revert"). Output shape is stable across versions.
    """
    try:
        read_config()
    except Exception:
        typer.echo("Not initialized. Run `agmem init` first.", err=True)
        raise typer.Exit(code=1)

    snapshot = collect_stats()
    if json_mode:
        import json
        typer.echo(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return

    m = snapshot["memories"]
    by_kind = ", ".join(f"{k}={v}" for k, v in sorted(m["by_kind"].items()))
    by_source = ", ".join(f"{k}={v}" for k, v in sorted(m["by_source"].items()))
    typer.echo(f"agmem_dir: {snapshot['agmem_dir']}")
    typer.echo(f"memories: live={m['live']} deleted={m['deleted']} drifted={m['drifted']} verified={m['verified']}")
    typer.echo(f"  by kind:   {by_kind or '(none)'}")
    typer.echo(f"  by source: {by_source or '(none)'}")
    typer.echo(f"  latest_index_ts:  {m['latest_index_ts'] or '(none)'}")
    typer.echo(f"  latest_manual_ts: {m['latest_manual_ts'] or '(none)'}")
    hot = snapshot["hot"]
    typer.echo(f"hot: exists={hot['exists']}" + (f" mtime={hot.get('mtime')}" if hot["exists"] else ""))


@app.command()
def update(
    since: str = typer.Option(
        "HEAD~1", "--since",
        help="Git ref to diff against (default: HEAD~1, i.e. since last commit)",
    ),
):
    """Diff-aware partial reindex: only re-analyze changed files since <ref>."""
    try:
        read_config()
    except Exception:
        typer.echo("Not initialized. Run `agmem init` first.", err=True)
        raise typer.Exit(code=1)

    result = run_update(since_ref=since)
    if "error" in result:
        typer.echo(result["error"], err=True)
        raise typer.Exit(code=1)
    if result["modified"] == 0 and result["added"] == 0 and result["deleted"] == 0:
        typer.echo(f"No changes since {since}.")
        return
    typer.echo(
        f"Updated since {since}: "
        f"{result['modified']} modified, {result['added']} added, {result['deleted']} deleted "
        f"→ {result['upserted']} upserted, {result['removed']} removed"
    )


@app.callback(invoke_without_command=True)
def version_callback(
    version: Optional[bool] = typer.Option(
        None, "--version", help="Show version and exit", is_eager=True
    ),
):
    if version:
        typer.echo(f"agmem {__version__}")
        raise typer.Exit()
