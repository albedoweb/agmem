#!/bin/sh
# Finish the "treatment" (with-agmem) side of an A/B experiment and emit a report.
#
# Usage:
#   exp-finish-b.sh <task-id> [--keep-changes]
#
# What it does:
#   - Captures B's git diff / status / new files / Claude Code transcript.
#   - Writes ~/agmem-experiments/<task-id>/REPORT.md with a side-by-side
#     comparison of A and B (file/line/transcript metrics) and pointers to
#     the artifacts so you can review qualitatively.
#   - Resets the repo back to baseline (skip with --keep-changes).

DIR="$(cd "$(dirname "$0")" && pwd)"
. "$DIR/_common.sh"

task_id="${1:-}"
require_arg "$task_id" "task-id required"

keep_changes=0
[ "${2:-}" = "--keep-changes" ] && keep_changes=1

exp="$(find_exp_dir "$task_id")" || die "experiment '$task_id' not found"
EXP_ROOT="$(dirname "$exp")"

repo_path="$(read_config "$task_id" repo_path)"
baseline="$(read_config "$task_id" baseline_commit)"
description="$(read_config "$task_id" description)"
prepared_at="$(read_config "$task_id" prepared_at)"
finished_a_at="$(read_config "$task_id" finished_a_at)"
[ -n "$repo_path" ] || die "config missing repo_path"
[ -n "$baseline" ] || die "config missing baseline_commit"

if [ ! -d "$exp/A" ] || [ ! -f "$exp/A/metrics.txt" ]; then
    die "A side hasn't been captured. Run exp-finish-a.sh first."
fi

echo "capturing B (treatment) artifacts from $repo_path …"
capture_side "$task_id" B "$repo_path"

write_config_kv "$exp/config" finished_b_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# --- Build report --------------------------------------------------------------

read_metric() {
    awk -F= -v k="$2" '$1==k{print $2}' "$1"
}

a_files="$(read_metric "$exp/A/metrics.txt" files_modified)"
a_lines="$(read_metric "$exp/A/metrics.txt" lines_added)"
a_new="$(read_metric "$exp/A/metrics.txt" new_files)"
a_commits="$(read_metric "$exp/A/metrics.txt" commits_made)"
a_tr="$(read_metric "$exp/A/metrics.txt" transcript_lines)"

b_files="$(read_metric "$exp/B/metrics.txt" files_modified)"
b_lines="$(read_metric "$exp/B/metrics.txt" lines_added)"
b_new="$(read_metric "$exp/B/metrics.txt" new_files)"
b_commits="$(read_metric "$exp/B/metrics.txt" commits_made)"
b_tr="$(read_metric "$exp/B/metrics.txt" transcript_lines)"

report="$exp/REPORT.md"
{
    echo "# Experiment: $task_id"
    echo
    [ -n "$description" ] && { echo "**Task:** $description"; echo; }
    echo "**Repo:** \`$repo_path\`"
    echo
    echo "**Baseline commit:** \`$baseline\`"
    echo
    echo "**Timeline:** prepared $prepared_at · A finished $finished_a_at · B finished $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo
    echo "## Quantitative comparison"
    echo
    echo "| metric | A (no agmem) | B (with agmem) | Δ |"
    echo "|---|---|---|---|"
    echo "| files modified | ${a_files:-0} | ${b_files:-0} | $(( ${b_files:-0} - ${a_files:-0} )) |"
    echo "| lines added | ${a_lines:-0} | ${b_lines:-0} | $(( ${b_lines:-0} - ${a_lines:-0} )) |"
    echo "| new files | ${a_new:-0} | ${b_new:-0} | $(( ${b_new:-0} - ${a_new:-0} )) |"
    echo "| commits made | ${a_commits:-0} | ${b_commits:-0} | $(( ${b_commits:-0} - ${a_commits:-0} )) |"
    echo "| transcript turns | ${a_tr:-0} | ${b_tr:-0} | $(( ${b_tr:-0} - ${a_tr:-0} )) |"
    echo
    echo "## Artifacts"
    echo
    echo "- A: \`$exp/A/{git.diff, git.status, new-files.txt, transcript.jsonl}\`"
    echo "- B: \`$exp/B/{git.diff, git.status, new-files.txt, transcript.jsonl}\`"
    echo
    echo "## Quick diff between sessions"
    echo
    echo '```'
    diff -u "$exp/A/git.diff" "$exp/B/git.diff" 2>&1 | head -60 || true
    echo '```'
    echo

    a_run=""
    b_run=""
    [ -f "$exp/A/run_id.txt" ] && a_run="$(cat "$exp/A/run_id.txt")"
    [ -f "$exp/B/run_id.txt" ] && b_run="$(cat "$exp/B/run_id.txt")"
    if [ -n "$a_run" ] && [ -n "$b_run" ] && command -v agent-diff >/dev/null 2>&1; then
        echo "## agent-diff comparison"
        echo
        echo "Run IDs: A=\`$a_run\`  B=\`$b_run\`"
        echo
        echo '```'
        agent-diff diff "$a_run" "$b_run" 2>&1 | head -80
        echo '```'
        echo
        echo "Drill in:"
        echo "- \`agent-diff show $a_run\` — full A timeline"
        echo "- \`agent-diff show $b_run\` — full B timeline"
        echo "- \`agent-diff diff $a_run $b_run --show-tool-payloads\` — verbose"
        echo
    elif [ -f "$exp/A/transcript.jsonl" ] || [ -f "$exp/B/transcript.jsonl" ]; then
        echo "## agent-diff comparison"
        echo
        echo "_Skipped — agent-diff not on PATH. To enable:_"
        echo "_\`uv tool install agent-diff\` then re-run this experiment._"
        echo
    fi
    echo "## Qualitative review checklist"
    echo
    echo "- [ ] Did B reuse existing modules / patterns that A reinvented?"
    echo "- [ ] Did B avoid an obvious pitfall A fell into?"
    echo "- [ ] Did A and B converge on the same solution shape?"
    echo "- [ ] Was B more or less verbose? (Total lines and transcript turns above.)"
    echo "- [ ] Any agmem-induced wrong direction (followed a stale rule)?"
} > "$report"

echo
echo "wrote $report"
echo
echo "summary:"
printf '  %-12s  A=%s  B=%s\n' "files"     "${a_files:-0}" "${b_files:-0}"
printf '  %-12s  A=%s  B=%s\n' "lines"     "${a_lines:-0}" "${b_lines:-0}"
printf '  %-12s  A=%s  B=%s\n' "new files" "${a_new:-0}"   "${b_new:-0}"
printf '  %-12s  A=%s  B=%s\n' "turns"     "${a_tr:-0}"    "${b_tr:-0}"

if [ "$keep_changes" -eq 0 ]; then
    baseline_branch="$(read_config "$task_id" baseline_branch)"
    echo
    echo "resetting $repo_path to ${baseline_branch:-baseline} @ $baseline …"
    git_reset_to "$repo_path" "$baseline" "$baseline_branch"
else
    echo
    echo "(keeping current B working-tree changes; pass without --keep-changes to reset)"
fi

cat <<EOF

experiment '$task_id' complete.
review the report:
  cat $report

re-run with a different task:
  $DIR/exp-prepare.sh <new-task-id> $repo_path [description]

clean up this experiment dir:
  rm -rf $exp
EOF
