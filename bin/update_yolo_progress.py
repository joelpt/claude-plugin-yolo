#!/usr/bin/env python3
"""YOLO session progress and task tracker.

Manages per-session task state in <git-root>/.claude/yolo-progress.json.
Provides durable task tracking that survives context compaction.

Reads CLAUDE_CODE_SESSION_ID from the environment; silently exits 0 if unset.

Global option (must precede subcommand):
  --work-dir DIR   Override git-root detection with an explicit directory.

Usage:
  update_yolo_progress.py [--work-dir D] C R          # legacy c/r pulse (no-task mode)
  update_yolo_progress.py [--work-dir D] --remove     # hard-delete this session's entry

  update_yolo_progress.py [--work-dir D] init [--tasks-json PATH|-]
  update_yolo_progress.py [--work-dir D] task-add --source-id ID --description TEXT
  update_yolo_progress.py [--work-dir D] task-update --tid TID
      [--status not_started|in_progress|completed|aborted]
      [--outcome TEXT] [--commit SHA] [--capture-diff]
  update_yolo_progress.py [--work-dir D] task-list [--format json|table]
  update_yolo_progress.py [--work-dir D] complete [--abort-if-incomplete]
  update_yolo_progress.py [--work-dir D] remove
  update_yolo_progress.py [--work-dir D] prune
      [--cutoff-completed-days N] [--cutoff-inflight-days N]
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Literal, TypedDict, cast

TaskStatus = Literal["not_started", "in_progress", "completed", "aborted"]
SessionStatus = Literal["in_flight", "completed", "aborted"]


class TaskEntry(TypedDict, total=False):
    """One work item tracked within a YOLO session."""

    tid: str
    source_id: str
    description: str
    status: TaskStatus
    started_at: str | None
    completed_at: str | None
    outcome: str | None
    commit: str | None
    lines_added: int | None
    lines_deleted: int | None


class ProgressEntry(TypedDict, total=False):
    """One YOLO session's full state."""

    c: int
    r: int
    percent: int
    updated: str
    session_start: str
    session_end: str | None
    status: SessionStatus
    work_dir: str
    tasks: list[TaskEntry]


ProgressData = dict[str, ProgressEntry]

_SUBCOMMANDS = frozenset(
    {"init", "task-add", "task-update", "task-list", "complete", "remove", "prune"}
)


def git_root() -> Path:
    """Return the git repository root, or cwd if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except subprocess.CalledProcessError:
        return Path.cwd()


def progress_file(work_dir: Path) -> Path:
    """Return the canonical path to this project's yolo-progress.json.

    Args:
        work_dir: The git root or project directory.

    Returns:
        Absolute path to the progress JSON file.
    """
    return work_dir / ".claude" / "yolo-progress.json"


def _now() -> str:
    """Return current UTC time as an ISO-8601 Z string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts_to_epoch(ts: str) -> float:
    """Parse an ISO-8601 Z timestamp and return a Unix epoch float.

    Args:
        ts: Timestamp string in '%Y-%m-%dT%H:%M:%SZ' format.

    Returns:
        Unix timestamp as a float, or 0.0 on parse failure.
    """
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        ).timestamp()
    except ValueError:
        return 0.0


