#!/bin/sh
# Shared helpers for the agmem A/B experiment harness.
# Sourced by exp-prepare.sh / exp-finish-a.sh / exp-finish-b.sh — not run directly.

set -eu

# Where experiments are stored. Default: ./agmem-experiments next to where you
# invoke the harness. Override per-call with AGMEM_EXP_ROOT.
# Add 'agmem-experiments/' to your .gitignore if you don't want them tracked.
EXP_ROOT="${AGMEM_EXP_ROOT:-$(pwd)/agmem-experiments}"

# Search order used by finish scripts to locate an existing experiment when the
# user's cwd differs from where prepare was run. First match wins.
find_exp_dir() {
    local id="$1" candidate
    for candidate in \
        "${AGMEM_EXP_ROOT:-}/$id" \
        "$(pwd)/agmem-experiments/$id" \
        "$HOME/agmem-experiments/$id"
    do
        [ -n "$candidate" ] || continue
        if [ -d "$candidate" ] && [ -f "$candidate/config" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

die() {
    echo "error: $*" >&2
    exit 1
}

require_arg() {
    [ -n "${1:-}" ] || die "$2"
}

exp_dir() {
    echo "$EXP_ROOT/$1"
}

read_config() {
    # Args: task_id, key. Echoes value or empty.
    # Resolves the experiment dir via find_exp_dir, so it works after a cd.
    local resolved
    resolved="$(find_exp_dir "$1")" || return 0
    local cfg="$resolved/config"
    [ -f "$cfg" ] || return 0
    awk -F= -v k="$2" '$1==k { sub(/^[^=]*=/, ""); print; exit }' "$cfg"
}

write_config_kv() {
    # Append/overwrite a key=value pair in the config file.
    local cfg="$1" key="$2" val="$3"
    if [ -f "$cfg" ] && grep -q "^${key}=" "$cfg"; then
        # Replace in place (portable sed across macOS/Linux: write to .tmp).
        awk -v k="$key" -v v="$val" -F= '
            $1==k { print k"="v; next }
            { print }
        ' "$cfg" > "${cfg}.tmp" && mv "${cfg}.tmp" "$cfg"
    else
        echo "${key}=${val}" >> "$cfg"
    fi
}

# Find the Claude Code session transcript directory that corresponds to a repo.
# Claude Code stores session JSONL under ~/.claude/projects/<encoded-cwd>/.
# The encoding replaces / with -.
claude_project_dir() {
    local repo_path="$1"
    local encoded
    encoded="$(echo "$repo_path" | sed 's|^/||; s|/|-|g')"
    echo "$HOME/.claude/projects/-$encoded"
}

# Locate the most recently modified jsonl in a Claude Code project dir.
# Echoes the path, or empty if none. Used to optionally archive the transcript.
latest_claude_transcript() {
    local proj="$1"
    [ -d "$proj" ] || return 0
    ls -t "$proj"/*.jsonl 2>/dev/null | head -1
}

# Import a captured Claude Code transcript into agent-diff (if installed).
# Echoes the resulting run_id (e.g. ``cc_2026-05-09T15-45_abcd1234``) or empty.
_agent_diff_import() {
    local transcript="$1"
    [ -f "$transcript" ] || return 0
    command -v agent-diff >/dev/null 2>&1 || return 0
    agent-diff import claude-code "$transcript" 2>/dev/null \
        | awk '/^[[:space:]]*cc_/ { print $1; exit }'
}

git_clean_or_warn() {
    local repo="$1"
    local status
    status="$(git -C "$repo" status --porcelain)"
    if [ -n "$status" ]; then
        echo "warn: $repo has uncommitted changes:" >&2
        echo "$status" | head -20 >&2
        return 1
    fi
    return 0
}

git_reset_to() {
    # Reset working tree + branch back to baseline. Restores the original
    # branch if one was passed (3rd arg) so a session that ran `git checkout`
    # doesn't leave the user stranded.
    local repo="$1" commit="$2" branch="${3:-}"
    if [ -n "$branch" ]; then
        local current
        current="$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null || echo)"
        if [ "$current" != "$branch" ] && [ "$current" != "HEAD" ]; then
            git -C "$repo" checkout -q "$branch" 2>/dev/null || true
        fi
    fi
    git -C "$repo" reset --hard "$commit"
    git -C "$repo" clean -fd
}

count_lines_in_diff() {
    local diff_file="$1"
    [ -f "$diff_file" ] || { echo 0; return; }
    grep -E '^\+[^+]' "$diff_file" | wc -l | tr -d ' '
}

count_files_in_diff() {
    local diff_file="$1"
    [ -f "$diff_file" ] || { echo 0; return; }
    grep -cE '^diff --git ' "$diff_file" || true
}

count_jsonl_lines() {
    local f="$1"
    [ -f "$f" ] || { echo 0; return; }
    wc -l < "$f" | tr -d ' '
}

# Capture the artifacts of the session that just finished.
# Args: task_id, side (A or B), repo_path
capture_side() {
    local task_id="$1" side="$2" repo="$3"
    local out
    out="$(exp_dir "$task_id")/$side"
    mkdir -p "$out"

    # Diff against baseline catches everything since prepare: working tree,
    # staged, AND any commits the session decided to make. We also record the
    # current HEAD so commits made during the session are visible.
    local baseline
    baseline="$(awk -F= '$1=="baseline_commit"{print $2}' "$(exp_dir "$task_id")/config")"
    git -C "$repo" status --porcelain > "$out/git.status"
    if [ -n "$baseline" ]; then
        git -C "$repo" diff "$baseline" > "$out/git.diff"
        git -C "$repo" log --oneline "$baseline"..HEAD > "$out/commits.txt" 2>/dev/null || true
    else
        git -C "$repo" diff > "$out/git.diff"
    fi
    git -C "$repo" rev-parse HEAD > "$out/head.txt"
    git -C "$repo" rev-parse --abbrev-ref HEAD > "$out/branch.txt" 2>/dev/null || true
    git -C "$repo" ls-files --others --exclude-standard > "$out/new-files.txt"

    local proj transcript
    proj="$(claude_project_dir "$repo")"
    transcript="$(latest_claude_transcript "$proj")"
    if [ -n "$transcript" ]; then
        cp "$transcript" "$out/transcript.jsonl"
        # If agent-diff is on PATH, register the run for pairwise comparison.
        local run_id
        run_id="$(_agent_diff_import "$out/transcript.jsonl")"
        [ -n "$run_id" ] && echo "$run_id" > "$out/run_id.txt"
    fi

    local commits_n=0
    [ -f "$out/commits.txt" ] && commits_n="$(wc -l < "$out/commits.txt" | tr -d ' ')"
    {
        echo "side=$side"
        echo "captured_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "files_modified=$(count_files_in_diff "$out/git.diff")"
        echo "lines_added=$(count_lines_in_diff "$out/git.diff")"
        echo "new_files=$(wc -l < "$out/new-files.txt" | tr -d ' ')"
        echo "commits_made=$commits_n"
        echo "transcript_lines=$(count_jsonl_lines "$out/transcript.jsonl")"
    } > "$out/metrics.txt"
}

print_metrics() {
    local file="$1"
    [ -f "$file" ] || return 0
    while IFS='=' read -r k v; do
        printf '  %-20s %s\n' "$k" "$v"
    done < "$file"
}
