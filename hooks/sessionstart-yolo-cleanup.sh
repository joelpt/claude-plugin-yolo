#!/usr/bin/env bash
# SessionStart hook for the yolo plugin. Two jobs:
#   1. Self-heal the update-yolo-progress.py symlink at a stable, version-agnostic
#      path so the /yolo skill body can call it without knowing the plugin-cache path.
#   2. Prune yolo-progress.json entries per retention policy (14d completed, 7d in-flight).
set -euo pipefail

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

python3 "$helper_dst" --work-dir "$project_dir" prune \
    --cutoff-completed-days 14 \
    --cutoff-inflight-days 7 \
    2>/dev/null || true

exit 0