@contextmanager
def _locked(path: Path) -> Generator[None, None, None]:
    """Acquire an exclusive flock on a sibling .lock file for the duration.

    Args:
        path: The data file being protected (not the lock file itself).

    Yields:
        Nothing; the lock is held for the duration of the with-block.
    """
    lock_path = path.parent / (path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def load(path: Path) -> ProgressData:
    """Load and return the progress data, returning {} on any read/parse error.

    Args:
        path: Path to the yolo-progress.json file.

    Returns:
        Parsed progress data, or an empty dict on any error.
    """
    try:
        return cast(ProgressData, json.loads(path.read_text()))
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return {}


def save(path: Path, data: ProgressData) -> None:
    """Atomically write data to path via a unique temp file and rename.

    The temp file is created in the destination directory so the final rename
    is an atomic same-filesystem operation.

    Args:
        path: Destination path.
        data: Progress data to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f"{path.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(data, indent=2))
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _compute_cr(tasks: list[TaskEntry]) -> tuple[int, int, int]:
    """Compute completed count, remaining count, and percent from task array.

    Args:
        tasks: Current task list for a session.

    Returns:
        Tuple of (c, r, percent) where c = completed, r = not_started + in_progress,
        percent is capped at 99 until the session is explicitly marked complete.
    """
    c = sum(1 for t in tasks if t.get("status") == "completed")
    r = sum(
        1 for t in tasks if t.get("status") in ("not_started", "in_progress")
    )
    total = c + r
    pct = min(99, round(c / total * 100)) if total > 0 else 0
    return c, r, pct


def _next_tid(tasks: list[TaskEntry]) -> str:
    """Return the next available synthetic task id ('t1', 't2', ...).

    Args:
        tasks: Existing task list.

    Returns:
        A 't<N>' string one higher than the current maximum, or 't1' if empty.
    """
    existing: list[int] = []
    for t in tasks:
        tid = t.get("tid", "")
        if isinstance(tid, str) and tid.startswith("t"):
            try:
                existing.append(int(tid[1:]))
            except ValueError:
                pass
    return f"t{max(existing, default=0) + 1}"


def _capture_diff(sha: str, work_dir: Path) -> tuple[int, int]:
    """Run git diff --shortstat against the given commit and parse line counts.

    Args:
        sha: Commit SHA to diff against its parent.
        work_dir: Repository root to run git in.

    Returns:
        Tuple of (lines_added, lines_deleted); (0, 0) on any error.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(work_dir), "diff", "--shortstat", f"{sha}^..{sha}"],
            capture_output=True,
            text=True,
            check=True,
        )
        text = result.stdout
        added = 0
        deleted = 0
        m = re.search(r"(\d+) insertion", text)
        if m:
            added = int(m.group(1))
        m = re.search(r"(\d+) deletion", text)
        if m:
            deleted = int(m.group(1))
        return added, deleted
    except subprocess.CalledProcessError:
        return 0, 0


def _get_or_create_entry(data: ProgressData, session_id: str) -> ProgressEntry:
    """Return the existing entry for session_id, creating a bare one if absent.

    Args:
        data: Full progress data dict (mutated in place on creation).
        session_id: Session identifier.

    Returns:
        The session's ProgressEntry (possibly newly created).
    """
    if session_id not in data:
        data[session_id] = cast(ProgressEntry, {
            "c": 0, "r": 0, "percent": 0, "updated": _now(),
            "status": "in_flight", "tasks": [],
        })
    return data[session_id]


def cmd_legacy_pulse(session_id: str, c: int, r: int, work_dir: Path) -> None:
    """Write c/r/percent/updated without touching the task array.

    Used for no-task-mode sessions or backward compat callers.

    Args:
        session_id: Current session.
        c: Items completed.
        r: Items remaining.
        work_dir: Project root.
    """
    total = c + r
    pct = min(99, round(c / total * 100)) if total > 0 else 0
    pfile = progress_file(work_dir)
    with _locked(pfile):
        data = load(pfile)
        entry = _get_or_create_entry(data, session_id)
        if not entry.get("tasks"):
            entry["c"] = c
            entry["r"] = r
            entry["percent"] = pct
        entry["updated"] = _now()
        save(pfile, data)


def cmd_init(
    session_id: str,
    tasks_json_input: str,
    work_dir: Path,
) -> None:
    """Initialize or merge the task list for this session.

    On first call, creates the session entry with the full task list.
    If an in-flight entry already exists, appends tasks not already present
    (matched on source_id). If a terminal-status entry exists, archives it
    under '<sid>#N' and starts fresh.

    Args:
        session_id: Current session.
        tasks_json_input: JSON string: array of {source_id, description}.
        work_dir: Project root.
    """
    raw_tasks = cast(
        list[dict[str, str]], json.loads(tasks_json_input)
    )
    pfile = progress_file(work_dir)
    with _locked(pfile):
        data = load(pfile)
        if session_id in data:
            existing = data[session_id]
            if existing.get("status") in ("completed", "aborted"):
                n = 1
                while f"{session_id}#{n}" in data:
                    n += 1
                data[f"{session_id}#{n}"] = existing
                del data[session_id]
        entry = _get_or_create_entry(data, session_id)
        tasks: list[TaskEntry] = entry.get("tasks") or []
        existing_ids = {t.get("source_id") or "" for t in tasks}
        for raw in raw_tasks:
            raw_sid = raw.get("source_id") or ""
            if raw_sid not in existing_ids:
                tasks.append(cast(TaskEntry, {
                    "tid": _next_tid(tasks),
                    "source_id": raw_sid,
                    "description": raw.get("description", ""),
                    "status": "not_started",
                    "started_at": None,
                    "completed_at": None,
                    "outcome": None,
                    "commit": None,
                    "lines_added": None,
                    "lines_deleted": None,
                }))
                existing_ids.add(raw_sid)
        entry["tasks"] = tasks
        if "session_start" not in entry:
            entry["session_start"] = _now()
        entry["status"] = "in_flight"
        entry["work_dir"] = str(work_dir)
        c, r, pct = _compute_cr(tasks)
        entry["c"] = c
        entry["r"] = r
        entry["percent"] = pct
        entry["updated"] = _now()
        save(pfile, data)


