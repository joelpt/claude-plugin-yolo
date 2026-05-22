#!/usr/bin/env bash
# Stop hook: completion gate for /yolo autonomous sessions.
#
# Plugin-provided skills do not honor frontmatter `hooks:` (claude-code#17688),
# so the gate that lived in the yolo skill's frontmatter is reimplemented here
# as a plugin-level Stop hook.
#
# Self-gating: any session that is not a /yolo run exits immediately, paying
# only a transcript grep. For a /yolo session, Stop is blocked until the recent
# transcript shows the goal-state declaration the skill is required to print.
set -uo pipefail

input=$(cat)

# Loop protection: don't re-evaluate while Claude is continuing due to this hook.
stop_active=$(printf '%s' "$input" | jq -r '.stop_hook_active // false' 2>/dev/null || echo false)
[[ "$stop_active" == "true" ]] && exit 0

transcript=$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null || true)
[[ -z "$transcript" || ! -f "$transcript" ]] && exit 0

# Not a /yolo session -> allow Stop silently (zero cost for every other session).
grep -q 'command-name>/yolo' "$transcript" || exit 0

# /yolo session: allow Stop only when the recent transcript shows goal state.
# The skill is required to print a "/yolo recap:" block and an explicit line
# ending "all completable work done" when (and only when) the run is complete.
recent=$(tail -c 131072 "$transcript" 2>/dev/null || true)
if printf '%s' "$recent" | grep -qE 'all completable work done|/yolo recap:'; then
    exit 0
fi

printf '%s' '{"decision":"block","reason":"/yolo completion gate: goal state not reached. Keep working pending items (GitHub issues / TODO.md / PLAN.md). Route anything you cannot decide alone into USER_TODO.md and continue with parallel work. When every completable item is done, print the /yolo recap block and the explicit goal-state line to release this gate."}'
exit 0
