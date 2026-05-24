#!/usr/bin/env bash
# PreCompact hook: instruct the summarizer to extract and preserve YOLO task
# state in the compaction summary so the post-compaction agent can update the
# JSON from accurate, summarizer-extracted data rather than degraded memory.
#
# Self-gating: exits silently for non-/yolo sessions (zero cost).
set -euo pipefail

input=$(cat)
transcript=$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null || true)

[[ -z "$transcript" || ! -f "$transcript" ]] && exit 0

grep -q 'command-name>/yolo' "$transcript" || exit 0

python3 - <<'PYEOF'
import json, sys

context = """\
INSTRUCTION FOR SUMMARIZER — /yolo session in progress:

Before writing your summary, scan the ENTIRE conversation for YOLO task \
activity. Build a YOLO_TASK_STATE block from what you observe and include it \
VERBATIM at the very beginning of your summary (before any prose), using this \
exact format:

YOLO_TASK_STATE_BEGIN
{"tasks":[{"tid":"t1","source_id":"gh#42","status":"completed",\
"outcome":"one sentence","commit":"abc1234 or null"},\
{"tid":"t2","source_id":"todo:Refactor auth","status":"in_progress",\
"outcome":null,"commit":null}],"notes":"any important session context"}
YOLO_TASK_STATE_END

Rules for building the block:
- tid: the task id from task-update calls (t1, t2, ...); null if not visible
- source_id: gh#N, todo:..., plan:..., or description — from init or task-add calls
- status: the LAST status you observed for each task in this conversation window
- outcome: the --outcome value from the last task-update call, or null
- commit: the --commit sha from the last task-update call, or null
- Include every task you can identify as having been worked in this window
- If no tasks are identifiable, write: YOLO_TASK_STATE_BEGIN {} YOLO_TASK_STATE_END

After the block, write your normal summary as usual. The post-compaction \
agent will read the YOLO_TASK_STATE block and update the durable task JSON \
from it — this is the mechanism that keeps session history accurate across \
compactions.\
"""

payload = {
    "hookSpecificOutput": {
        "hookEventName": "PreCompact",
        "additionalContext": context,
    }
}
print(json.dumps(payload))
PYEOF
exit 0
