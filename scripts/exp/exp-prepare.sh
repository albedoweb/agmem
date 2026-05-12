#!/bin/sh
# Prepare an A/B agmem experiment.
#
# Usage:
#   exp-prepare.sh <task-id> <repo-path> [<task-description>]
#
# What it does:
#   - Verifies the repo is clean (no uncommitted changes).
#   - Records the baseline commit so we can reset between sessions.
#   - Creates ~/agmem-experiments/<task-id>/{A,B,config}.
#
# After it finishes, run your "control" session (no agmem) in the repo,
# then call exp-finish-a.sh.

DIR="$(cd "$(dirname "$0")" && pwd)"
. "$DIR/_common.sh"

task_id="${1:-}"
repo_path="${2:-}"
description="${3:-}"

require_arg "$task_id" "task-id required (e.g. exp-prepare.sh bastion-2 /path/to/repo)"
require_arg "$repo_path" "repo-path required"

repo_path="$(cd "$repo_path" 2>/dev/null && pwd || echo "$repo_path")"
[ -d "$repo_path/.git" ] || die "$repo_path is not a git repository"

if ! git_clean_or_warn "$repo_path"; then
    printf 'commit/stash these first, or set AGMEM_EXP_ALLOW_DIRTY=1 to override.\n' >&2
    [ "${AGMEM_EXP_ALLOW_DIRTY:-}" = "1" ] || exit 1
fi

baseline="$(git -C "$repo_path" rev-parse HEAD)"
baseline_branch="$(git -C "$repo_path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo)"
exp="$(exp_dir "$task_id")"

# Warn if the experiment dir would land inside the target repo's working tree —
# our reset between sessions would wipe it. Refuse unless explicitly allowed.
case "$exp/" in
    "$repo_path"/*)
        if [ "${AGMEM_EXP_ALLOW_INSIDE_REPO:-}" != "1" ]; then
            die "experiment dir $exp is inside target repo $repo_path; the reset step would wipe it. cd elsewhere or set AGMEM_EXP_ROOT."
        fi
        ;;
esac

if [ -d "$exp" ]; then
    if [ "${AGMEM_EXP_FORCE:-}" = "1" ]; then
        rm -rf "$exp"
    else
        die "experiment '$task_id' already exists at $exp. Use AGMEM_EXP_FORCE=1 to overwrite or pick a new id."
    fi
fi

mkdir -p "$exp/A" "$exp/B"
cfg="$exp/config"
: > "$cfg"
write_config_kv "$cfg" task_id "$task_id"
write_config_kv "$cfg" repo_path "$repo_path"
write_config_kv "$cfg" baseline_commit "$baseline"
write_config_kv "$cfg" baseline_branch "$baseline_branch"
write_config_kv "$cfg" description "$description"
write_config_kv "$cfg" prepared_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat <<EOF
prepared experiment: $task_id
  repo:     $repo_path
  baseline: $baseline
  dir:      $exp

next steps:
  1. Run your "control" (no-agmem) session in the repo. Use Claude Code as usual.
  2. When done (don't commit; leave changes in working tree):
     $DIR/exp-finish-a.sh $task_id

  3. Then run the "treatment" (with-agmem) session.
  4. When done:
     $DIR/exp-finish-b.sh $task_id
EOF