def cmd_task_add(
    session_id: str,
    source_id: str,
    description: str,
    work_dir: Path,
) -> None:
    """Append one newly-discovered task to this session.

    Prints the assigned tid to stdout.

    Args:
        session_id: Current session.
        source_id: Stable identifier ('gh#42', 'todo:...', etc.).
        description: One-liner description.
        work_dir: Project root.
    """
    pfile = progress_file(work_dir)
    with _locked(pfile):
        data = load(pfile)
        entry = _get_or_create_entry(data, session_id)
        tasks: list[TaskEntry] = entry.get("tasks") or []
        tid = _next_tid(tasks)
        tasks.append(cast(TaskEntry, {
            "tid": tid,
            "source_id": source_id,
            "description": description,
            "status": "not_started",
            "started_at": None,
            "completed_at": None,
            "outcome": None,
            "commit": None,
            "lines_added": None,
            "lines_deleted": None,
        }))
        entry["tasks"] = tasks
        c, r, pct = _compute_cr(tasks)
        entry["c"] = c
        entry["r"] = r
        entry["percent"] = pct
        entry["updated"] = _now()
        save(pfile, data)
    print(tid)


def cmd_task_update(
    session_id: str,
    tid: str,
    status: TaskStatus | None,
    outcome: str | None,
    commit_sha: str | None,
    capture_diff: bool,
    work_dir: Path,
) -> None:
    """Update fields on a single task entry, then recompute c/r/percent.

    Args:
        session_id: Current session.
        tid: Task id to update (e.g. 't3').
        status: New status, or None to leave unchanged.
        outcome: Outcome prose, or None to leave unchanged.
        commit_sha: Commit SHA to record, or None to leave unchanged.
        capture_diff: If True and commit_sha is set, run git diff --shortstat.
        work_dir: Project root.
    """
    pfile = progress_file(work_dir)
    with _locked(pfile):
        data = load(pfile)
        if session_id not in data:
            print(f"warning: task-update: session '{session_id}' not found", file=sys.stderr)
            return
        entry = data[session_id]
        tasks: list[TaskEntry] = entry.get("tasks") or []
        now = _now()
        for task in tasks:
            if task.get("tid") == tid:
                if status is not None:
                    prev = task.get("status")
                    task["status"] = status
                    if status == "in_progress" and prev == "not_started":
                        if not task.get("started_at"):
                            task["started_at"] = now
                    elif status in ("completed", "aborted"):
                        if not task.get("started_at"):
                            task["started_at"] = now
                        if not task.get("completed_at"):
                            task["completed_at"] = now
                if outcome is not None:
                    task["outcome"] = outcome
                if commit_sha is not None:
                    task["commit"] = commit_sha
                    if capture_diff:
                        added, deleted = _capture_diff(commit_sha, work_dir)
                        task["lines_added"] = added
                        task["lines_deleted"] = deleted
                break
        else:
            print(f"warning: task-update: tid '{tid}' not found", file=sys.stderr)
            return
        entry["tasks"] = tasks
        c, r, pct = _compute_cr(tasks)
        entry["c"] = c
        entry["r"] = r
        entry["percent"] = pct
        entry["updated"] = now
        save(pfile, data)


