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

```text
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

### Session Initialization

Once the startup gate clears, before entering the per-item loop, initialize durable task tracking:

1. Enumerate every pending work item from the prioritized sources into a JSON array.
   Each item: `{"source_id": "<gh#N|todo:First line|plan:Phase N|description>", "description": "<one-liner>"}`.
2. Initialize (or merge) the task list:

   ```bash
   printf '%s' '[{"source_id":"gh#1","description":"..."},...]' | \
     python3 ~/.claude/hooks/update-yolo-progress.py \
       --work-dir "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" \
       init 2>/dev/null || true
   ```

3. Read the assigned tids so you can reference them throughout:

   ```bash
   python3 ~/.claude/hooks/update-yolo-progress.py task-list --format json 2>/dev/null || echo '[]'
   ```

   Store the mapping of `source_id → tid` in working memory for the session.

If new work is discovered mid-session, register it immediately:

```bash
python3 ~/.claude/hooks/update-yolo-progress.py \
  task-add --source-id "gh#42" --description "one-liner" 2>/dev/null || true
```

This prints the new tid to stdout — capture it. Also call `task-update --tid <new-tid> --status aborted --outcome "Deferred: <reason>"` immediately for any item you know won't be worked this session (blocked on user input, depends on unfinished prerequisite, etc.).

### Blocked Work

Note in USER_TODO.md, find parallel work (different feature/platform/docs/mocked tests) and continue there.
Only halt when ALL remaining work is gated on BLOCKING USER_TODOs — then summarize what's blocked and why.
This counts as goal reached.

### Per-Item Loop

For each task (referenced by its `tid`):

0. **Mark in-progress**:

   ```bash
   python3 ~/.claude/hooks/update-yolo-progress.py \
     task-update --tid "$TID" --status in_progress 2>/dev/null || true
   ```

1. Make a judgment call and do the work.
2. Verify (tests/output/smoke as applicable).
3. Use `/rca` on second failure, `/think` on complex root causes.
4. Route blockers into USER_TODO.md. If a task cannot be completed, mark it aborted now:

   ```bash
   python3 ~/.claude/hooks/update-yolo-progress.py \
     task-update --tid "$TID" --status aborted \
     --outcome "Blocked: <reason>" 2>/dev/null || true
   ```

5. Update the work-tracking source (check off TODO.md / close-or-comment GH issue / update PLAN.md).
6. Commit via `/commit-commands:commit`.
6.5. **Persist task outcome** (immediately after commit):

   ```bash
   python3 ~/.claude/hooks/update-yolo-progress.py \
     task-update --tid "$TID" \
     --status completed \
     --commit "$(git rev-parse --short HEAD 2>/dev/null || echo '')" \
     --outcome "<one or two sentence outcome, including any notable follow-ups>" \
     --capture-diff 2>/dev/null || true
   ```

7. Print the USER TODO count line.

Multi-step items: mark `[WIP]` in TODO.md or post a progress comment on the GH issue; leave the task `in_progress` in the JSON until the final commit for that item.

### PreCompact Correctness Sweep

When the PreCompact hook fires, a directive is preserved in the compaction summary. After compaction completes, you will see instructions to reconcile task state. **Do this immediately before resuming work**:

1. Run `python3 ~/.claude/hooks/update-yolo-progress.py task-list --format json` and read the current state.
2. For each task whose JSON `status` is `in_progress` or `not_started` but which you know from the compacted context is actually `completed` or `aborted`, call `task-update` to correct it (with `--commit` and `--capture-diff` if applicable).
3. For any work referenced in the summary that is not in the JSON, call `task-add` then `task-update` to register it.

This is the mechanism by which long sessions retain accurate history across compactions. Do not skip it.

### Goal State

Every item completable without BLOCKING USER_TODOs is done and marked complete.
README.md/PLAN.md reflect current reality.
USER_TODO.md captures everything waiting on the human.

When goal state is reached:

1. **Mark the session complete**:

   ```bash
   python3 ~/.claude/hooks/update-yolo-progress.py complete 2>/dev/null || true
   ```

   The entry is retained for 14 days. The statusline `Y:c/r` cell clears once `session_end` is set.

2. **Build the recap from the JSON**:

   ```bash
   python3 ~/.claude/hooks/update-yolo-progress.py task-list --format json
   ```

   Use that as the authoritative source — not memory. Open with a bold `/yolo recap:` line, then labeled bullets:

   - **Done** — name each completed item (use `description` field) when there are ≤6; otherwise give a count plus a one-line characterization. Include commit sha where recorded.
   - **Verified** — how completion was checked (tests, smoke runs, builds); call out anything left unverified.
   - **Blocked** — items with `status: "aborted"` and their `outcome` prose. Omit bullet if none.
   - **Surfaced** — new USER_TODO.md entries added this session. Omit if none.
   - **Commits & sources** — count of distinct commits recorded, which sources were used.
   - **Session duration** — derive from `session_start` to `session_end` if available.
   - **Follow-ups** — anything the user should know or do next. Omit if none.

3. If lines_added/lines_deleted are recorded for any task, include a total at the end of the recap:
   `Lines changed: +N / -M across N tasks`.

Then explicitly state: **"∴ Goal state reached — all completable work done."**

> The `Stop` completion gate (this plugin's `hooks/yolo-completion-gate.sh`) blocks the
> session from ending until the recent transcript shows `Goal state reached` or a
> `/yolo recap:`. Printing the recap and the goal-state line above is what releases it.
