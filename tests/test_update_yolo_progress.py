"""Tests for update_yolo_progress.py.

Covers: legacy c/r pulse, task CRUD, session complete/prune, locking
invariants, and graceful degradation when CLAUDE_CODE_SESSION_ID is absent.

Run with:  python3 -m unittest discover tests/
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

import update_yolo_progress  # noqa: E402  # type: ignore[import]  (sys.path injection)

SESSION = "test-session-abc123"
OTHER_SESSION = "other-session-xyz789"


def _pfile(tmp_root: Path) -> Path:
    """Return the fake global progress file path under tmp_root.

    Args:
        tmp_root: Temporary directory used as a stand-in for ~/.claude's parent.

    Returns:
        Path to tmp_root/.claude/yolo-progress.json.
    """
    return tmp_root / ".claude" / "yolo-progress.json"


def _run(tmp_root: Path, *argv: str, session_id: str = SESSION) -> int:
    """Run main() with patched progress_file, env, and sys.argv; return exit code.

    Args:
        tmp_root: Temporary directory; progress_file() is patched to write here.
        *argv: Command-line arguments after the program name.
        session_id: Value to inject as CLAUDE_CODE_SESSION_ID.

    Returns:
        The process exit code (0 on success).
    """
    with (
        patch(
            "update_yolo_progress.progress_file",
            return_value=_pfile(tmp_root),
        ),
        patch.dict("os.environ", {"CLAUDE_CODE_SESSION_ID": session_id}),
        patch("sys.argv", ["upd"] + list(argv)),
    ):
        try:
            update_yolo_progress.main()
            return 0
        except SystemExit as e:
            return int(e.code) if e.code is not None else 0


def _load(tmp_root: Path) -> dict[str, Any]:
    """Load the yolo-progress.json from tmp_root/.claude/.

    Args:
        tmp_root: Temporary directory holding the fake progress file.

    Returns:
        Parsed JSON dict, or {} if the file is absent.
    """
    pfile = _pfile(tmp_root)
    if not pfile.exists():
        return {}
    return cast_dict(json.loads(pfile.read_text()))


def cast_dict(obj: Any) -> dict[str, Any]:
    """Return obj cast to dict[str, Any] for the type checker.

    Args:
        obj: Value from json.loads.

    Returns:
        The same object, typed as dict[str, Any].
    """
    return obj  # type: ignore[return-value]


def _tasks_json(items: list[dict[str, str]]) -> str:
    """Serialize a list of {source_id, description} items to JSON.

    Args:
        items: Task descriptors to serialize.

    Returns:
        JSON string.
    """
    return json.dumps(items)


class LegacyPulseTests(unittest.TestCase):
    """Tests for the legacy C R positional form."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_creates_file_and_entry(self) -> None:
        _run(self.root, "2", "3")
        data = _load(self.root)
        self.assertIn(SESSION, data)
        entry = data[SESSION]
        self.assertEqual(entry["percent"], 40)
        self.assertRegex(entry["updated"], r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

    def test_updates_existing_entry(self) -> None:
        _run(self.root, "1", "4")
        _run(self.root, "2", "3")
        data = _load(self.root)
        self.assertEqual(data[SESSION]["percent"], 40)

    def test_preserves_other_session_entries(self) -> None:
        _run(self.root, "1", "1", session_id=OTHER_SESSION)
        _run(self.root, "2", "3")
        data = _load(self.root)
        self.assertIn(OTHER_SESSION, data)
        self.assertIn(SESSION, data)

    def test_percent_100_when_nothing_remains(self) -> None:
        _run(self.root, "5", "0")
        data = _load(self.root)
        self.assertEqual(data[SESSION]["percent"], 100)

    def test_percent_zero_when_no_work_counted(self) -> None:
        _run(self.root, "0", "0")
        data = _load(self.root)
        self.assertEqual(data[SESSION]["percent"], 0)

    def test_percent_rounds_correctly(self) -> None:
        _run(self.root, "1", "2")
        data = _load(self.root)
        self.assertEqual(data[SESSION]["percent"], 33)

    def test_corrupt_file_is_overwritten(self) -> None:
        claude_dir = self.root / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "yolo-progress.json").write_text("not valid json{{")
        _run(self.root, "2", "3")
        data = _load(self.root)
        self.assertIn(SESSION, data)

    def test_legacy_pulse_does_not_overwrite_task_cr(self) -> None:
        tasks = [{"source_id": "gh#1", "description": "thing"}]
        with (
            patch(
                "update_yolo_progress.progress_file",
                return_value=_pfile(self.root),
            ),
            patch.dict("os.environ", {"CLAUDE_CODE_SESSION_ID": SESSION}),
            patch("sys.argv", ["upd", "init"]),
            patch("sys.stdin") as mock_stdin,
        ):
            mock_stdin.read.return_value = _tasks_json(tasks)
            update_yolo_progress.main()
        _run(self.root, "task-update", "--tid", "t1", "--status", "completed")
        before = _load(self.root)[SESSION]["c"]
        _run(self.root, "99", "0")
        after = _load(self.root)[SESSION]["c"]
        self.assertEqual(before, after, "legacy pulse must not overwrite tasks-derived c")


