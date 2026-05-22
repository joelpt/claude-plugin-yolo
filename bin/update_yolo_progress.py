#!/usr/bin/env python3
"""Update (or remove) the current session's yolo-progress entry.

Called by the /yolo skill after each commit and at the start of each item loop.
Reads CLAUDE_CODE_SESSION_ID from the environment; silently exits 0 if unset.

Usage:
  update_yolo_progress.py C R       # write/update progress (C completed, R remaining)
  update_yolo_progress.py --remove  # delete this session's entry
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast


class ProgressEntry(TypedDict):
    """A single yolo session's progress snapshot."""

    percent: int
    updated: str


ProgressData = dict[str, ProgressEntry]


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


def progress_file() -> Path:
    """Return the canonical path to this project's yolo-progress.json."""
    return git_root() / ".claude" / "yolo-progress.json"


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

    The temp file is created per call in the destination directory — a fixed
    name would let concurrent yolo sessions in the same repo clobber each
    other's in-flight writes, and a temp file on a different filesystem would
    make the final rename non-atomic.

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
            handle.write(json.dumps(data))
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_progress(session_id: str, c: int, r: int) -> None:
    """Write the percent/updated entry for the given session.

    Args:
        session_id: The CLAUDE_CODE_SESSION_ID value.
        c: Items completed this session.
        r: Items visibly remaining (including any in-progress item).
    """
    total = c + r
    pct = min(99, round(c / total * 100)) if total > 0 else 0
    pfile = progress_file()
    data = load(pfile)
    data[session_id] = {
        "percent": pct,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    save(pfile, data)


def remove_progress(session_id: str) -> None:
    """Remove the given session's entry; delete the file if it becomes empty.

    Args:
        session_id: The CLAUDE_CODE_SESSION_ID to remove.
    """
    pfile = progress_file()
    data = load(pfile)
    data.pop(session_id, None)
    if data:
        save(pfile, data)
    elif pfile.exists():
        pfile.unlink()


def main() -> None:
    """Parse args and dispatch to write or remove mode."""
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not session_id:
        sys.exit(0)

    parser = argparse.ArgumentParser(
        description="Update or remove the current session's yolo-progress entry.",
        usage="%(prog)s C R | %(prog)s --remove",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove this session's entry from yolo-progress.json",
    )
    parser.add_argument(
        "c",
        type=int,
        nargs="?",
        metavar="C",
        help="Items completed this session",
    )
    parser.add_argument(
        "r",
        type=int,
        nargs="?",
        metavar="R",
        help="Items visibly remaining",
    )
    args = parser.parse_args()

    if args.remove:
        remove_progress(session_id)
    elif args.c is not None and args.r is not None:
        write_progress(session_id, args.c, args.r)
    else:
        parser.error("provide C and R, or --remove")


if __name__ == "__main__":
    main()
