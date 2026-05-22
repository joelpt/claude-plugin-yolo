#!/usr/bin/env bash
# SessionStart hook for the yolo plugin. Two jobs:
#   1. Self-heal the update-yolo-progress.py symlink at a stable, version-agnostic
#      path so the /yolo skill body can call it without knowing the plugin-cache path.
#   2. Prune yolo-progress.json entries older than 48h (per-project sweep).
set -uo pipefail

plugin_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
helper_src="${plugin_root}/bin/update_yolo_progress.py"
helper_dst="${HOME}/.claude/hooks/update-yolo-progress.py"
if [[ -f "$helper_src" ]]; then
    mkdir -p "$(dirname "$helper_dst")"
    ln -sfn "$helper_src" "$helper_dst"
fi

input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null || true)

[[ -z "$cwd" ]] && exit 0

project_dir=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null || echo "$cwd")
yolo_file="${project_dir}/.claude/yolo-progress.json"

[[ ! -f "$yolo_file" ]] && exit 0

cutoff=$(( $(date +%s) - 172800 ))   # 48h in seconds

# Temp file in the target dir so the final mv is an atomic same-filesystem
# rename (bare mktemp lands in $TMPDIR, often a different volume on macOS).
tmp=$(mktemp "${project_dir}/.claude/.yolo-progress.XXXXXX")
trap 'rm -f "$tmp"' EXIT   # Failure expectation: tmp may already be gone on re-entry

if jq --argjson cutoff "$cutoff" '
  with_entries(
    select(
      (.value.updated // "1970-01-01T00:00:00Z") |
      (strptime("%Y-%m-%dT%H:%M:%SZ") | mktime) >= $cutoff
    )
  )
' "$yolo_file" >"$tmp" 2>/dev/null; then
    remaining=$(jq 'keys | length' "$tmp" 2>/dev/null || echo 1)
    if [[ "$remaining" == "0" ]]; then
        rm -f "$yolo_file"
    else
        mv -f "$tmp" "$yolo_file"
    fi
fi

exit 0