class RemoveTests(unittest.TestCase):
    """Tests for --remove (legacy flag) and 'remove' subcommand."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_flag_remove_deletes_session_entry(self) -> None:
        _run(self.root, "2", "3")
        _run(self.root, "--remove")
        self.assertNotIn(SESSION, _load(self.root))

    def test_flag_remove_deletes_file_when_last_entry(self) -> None:
        _run(self.root, "2", "3")
        _run(self.root, "--remove")
        self.assertFalse(_pfile(self.root).exists())

    def test_flag_remove_preserves_other_sessions(self) -> None:
        _run(self.root, "1", "1", session_id=OTHER_SESSION)
        _run(self.root, "2", "3")
        _run(self.root, "--remove")
        data = _load(self.root)
        self.assertIn(OTHER_SESSION, data)
        self.assertNotIn(SESSION, data)

    def test_flag_remove_on_missing_file_is_noop(self) -> None:
        self.assertEqual(_run(self.root, "--remove"), 0)

    def test_subcommand_remove_deletes_session(self) -> None:
        _run(self.root, "2", "3")
        _run(self.root, "remove")
        self.assertNotIn(SESSION, _load(self.root))


class NoSessionIdTests(unittest.TestCase):
    """Tests for missing CLAUDE_CODE_SESSION_ID."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_no_session_id_exits_zero_without_writing(self) -> None:
        with (
            patch(
                "update_yolo_progress.progress_file",
                return_value=_pfile(self.root),
            ),
            patch.dict("os.environ", {}, clear=True),
            patch("sys.argv", ["upd", "2", "3"]),
        ):
            with self.assertRaises(SystemExit) as cm:
                update_yolo_progress.main()
            self.assertEqual(cm.exception.code, 0)
        self.assertEqual(_load(self.root), {})


