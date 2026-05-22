#!/usr/bin/env bash
# SessionEnd hook: remove this session's yolo-progress.json entry on session end.
# /yolo itself removes the entry on clean completion; this is the safety net for
# sessions that end abruptly (crash, kill, context limit).
set -uo pipefail

input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null || true)
session_id="${CLAUDE_CODE_SESSION_ID:-}"

[[ -z "$session_id" || -z "$cwd" ]] && exit 0

# Resolve to git root so the path is consistent with what /yolo writes.
project_dir=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null || echo "$cwd")
yolo_file="${project_dir}/.claude/yolo-progress.json"

[[ ! -f "$yolo_file" ]] && exit 0

# Temp file in the target dir so the final mv is an atomic same-filesystem
# rename (bare mktemp lands in $TMPDIR, often a different volume on macOS).
tmp=$(mktemp "${project_dir}/.claude/.yolo-progress.XXXXXX")
trap 'rm -f "$tmp"' EXIT   # Failure expectation: tmp may already be removed on re-entry

if jq --arg sid "$session_id" 'del(.[$sid])' "$yolo_file" >"$tmp" 2>/dev/null; then
    remaining=$(jq 'keys | length' "$tmp" 2>/dev/null || echo 1)
    if [[ "$remaining" == "0" ]]; then
        rm -f "$yolo_file"
    else
        mv -f "$tmp" "$yolo_file"
    fi
fi

exit 0
