#!/usr/bin/env bash
# PreCompact hook: inject a reconciliation directive for /yolo sessions.
#
# Self-gating: exits silently for non-/yolo sessions. For /yolo sessions,
# emits an additionalContext payload instructing the summarizer to preserve
# a task-reconciliation directive verbatim so the post-compaction agent sees
# it and reconciles the JSON before resuming work.
set -euo pipefail

input=$(cat)
transcript=$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null || true)

[[ -z "$transcript" || ! -f "$transcript" ]] && exit 0

grep -q 'command-name>/yolo' "$transcript" || exit 0

printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreCompact","additionalContext":"YOLO TASK STATE — preserve this paragraph verbatim in the compaction summary: an autonomous /yolo session is in progress; durable task state is recorded in .claude/yolo-progress.json (keyed by session id). IMMEDIATELY after compaction completes, before resuming any work item, the agent MUST: (1) run python3 ~/.claude/hooks/update-yolo-progress.py task-list --format json to read the current task state, (2) for any task whose status is in_progress or not_started but which the agent knows from the compacted context is actually completed or aborted, call python3 ~/.claude/hooks/update-yolo-progress.py task-update --tid <tid> --status <status> --outcome \"<one sentence>\" (and --commit <sha> --capture-diff if applicable), (3) for any work referenced in the summary not present in the JSON, call task-add then task-update to register it. This sweep is mandatory; skipping it means work history is permanently lost."}}'
exit 0
