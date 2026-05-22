"""Tests for update_yolo_progress.py.

Focus: percent math, atomic JSON merge, remove mode, and graceful
degradation when CLAUDE_CODE_SESSION_ID is absent or the progress file
is corrupt/missing.

Run with:  python3 -m unittest discover tests/
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

import update_yolo_progress  # noqa: E402  (sys.path injection)


SESSION = "test-session-abc123"
OTHER_SESSION = "other-session-xyz789"


def _run(tmp_root: Path, *argv: str, session_id: str = SESSION) -> int:
    """Run main() with patched git_root, env, and sys.argv; return sys.exit code."""
    with (
        patch("update_yolo_progress.git_root", return_value=tmp_root),
        patch.dict("os.environ", {"CLAUDE_CODE_SESSION_ID": session_id}),
        patch("sys.argv", ["upd"] + list(argv)),
    ):
        try:
            update_yolo_progress.main()
            return 0
        except SystemExit as e:
            return int(e.code) if e.code is not None else 0


def _load(tmp_root: Path) -> dict[str, object]:
    pfile = tmp_root / ".claude" / "yolo-progress.json"
    if not pfile.exists():
        return {}
    return json.loads(pfile.read_text())  # type: ignore[return-value]


class WriteProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_creates_file_and_entry(self) -> None:
        _run(self.root, "2", "3")
        data = _load(self.root)
        self.assertIn(SESSION, data)
        entry = data[SESSION]
        self.assertEqual(entry["percent"], 40)  # type: ignore[index]
        self.assertRegex(entry["updated"], r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")  # type: ignore[index]

    def test_updates_existing_entry(self) -> None:
        _run(self.root, "1", "4")
        _run(self.root, "2", "3")
        data = _load(self.root)
        self.assertEqual(data[SESSION]["percent"], 40)  # type: ignore[index]

    def test_preserves_other_session_entries(self) -> None:
        _run(self.root, "1", "1", session_id=OTHER_SESSION)
        _run(self.root, "2", "3")
        data = _load(self.root)
        self.assertIn(OTHER_SESSION, data)
        self.assertIn(SESSION, data)

    def test_percent_cap_at_99_when_nothing_remains(self) -> None:
        _run(self.root, "5", "0")
        data = _load(self.root)
        self.assertEqual(data[SESSION]["percent"], 99)  # type: ignore[index]

    def test_percent_zero_when_no_work_counted(self) -> None:
        _run(self.root, "0", "0")
        data = _load(self.root)
        self.assertEqual(data[SESSION]["percent"], 0)  # type: ignore[index]

    def test_percent_rounds_correctly(self) -> None:
        # 1 done of 3 total = 33%
        _run(self.root, "1", "2")
        data = _load(self.root)
        self.assertEqual(data[SESSION]["percent"], 33)  # type: ignore[index]

    def test_corrupt_progress_file_is_overwritten(self) -> None:
        claude_dir = self.root / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "yolo-progress.json").write_text("not valid json{{")
        _run(self.root, "2", "3")
        data = _load(self.root)
        self.assertIn(SESSION, data)


class RemoveProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_remove_deletes_session_entry(self) -> None:
        _run(self.root, "2", "3")
        _run(self.root, "--remove")
        data = _load(self.root)
        self.assertNotIn(SESSION, data)

    def test_remove_deletes_file_when_last_entry(self) -> None:
        _run(self.root, "2", "3")
        _run(self.root, "--remove")
        pfile = self.root / ".claude" / "yolo-progress.json"
        self.assertFalse(pfile.exists())

    def test_remove_preserves_other_session_entries(self) -> None:
        _run(self.root, "1", "1", session_id=OTHER_SESSION)
        _run(self.root, "2", "3")
        _run(self.root, "--remove")
        data = _load(self.root)
        self.assertIn(OTHER_SESSION, data)
        self.assertNotIn(SESSION, data)

    def test_remove_on_missing_file_is_noop(self) -> None:
        rc = _run(self.root, "--remove")
        self.assertEqual(rc, 0)


class NoSessionIdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_no_session_id_exits_zero_without_writing(self) -> None:
        with (
            patch("update_yolo_progress.git_root", return_value=self.root),
            patch.dict("os.environ", {}, clear=True),
            patch("sys.argv", ["upd", "2", "3"]),
        ):
            with self.assertRaises(SystemExit) as cm:
                update_yolo_progress.main()
            self.assertEqual(cm.exception.code, 0)
        self.assertEqual(_load(self.root), {})


if __name__ == "__main__":
    unittest.main()