def cmd_task_list(session_id: str, fmt: str, work_dir: Path) -> None:
    """Print the current session's tasks to stdout.

    Args:
        session_id: Current session.
        fmt: Output format: 'json' or 'table'.
        work_dir: Project root.
    """
    pfile = progress_file(work_dir)
    data = load(pfile)
    entry = data.get(session_id, cast(ProgressEntry, {}))
    tasks: list[TaskEntry] = entry.get("tasks") or []
    if fmt == "json":
        print(json.dumps(tasks, indent=2))
    else:
        if not tasks:
            print("(no tasks)")
            return
        col_tid = max(3, max(len(t.get("tid", "")) for t in tasks))
        col_status = max(6, max(len(t.get("status", "")) for t in tasks))
        col_src = max(9, max(len(t.get("source_id", "")) for t in tasks))
        col_src = min(col_src, 20)
        header = (
            f"{'tid':<{col_tid}}  {'status':<{col_status}}  "
            f"{'source_id':<{col_src}}  description"
        )
        print(header)
        print("-" * (len(header) + 20))
        for task in tasks:
            src = (task.get("source_id") or "")[:col_src]
            desc = task.get("description") or ""
            desc_short = desc[:60] + ("…" if len(desc) > 60 else "")
            print(
                f"{task.get('tid', ''):<{col_tid}}  "
                f"{task.get('status', ''):<{col_status}}  "
                f"{src:<{col_src}}  {desc_short}"
            )


def cmd_complete(
    session_id: str,
    abort_if_incomplete: bool,
    work_dir: Path,
) -> None:
    """Mark this session as completed or aborted; set session_end.

    Idempotent: if session_end is already set, updates status only if
    the current status is 'in_flight'.

    Args:
        session_id: Current session.
        abort_if_incomplete: If True, set status 'aborted' when any task is
            still in a non-terminal state; otherwise always set 'completed'.
        work_dir: Project root.
    """
    pfile = progress_file(work_dir)
    with _locked(pfile):
        data = load(pfile)
        if session_id not in data:
            return
        entry = data[session_id]
        tasks: list[TaskEntry] = entry.get("tasks") or []
        if abort_if_incomplete:
            incomplete = any(
                t.get("status") in ("not_started", "in_progress") for t in tasks
            )
            new_status: SessionStatus = "aborted" if incomplete else "completed"
        else:
            new_status = "completed"
        if entry.get("status") != "completed":
            entry["status"] = new_status
        if not entry.get("session_end"):
            entry["session_end"] = _now()
        entry["updated"] = _now()
        save(pfile, data)


def cmd_remove(session_id: str, work_dir: Path) -> None:
    """Hard-delete this session's entry; delete the file if it becomes empty.

    Args:
        session_id: The session id to remove.
        work_dir: Project root.
    """
    pfile = progress_file(work_dir)
    with _locked(pfile):
        data = load(pfile)
        data.pop(session_id, None)
        if data:
            save(pfile, data)
        elif pfile.exists():
            pfile.unlink()


def cmd_prune(
    cutoff_completed_days: int,
    cutoff_inflight_days: int,
    work_dir: Path,
) -> None:
    """Remove stale session entries according to retention policy.

    In-flight sessions idle for more than cutoff_inflight_days are marked
    'aborted' with session_end=now, then enter the completed retention window.
    Completed/aborted sessions with session_end older than cutoff_completed_days
    are removed.

    Args:
        cutoff_completed_days: Days to retain sessions after session_end.
        cutoff_inflight_days: Days to keep idle in-flight sessions before aborting.
        work_dir: Project root.
    """
    pfile = progress_file(work_dir)
    if not pfile.exists():
        return

    now_epoch = time.time()
    completed_cutoff = now_epoch - cutoff_completed_days * 86400
    inflight_cutoff = now_epoch - cutoff_inflight_days * 86400

    with _locked(pfile):
        data = load(pfile)
        to_delete: list[str] = []
        for sid, entry in data.items():
            status = entry.get("status", "in_flight")
            session_end = entry.get("session_end")
            updated = entry.get("updated", "1970-01-01T00:00:00Z")

            if "#" not in sid:
                if status == "in_flight" and not session_end:
                    if _ts_to_epoch(updated) < inflight_cutoff:
                        entry["status"] = "aborted"
                        entry["session_end"] = _now()
                        entry["updated"] = _now()
                        session_end = entry["session_end"]
                elif status in ("completed", "aborted") and not session_end:
                    entry["session_end"] = updated
                    session_end = updated

            if session_end and _ts_to_epoch(session_end) < completed_cutoff:
                to_delete.append(sid)

        for sid in to_delete:
            del data[sid]

        if data:
            save(pfile, data)
        elif pfile.exists():
            pfile.unlink()


