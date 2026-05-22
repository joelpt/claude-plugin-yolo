---
name: yolo
description: Autonomous work mode — work all pending items (GitHub issues, TODO.md, PLAN.md) sequentially without pausing for decisions, committing as you go. Invoke when user says /yolo, "go autonomous", "work everything", or "keep going until done".
---

## Autonomous Mode Instructions

Work pending items sequentially with commits as you go.
Make normal-decision calls without asking.
Surface only genuine human-input items in USER_TODO.md and keep going.

### Work Source Priority

Check sources in this order:

1. **GitHub Issues** — if `gh issue list --state open --limit 1000` returns issues, work them in order set by README.md/PLAN.md phase tables, milestones, or dependency notes; else by `next`/`priority`/milestone-pinned labels; then ascending issue number, skipping blocked.
2. **TODO.md** — top `- [ ]` item.
3. **PLAN.md** then **README.md** roadmap — fall through only if no issues or TODO.md.

Update README.md/PLAN.md in the same atomic change as the underlying work — no doc drift.

### USER_TODO.md

Flag items you cannot safely decide alone:

- Irreversible/destructive ops
- Credentials, secrets, external account setup
- Personal-preference or external-context choices
- Anything that could non-recoverably break something the user cares about

Each entry: `- [ ] [BLOCKING|NON-BLOCKING] short description` plus indented `Context:` and optional `Options:`.

- **BLOCKING** = no meaningful forward progress on related work
- **NON-BLOCKING** = continue with mock/stub/placeholder and a `# USER_TODO: <desc>` marker in code/config

After every commit (always via `/commit-commands:commit`) print one line:

```
∴ USER TODOs: N pending — [item1], [item2], ...
```

or `∴ USER TODOs: 0`.

### Startup Gate

At the very start of a /yolo run, before any other work, read USER_TODO.md at project root.

If it has pending items needing user attention, front-load the interactive interview FIRST:
- Walk every pending item via AskUserQuestion
- Surface relevant Context inline (don't make the user open the file)
- Present item Options plus obvious additional choices as selectable answers
- Mark the single best option with ` (recommended)` as first option with one-line why
- Batch up to 4 items per AskUserQuestion call
- Apply answers, then enter autonomous mode

**Single exception**: if the user explicitly asks to skip USER_TODOs or jump straight into implementing, do NOT run the startup interview — proceed directly into autonomous mode on work not gated on BLOCKING USER_TODOs.

If the user says they're present or asks to go through USER_TODOs, switch to interactive mode and walk every pending USER_TODO via AskUserQuestion, then resume autonomous mode.

### Blocked Work

Note in USER_TODO.md, find parallel work (different feature/platform/docs/mocked tests) and continue there.
Only halt when ALL remaining work is gated on BLOCKING USER_TODOs — then summarize what's blocked and why.
This counts as goal reached.

### Per-Item Loop

0. **Pulse yolo progress**: at the very start of each item iteration, before doing any work, run `python3 ~/.claude/hooks/update-yolo-progress.py C R 2>/dev/null || true` where `C` = items completed so far this session and `R` = estimated items remaining including the current one. Keeps the statusline alive mid-task. C and R may regress (e.g. a reverted change decrements C, a newly discovered issue increments R) — always pass the current counts.
1. Make a judgment call and do the work
2. Verify (tests/output/smoke as applicable)
3. Use `/rca` on second failure, `/think` on complex root causes
4. Route blockers into USER_TODO.md
5. Update the work-tracking source (check off TODO.md / close-or-comment GH issue / update PLAN.md)
6. Commit via `/commit-commands:commit`
6.5. **Write yolo progress**: count items completed this session (`C`) and items visibly remaining (`R`). Run `python3 ~/.claude/hooks/update-yolo-progress.py C R 2>/dev/null || true`.
7. Print the USER TODO count line

Multi-step items: mark `[WIP]` in TODO.md or post a progress comment on the GH issue so the session can resume cleanly.

### Goal State

Every item completable without BLOCKING USER_TODOs is done and marked complete.
README.md/PLAN.md reflect current reality.
USER_TODO.md captures everything waiting on the human.

When goal state is reached:

1. **Remove progress entry**: run `python3 ~/.claude/hooks/update-yolo-progress.py --remove 2>/dev/null || true` to clear the statusline `Y:N%` immediately (the `SessionEnd` hook handles this as a safety net, but do it here for instant feedback).
2. **Print recap** in this format (then explicitly state the goal-state line):

```
/yolo recap:
  Done (N): item-1, item-2, ...   ← list names when ≤6 total items, else "N items (GitHub Issues, TODO.md)"
  Blocked (M): item-4 (reason)    ← omit line entirely if M=0
  Commits: X | Sources: <sources actually used>
```

Explicitly state: **"∴ Goal state reached — all completable work done."**

> The `Stop` completion gate (this plugin's `hooks/yolo-completion-gate.sh`) blocks the
> session from ending until the recent transcript shows `Goal state reached` or a
> `/yolo recap:`. Printing the recap and the goal-state line above is what releases it.
