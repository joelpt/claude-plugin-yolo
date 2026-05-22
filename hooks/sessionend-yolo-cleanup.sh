#!/usr/bin/env bash
# SessionEnd hook: mark this session complete/aborted in yolo-progress.json.
# The /yolo skill calls 'complete' on clean goal-state reach; this is the safety
# net for sessions that end abruptly. Sets status='aborted' when any tasks remain
# unfinished, 'completed' otherwise. Does NOT delete the entry (retention sweep
# in SessionStart handles eventual cleanup).
set -euo pipefail

input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null || true)
session_id="${CLAUDE_CODE_SESSION_ID:-}"

[[ -z "$session_id" || -z "$cwd" ]] && exit 0

project_dir=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null || echo "$cwd")
helper="${HOME}/.claude/hooks/update-yolo-progress.py"

[[ -f "$helper" ]] || exit 0

python3 "$helper" --work-dir "$project_dir" complete --abort-if-incomplete \
    2>/dev/null || true

exit 0
