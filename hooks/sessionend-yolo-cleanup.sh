#!/usr/bin/env bash
# SessionEnd hook: mark this session complete/aborted in yolo-progress.json,
# then opportunistically run the worktree janitor.
#
# The /yolo skill calls 'complete' on clean goal-state reach; this is the safety
# net for sessions that end abruptly. Sets status='aborted' when any tasks remain
# unfinished, 'completed' otherwise. Does NOT delete the entry (retention sweep
# in SessionStart handles eventual cleanup).
#
# Worktree janitor (opt-in, OFF by default — see Janitor section below):
# enforces four hard gates before tearing down any linked worktree, delegates
# the actual teardown to ~/.claude/skills/rmws/scripts/rmws.py --teardown
# (the canonical primitive — never duplicates the teardown logic).
set -euo pipefail

input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null || true)
session_id="${CLAUDE_CODE_SESSION_ID:-}"

[[ -z "$session_id" || -z "$cwd" ]] && exit 0

project_dir=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null || echo "$cwd")
helper="${HOME}/.claude/hooks/update-yolo-progress.py"

[[ -f "$helper" ]] || exit 0

python3 "$helper" complete --abort-if-incomplete \
    2>/dev/null || true

############################################################
# Janitor — opt-in cleanup of already-merged bot worktrees #
############################################################
#
# OFF by default. Enable per-project by creating an empty marker file:
#   touch <project>/.claude/yolo-janitor.enabled
# Or globally by exporting YOLO_JANITOR_ENABLED=1 in your shell env.
#
# Four hard gates (ALL required):
#   1. Worktree path is under <project>/.claude/worktrees/
#   2. Branch is an ancestor of main (git merge-base --is-ancestor)
#   3. Working tree clean (ignoring .claude/settings.local.json noise)
#   4. Branch's last commit is older than YOLO_JANITOR_AGE_SECONDS (default 86400)
#
# All actions logged to <project>/.claude/worktree-janitor.log. The teardown
# itself is delegated to rmws.py --teardown, which uses `git branch -d`
# (never -D) — git's own merged-check is the last-line safety net, so the
# janitor is mathematically incapable of destroying unmerged work.

janitor_enabled=0
[[ "${YOLO_JANITOR_ENABLED:-0}" == "1" ]] && janitor_enabled=1
[[ -f "$project_dir/.claude/yolo-janitor.enabled" ]] && janitor_enabled=1
[[ $janitor_enabled -eq 0 ]] && exit 0

rmws_teardown="${HOME}/.claude/skills/rmws/scripts/rmws.py"
[[ -f "$rmws_teardown" ]] || exit 0

age_threshold="${YOLO_JANITOR_AGE_SECONDS:-86400}"
log_file="$project_dir/.claude/worktree-janitor.log"
mkdir -p "$(dirname "$log_file")"

# Resolve target branch (default main, override via YOLO_JANITOR_TARGET).
target="${YOLO_JANITOR_TARGET:-main}"

# Don't run if there's no target branch — protects fresh repos.
git -C "$project_dir" rev-parse --verify --quiet "refs/heads/$target" >/dev/null \
    || exit 0

now=$(date +%s)

# Parse `git worktree list --porcelain` once; emit "<path>|<branch>" pairs
# for every linked worktree (the primary worktree has no `branch refs/heads/`
# line in porcelain output for detached HEAD primary; we skip non-branch
# entries naturally).
git -C "$project_dir" worktree list --porcelain 2>/dev/null \
  | awk '
      /^worktree / { wt = substr($0, 10); br = "" }
      /^branch refs\/heads\// {
          br = substr($0, 19)
          if (wt != "" && br != "") print wt "|" br
          wt = ""; br = ""
      }
  ' \
  | while IFS='|' read -r wt_path branch; do
        # Skip the primary worktree (it appears first in porcelain output).
        [[ "$wt_path" == "$project_dir" ]] && continue

        # Gate 1: path under .claude/worktrees/
        case "$wt_path" in
            "$project_dir/.claude/worktrees/"*) ;;
            *)
                printf '%s SKIP path-gate %s (%s)\n' \
                    "$(date -u +%FT%TZ)" "$wt_path" "$branch" >>"$log_file"
                continue
                ;;
        esac

        # Gate 2: branch ancestor of target
        if ! git -C "$project_dir" merge-base --is-ancestor \
                "$branch" "$target" 2>/dev/null; then
            printf '%s SKIP unmerged %s (%s)\n' \
                "$(date -u +%FT%TZ)" "$wt_path" "$branch" >>"$log_file"
            continue
        fi

        # Gate 3: clean worktree (allow only the known noise file)
        dirty=$(git -C "$wt_path" status --porcelain 2>/dev/null \
                | grep -vE '^.. \.claude/settings\.local\.json$' || true)
        if [[ -n "$dirty" ]]; then
            printf '%s SKIP dirty %s (%s)\n' \
                "$(date -u +%FT%TZ)" "$wt_path" "$branch" >>"$log_file"
            continue
        fi

        # Gate 4: age > threshold (use branch tip committer date)
        commit_ts=$(git -C "$project_dir" log -1 --format=%ct "$branch" \
                    2>/dev/null || echo "$now")
        age=$(( now - commit_ts ))
        if (( age < age_threshold )); then
            printf '%s SKIP recent %s (%s age=%ds)\n' \
                "$(date -u +%FT%TZ)" "$wt_path" "$branch" "$age" >>"$log_file"
            continue
        fi

        # All gates passed — delegate to the canonical teardown primitive.
        # We cd into project_dir because rmws.py --teardown requires its
        # cwd's git toplevel to equal the primary checkout.
        if result=$(cd "$project_dir" && python3 "$rmws_teardown" \
                    --teardown "$branch" "$target" 2>&1); then
            printf '%s CLEAN %s (%s)\n' \
                "$(date -u +%FT%TZ)" "$wt_path" "$branch" >>"$log_file"
        else
            rc=$?
            # Compress the JSON noise into one log line; preserve the trailing
            # stderr summary `[rmws] status: message` for debuggability.
            summary=$(printf '%s' "$result" | grep '^\[rmws\]' | tail -1)
            printf '%s FAIL %s (%s rc=%d %s)\n' \
                "$(date -u +%FT%TZ)" "$wt_path" "$branch" "$rc" "$summary" \
                >>"$log_file"
        fi
    done

exit 0