class InitTests(unittest.TestCase):
    """Tests for the 'init' subcommand."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _init(self, tasks: list[dict[str, str]]) -> None:
        with (
            patch(
                "update_yolo_progress.progress_file",
                return_value=_pfile(self.root),
            ),
            patch.dict("os.environ", {"CLAUDE_CODE_SESSION_ID": SESSION}),
            patch("sys.argv", ["upd", "init"]),
            patch("sys.stdin") as mock_stdin,
        ):
            mock_stdin.read.return_value = _tasks_json(tasks)
            update_yolo_progress.main()

    def test_creates_task_entries(self) -> None:
        self._init([
            {"source_id": "gh#1", "description": "Fix auth"},
            {"source_id": "gh#2", "description": "Add tests"},
        ])
        data = _load(self.root)
        tasks = data[SESSION]["tasks"]
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["tid"], "t1")
        self.assertEqual(tasks[1]["tid"], "t2")
        self.assertEqual(tasks[0]["status"], "not_started")

    def test_sets_session_start_and_status(self) -> None:
        self._init([{"source_id": "gh#1", "description": "x"}])
        entry = _load(self.root)[SESSION]
        self.assertIn("session_start", entry)
        self.assertEqual(entry["status"], "in_flight")

    def test_init_twice_merges_without_duplication(self) -> None:
        self._init([{"source_id": "gh#1", "description": "A"}])
        self._init([
            {"source_id": "gh#1", "description": "A"},
            {"source_id": "gh#2", "description": "B"},
        ])
        data = _load(self.root)
        self.assertEqual(len(data[SESSION]["tasks"]), 2)

    def test_init_on_completed_session_archives_it(self) -> None:
        self._init([{"source_id": "gh#1", "description": "x"}])
        _run(self.root, "complete")
        self._init([{"source_id": "gh#2", "description": "y"}])
        data = _load(self.root)
        self.assertIn(f"{SESSION}#1", data)
        self.assertEqual(data[SESSION]["tasks"][0]["source_id"], "gh#2")


class TaskAddTests(unittest.TestCase):
    """Tests for the 'task-add' subcommand."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_adds_task_and_prints_tid(self) -> None:
        import io
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            _run(self.root, "task-add", "--source-id", "gh#5", "--description", "Foo")
        tid = buf.getvalue().strip()
        self.assertTrue(tid.startswith("t"))
        data = _load(self.root)
        tasks = data[SESSION]["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["source_id"], "gh#5")

    def test_second_add_increments_tid(self) -> None:
        _run(self.root, "task-add", "--source-id", "gh#1", "--description", "A")
        _run(self.root, "task-add", "--source-id", "gh#2", "--description", "B")
        tasks = _load(self.root)[SESSION]["tasks"]
        self.assertEqual(tasks[0]["tid"], "t1")
        self.assertEqual(tasks[1]["tid"], "t2")


class TaskUpdateTests(unittest.TestCase):
    """Tests for the 'task-update' subcommand."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        _run(self.root, "task-add", "--source-id", "gh#1", "--description", "A")
        _run(self.root, "task-add", "--source-id", "gh#2", "--description", "B")

    def test_status_transition_in_progress_sets_started_at(self) -> None:
        _run(self.root, "task-update", "--tid", "t1", "--status", "in_progress")
        task = _load(self.root)[SESSION]["tasks"][0]
        self.assertEqual(task["status"], "in_progress")
        self.assertIsNotNone(task.get("started_at"))

    def test_status_completed_sets_completed_at_and_cr(self) -> None:
        _run(self.root, "task-update", "--tid", "t1", "--status", "completed")
        data = _load(self.root)[SESSION]
        self.assertEqual(data["c"], 1)
        self.assertEqual(data["r"], 2)  # r = total tasks (2), not remaining
        task = data["tasks"][0]
        self.assertIsNotNone(task.get("completed_at"))

    def test_outcome_stored(self) -> None:
        _run(self.root, "task-update", "--tid", "t1", "--outcome", "Done fine")
        task = _load(self.root)[SESSION]["tasks"][0]
        self.assertEqual(task["outcome"], "Done fine")

    def test_commit_stored(self) -> None:
        _run(self.root, "task-update", "--tid", "t1", "--commit", "abc1234")
        task = _load(self.root)[SESSION]["tasks"][0]
        self.assertEqual(task["commit"], "abc1234")

    def test_capture_diff_calls_git(self) -> None:
        with patch(
            "update_yolo_progress._capture_diff", return_value=(10, 3)
        ) as mock_diff:
            _run(
                self.root,
                "task-update",
                "--tid",
                "t1",
                "--commit",
                "abc1234",
                "--capture-diff",
            )
        mock_diff.assert_called_once()
        task = _load(self.root)[SESSION]["tasks"][0]
        self.assertEqual(task["lines_added"], 10)
        self.assertEqual(task["lines_deleted"], 3)

    def test_aborted_task_does_not_change_r(self) -> None:
        _run(self.root, "task-update", "--tid", "t1", "--status", "aborted")
        data = _load(self.root)[SESSION]
        self.assertEqual(data["r"], 2)  # total stays 2; aborted still counts
        self.assertEqual(data["c"], 0)


class TaskListTests(unittest.TestCase):
    """Tests for the 'task-list' subcommand."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        _run(self.root, "task-add", "--source-id", "gh#1", "--description", "A")

    def test_json_format_returns_array(self) -> None:
        import io
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            _run(self.root, "task-list", "--format", "json")
        tasks = json.loads(buf.getvalue())
        self.assertIsInstance(tasks, list)
        self.assertEqual(len(tasks), 1)

    def test_table_format_prints_header(self) -> None:
        import io
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            _run(self.root, "task-list", "--format", "table")
        self.assertIn("tid", buf.getvalue())

    def test_empty_session_returns_empty_array(self) -> None:
        import io
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            _run(self.root, "task-list", "--format", "json", session_id="unknown-sid")
        tasks = json.loads(buf.getvalue())
        self.assertEqual(tasks, [])


class CompleteTests(unittest.TestCase):
    """Tests for the 'complete' subcommand."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        _run(self.root, "task-add", "--source-id", "gh#1", "--description", "A")
        _run(self.root, "task-add", "--source-id", "gh#2", "--description", "B")

    def test_complete_sets_session_end_and_status(self) -> None:
        _run(self.root, "complete")
        entry = _load(self.root)[SESSION]
        self.assertIsNotNone(entry.get("session_end"))
        self.assertEqual(entry["status"], "completed")

    def test_complete_abort_if_incomplete_sets_aborted(self) -> None:
        _run(self.root, "complete", "--abort-if-incomplete")
        entry = _load(self.root)[SESSION]
        self.assertEqual(entry["status"], "aborted")

    def test_complete_abort_if_incomplete_marks_completed_when_all_done(self) -> None:
        _run(self.root, "task-update", "--tid", "t1", "--status", "completed")
        _run(self.root, "task-update", "--tid", "t2", "--status", "completed")
        _run(self.root, "complete", "--abort-if-incomplete")
        entry = _load(self.root)[SESSION]
        self.assertEqual(entry["status"], "completed")

    def test_complete_does_not_flip_aborted_to_completed(self) -> None:
        """Verify aborted status is terminal and cannot be overwritten by complete."""
        _run(self.root, "complete", "--abort-if-incomplete")
        self.assertEqual(_load(self.root)[SESSION]["status"], "aborted")
        _run(self.root, "complete")
        self.assertEqual(_load(self.root)[SESSION]["status"], "aborted")

    def test_complete_is_idempotent(self) -> None:
        _run(self.root, "complete")
        first_end = _load(self.root)[SESSION]["session_end"]
        _run(self.root, "complete")
        second_end = _load(self.root)[SESSION]["session_end"]
        self.assertEqual(first_end, second_end)

    def test_complete_does_not_delete_entry(self) -> None:
        _run(self.root, "complete")
        self.assertIn(SESSION, _load(self.root))

    def test_complete_cr_shows_done_state(self) -> None:
        _run(self.root, "task-update", "--tid", "t1", "--status", "completed")
        _run(self.root, "task-update", "--tid", "t2", "--status", "completed")
        _run(self.root, "complete")
        entry = _load(self.root)[SESSION]
        self.assertEqual(entry["c"], 2)
        self.assertEqual(entry["r"], 2)  # 2/2 visible after completion, not blanked out
        self.assertEqual(entry["percent"], 100)


class PruneTests(unittest.TestCase):
    """Tests for the 'prune' subcommand."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _write_entry(
        self,
        sid: str,
        status: str,
        session_end: str | None = None,
        updated: str = "2020-01-01T00:00:00Z",
    ) -> None:
        pfile = _pfile(self.root)
        pfile.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, object] = {}
        if pfile.exists():
            data = json.loads(pfile.read_text())
        data[sid] = {
            "c": 0, "r": 0, "percent": 0,
            "updated": updated,
            "status": status,
            "session_end": session_end,
            "tasks": [],
        }
        pfile.write_text(json.dumps(data))

    def test_prune_removes_old_completed_sessions(self) -> None:
        self._write_entry(
            SESSION,
            "completed",
            session_end="2020-01-01T00:00:00Z",
        )
        _run(self.root, "prune", "--cutoff-completed-days", "1", "--cutoff-inflight-days", "1")
        self.assertNotIn(SESSION, _load(self.root))

    def test_prune_keeps_recent_completed_sessions(self) -> None:
        from update_yolo_progress import _now  # type: ignore[import]
        self._write_entry(SESSION, "completed", session_end=_now())
        _run(self.root, "prune", "--cutoff-completed-days", "14", "--cutoff-inflight-days", "7")
        self.assertIn(SESSION, _load(self.root))

    def test_prune_marks_stale_inflight_as_aborted(self) -> None:
        self._write_entry(SESSION, "in_flight", updated="2020-01-01T00:00:00Z")
        _run(self.root, "prune", "--cutoff-completed-days", "14", "--cutoff-inflight-days", "1")
        data = _load(self.root)
        self.assertIn(SESSION, data)
        self.assertEqual(data[SESSION]["status"], "aborted")

    def test_prune_keeps_active_inflight_sessions(self) -> None:
        from update_yolo_progress import _now  # type: ignore[import]
        self._write_entry(SESSION, "in_flight", updated=_now())
        _run(self.root, "prune", "--cutoff-completed-days", "14", "--cutoff-inflight-days", "7")
        self.assertIn(SESSION, _load(self.root))

    def test_prune_removes_old_archived_entries(self) -> None:
        self._write_entry(
            f"{SESSION}#1",
            "completed",
            session_end="2020-01-01T00:00:00Z",
        )
        _run(self.root, "prune", "--cutoff-completed-days", "1", "--cutoff-inflight-days", "1")
        self.assertNotIn(f"{SESSION}#1", _load(self.root))

    def test_prune_keeps_recent_archived_entries(self) -> None:
        from update_yolo_progress import _now  # type: ignore[import]
        self._write_entry(
            f"{SESSION}#1",
            "completed",
            session_end=_now(),
        )
        _run(self.root, "prune", "--cutoff-completed-days", "14", "--cutoff-inflight-days", "7")
        self.assertIn(f"{SESSION}#1", _load(self.root))


