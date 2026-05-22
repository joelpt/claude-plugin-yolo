# yolo

Autonomous work mode for Claude Code. Invoke `/yolo:yolo` and Claude works every pending item — GitHub issues, `TODO.md`, `PLAN.md` — sequentially, committing as it goes, without pausing for normal-decision calls.

## What it does

- **`/yolo:yolo`** — enters autonomous mode. Claude picks work from the highest-priority source (GitHub issues → `TODO.md` → `PLAN.md`/`README.md`), does each item, verifies, commits, and repeats until everything completable is done.
- **`USER_TODO.md`** — anything Claude can't safely decide alone (irreversible ops, secrets, personal-preference calls) is routed here instead of guessed. A startup interview front-loads any pending items.
- **Stop completion gate** — a `Stop` hook blocks the session from ending while a `/yolo` run is in progress and has not yet declared goal state. See below.
- **Statusline progress pulse** — after each item (and at the start of each loop) Claude writes `{percent, updated}` to the project's `.claude/yolo-progress.json`, keyed by session id, which the statusline can render as `Y:N%`.

## Plugin layout

```text
.claude-plugin/plugin.json          manifest (CalVer: YYYY.MM.DD.N)
skills/yolo/SKILL.md                the /yolo:yolo skill — autonomous-mode instructions
commands/yolo.md                    /yolo:yolo slash command (invokes the skill + /loop)
hooks/hooks.json                    SessionStart, SessionEnd, Stop wiring
hooks/sessionstart-yolo-cleanup.sh  prune stale progress entries; heal helper symlink
hooks/sessionend-yolo-cleanup.sh    remove this session's progress entry
hooks/yolo-completion-gate.sh       Stop gate — blocks until goal state reached
bin/update_yolo_progress.py         helper: write/remove a session's progress entry
tests/test_update_yolo_progress.py  unit tests for the helper
```

## The completion gate

The original `/yolo` skill declared its completion gate as a `Stop` hook in SKILL.md
frontmatter. Plugin-provided skills do **not** honor frontmatter `hooks:` blocks
([claude-code#17688](https://github.com/anthropics/claude-code/issues/17688)), so the
gate is reimplemented as a plugin-level `Stop` hook in `hooks.json`.

`hooks/yolo-completion-gate.sh` self-gates: it scans the transcript for a
`/yolo` invocation marker and exits immediately (zero cost) for any session that is
not a `/yolo` run. For a real `/yolo` session it blocks `Stop` unless the recent
transcript shows `Goal state reached` (or a `/yolo recap:`), keeping the autonomous
run going until the work is genuinely done. The check is deterministic — no LLM call —
which is stricter and cheaper than the original Haiku-evaluated gate.

## The progress helper

`bin/update_yolo_progress.py` is invoked by the skill (`C` completed, `R` remaining;
or `--remove`). The `SessionStart` hook symlinks it to a stable path,
`~/.claude/hooks/update-yolo-progress.py`, so the skill body can call it without
knowing the versioned plugin-cache path.

## Install

```bash
claude plugin marketplace add joelpt/joelpt-claude-plugins
claude plugin install yolo@joelpt-claude-plugins
```

Restart Claude Code. Requires read access to the private marketplace repo (`gh auth login`).

## License

MIT. See `LICENSE`.
