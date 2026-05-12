#!/bin/sh
# Finish the "control" (no-agmem) side of an A/B experiment.
#
# Usage:
#   exp-finish-a.sh <task-id>
#
# What it does:
#   - Captures git diff, git status, new files, and the latest Claude Code
#     transcript (if found) to ~/agmem-experiments/<task-id>/A/.
#   - Resets the repo back to the baseline commit so the B (treatment)
#     session starts from an identical clean slate.
#
# Run this BEFORE starting the B session.

DIR="$(cd "$(dirname "$0")" && pwd)"
. "$DIR/_common.sh"

task_id="${1:-}"
require_arg "$task_id" "task-id required (e.g. exp-finish-a.sh bastion-2)"

exp="$(find_exp_dir "$task_id")" || die "experiment '$task_id' not found. Run exp-prepare.sh first (or set AGMEM_EXP_ROOT)."

repo_path="$(read_config "$task_id" repo_path)"
baseline="$(read_config "$task_id" baseline_commit)"
[ -n "$repo_path" ] || die "config missing repo_path"
[ -n "$baseline" ] || die "config missing baseline_commit"
[ -d "$repo_path/.git" ] || die "$repo_path is no longer a git repo"

echo "capturing A (control) artifacts from $repo_path …"
# Realign EXP_ROOT in case the user invoked finish from a different cwd than prepare.
EXP_ROOT="$(dirname "$exp")"
capture_side "$task_id" A "$repo_path"

echo "A metrics:"
print_metrics "$exp/A/metrics.txt"

baseline_branch="$(read_config "$task_id" baseline_branch)"
echo
echo "resetting $repo_path to ${baseline_branch:-baseline} @ $baseline …"
git_reset_to "$repo_path" "$baseline" "$baseline_branch"

write_config_kv "$exp/config" finished_a_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat <<EOF

A done. Repo is back to baseline.

next:
  - Now run the "treatment" (with-agmem) session on the same task.
  - Make sure 'agmem hot' / 'agmem context' is wired in the agent's prompt.
  - When done (don't commit):
      $DIR/exp-finish-b.sh $task_id
EOF