def _extract_work_dir(argv: list[str]) -> tuple[Path | None, list[str]]:
    """Extract --work-dir from argv before subcommand dispatch.

    Args:
        argv: Raw sys.argv[1:].

    Returns:
        Tuple of (work_dir or None, remaining argv without --work-dir flag).
    """
    work_dir: Path | None = None
    remaining: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--work-dir" and i + 1 < len(argv):
            work_dir = Path(argv[i + 1])
            i += 2
        elif argv[i].startswith("--work-dir="):
            work_dir = Path(argv[i].split("=", 1)[1])
            i += 1
        else:
            remaining.append(argv[i])
            i += 1
    return work_dir, remaining


def main() -> None:
    """Parse args and dispatch to the appropriate command."""
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not session_id:
        sys.exit(0)

    raw_argv = sys.argv[1:]
    work_dir_override, argv = _extract_work_dir(raw_argv)

    def resolve_work_dir() -> Path:
        return work_dir_override if work_dir_override is not None else git_root()

    if argv and argv[0] in _SUBCOMMANDS:
        subcommand = argv[0]
        subargs = argv[1:]

        if subcommand == "init":
            p = argparse.ArgumentParser(prog="update_yolo_progress.py init")
            p.add_argument(
                "--tasks-json",
                default="-",
                metavar="PATH|-",
                help="Path to JSON task array, or '-' for stdin",
            )
            a = p.parse_args(subargs)
            if a.tasks_json == "-":
                tasks_json_input = sys.stdin.read()
            else:
                tasks_json_input = Path(a.tasks_json).read_text()
            cmd_init(session_id, tasks_json_input, resolve_work_dir())

        elif subcommand == "task-add":
            p = argparse.ArgumentParser(prog="update_yolo_progress.py task-add")
            p.add_argument("--source-id", required=True)
            p.add_argument("--description", required=True)
            a = p.parse_args(subargs)
            cmd_task_add(session_id, a.source_id, a.description, resolve_work_dir())

        elif subcommand == "task-update":
            p = argparse.ArgumentParser(prog="update_yolo_progress.py task-update")
            p.add_argument("--tid", required=True)
            p.add_argument(
                "--status",
                choices=["not_started", "in_progress", "completed", "aborted"],
            )
            p.add_argument("--outcome")
            p.add_argument("--commit")
            p.add_argument("--capture-diff", action="store_true")
            a = p.parse_args(subargs)
            cmd_task_update(
                session_id,
                a.tid,
                cast(TaskStatus | None, a.status),
                a.outcome,
                a.commit,
                a.capture_diff,
                resolve_work_dir(),
            )

        elif subcommand == "task-list":
            p = argparse.ArgumentParser(prog="update_yolo_progress.py task-list")
            p.add_argument("--format", choices=["json", "table"], default="json")
            a = p.parse_args(subargs)
            cmd_task_list(session_id, a.format, resolve_work_dir())

        elif subcommand == "complete":
            p = argparse.ArgumentParser(prog="update_yolo_progress.py complete")
            p.add_argument("--abort-if-incomplete", action="store_true")
            a = p.parse_args(subargs)
            cmd_complete(session_id, a.abort_if_incomplete, resolve_work_dir())

        elif subcommand == "remove":
            cmd_remove(session_id, resolve_work_dir())

        elif subcommand == "prune":
            p = argparse.ArgumentParser(prog="update_yolo_progress.py prune")
            p.add_argument("--cutoff-completed-days", type=int, default=14)
            p.add_argument("--cutoff-inflight-days", type=int, default=7)
            a = p.parse_args(subargs)
            cmd_prune(a.cutoff_completed_days, a.cutoff_inflight_days, resolve_work_dir())

    else:
        p = argparse.ArgumentParser(
            description="Update or remove the current session's yolo-progress entry.",
            usage="%(prog)s [--work-dir D] C R | %(prog)s [--work-dir D] --remove",
        )
        p.add_argument(
            "--remove",
            action="store_true",
            help="Hard-delete this session's entry from yolo-progress.json",
        )
        p.add_argument("c", type=int, nargs="?", metavar="C")
        p.add_argument("r", type=int, nargs="?", metavar="R")
        a = p.parse_args(argv)
        if a.remove:
            cmd_remove(session_id, resolve_work_dir())
        elif a.c is not None and a.r is not None:
            cmd_legacy_pulse(session_id, a.c, a.r, resolve_work_dir())
        else:
            p.error("provide C and R, or --remove, or a subcommand")


if __name__ == "__main__":
    main()