class GlobalProgressFileTests(unittest.TestCase):
    """Verify that progress_file() always returns the global ~/.claude path."""

    def test_progress_file_returns_home_dot_claude(self) -> None:
        expected = Path.home() / ".claude" / "yolo-progress.json"
        self.assertEqual(update_yolo_progress.progress_file(), expected)

    def test_extract_work_dir_space_form(self) -> None:
        wd, rest = update_yolo_progress._extract_work_dir(
            ["--work-dir", "/some/path", "task-add", "--source-id", "gh#1"]
        )
        self.assertEqual(wd, Path("/some/path"))
        self.assertEqual(rest, ["task-add", "--source-id", "gh#1"])

    def test_extract_work_dir_equals_form(self) -> None:
        wd, rest = update_yolo_progress._extract_work_dir(
            ["--work-dir=/other/path", "init"]
        )
        self.assertEqual(wd, Path("/other/path"))
        self.assertEqual(rest, ["init"])

    def test_extract_work_dir_absent(self) -> None:
        wd, rest = update_yolo_progress._extract_work_dir(["2", "3"])
        self.assertIsNone(wd)
        self.assertEqual(rest, ["2", "3"])


class LoadCorruptionTests(unittest.TestCase):
    """Verify that load() preserves corrupt files instead of silently wiping them."""

    def test_corrupt_file_is_renamed_not_silently_wiped(self) -> None:
        """Confirm JSONDecodeError renames the bad file to a .corrupt-* sibling."""
        with TemporaryDirectory() as tmp:
            pfile = Path(tmp) / ".claude" / "yolo-progress.json"
            pfile.parent.mkdir(parents=True)
            pfile.write_text("{bad json")

            result = update_yolo_progress.load(pfile)

            self.assertEqual(result, {})
            self.assertFalse(pfile.exists(), "corrupt original should be renamed away")
            siblings = list(pfile.parent.glob("yolo-progress.json.corrupt-*"))
            self.assertEqual(len(siblings), 1, "exactly one .corrupt-* file should exist")

    def test_missing_file_returns_empty_dict(self) -> None:
        """Confirm that a missing file returns {} without raising."""
        with TemporaryDirectory() as tmp:
            pfile = Path(tmp) / ".claude" / "yolo-progress.json"
            self.assertEqual(update_yolo_progress.load(pfile), {})


if __name__ == "__main__":
    unittest.main()
