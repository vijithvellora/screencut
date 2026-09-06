import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from screencut.models import EditSession, TrimRange, validate_session
from screencut.projects import SessionHistory, load_project, save_project


class ProjectTests(unittest.TestCase):
    def session(self):
        session = EditSession(duration=10, width=1920, height=1080)
        session.add_blur(0.1, 0.2, 0.3, 0.4, 1, 9, mode="redact")
        return session

    def test_round_trip_relative_media_and_next_identifier(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.session()
            session.video_path = str((Path(directory) / "media" / "clip.mp4").resolve())
            path = Path(directory) / "edit.screencut"
            save_project(path, session)
            self.assertEqual(
                json.loads(path.read_text())["session"]["video_path"], "media/clip.mp4"
            )
            loaded = load_project(path)
            self.assertEqual(loaded, session)
            self.assertEqual(loaded.add_blur(0, 0, 1, 1, 0, 10).id, 1)

    def test_atomic_failure_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "edit.screencut"
            path.write_text("original")
            with patch("screencut.projects.os.replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    save_project(path, self.session())
            self.assertEqual(path.read_text(), "original")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_rejects_versions_and_malformed_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "edit.screencut"
            for payload in ("{", "[]", '{"version": 2}', '{"version": 1, "session": null}'):
                path.write_text(payload)
                with self.assertRaises(ValueError):
                    load_project(path)

    def test_rejects_nonfinite_speed_times_and_coordinates(self):
        for field in ("speed", "duration", "fps"):
            for value in (float("nan"), float("inf"), True):
                session = self.session()
                setattr(session, field, value)
                with self.assertRaises(ValueError):
                    validate_session(session)
        for field in ("x", "y", "w", "h", "start_time", "end_time"):
            session = self.session()
            setattr(session.blur_regions[0], field, float("nan"))
            with self.assertRaises(ValueError):
                validate_session(session)

    def test_invalid_ranges_do_not_silently_drop_masks(self):
        session = self.session()
        session.blur_regions[0].end_time = 0.5
        with self.assertRaisesRegex(ValueError, "Region start"):
            validate_session(session)
        session = self.session()
        session.trim = TrimRange(8, 3)
        with self.assertRaisesRegex(ValueError, "Trim start"):
            validate_session(session)

    def test_clamps_bounds_without_mutating_original(self):
        session = self.session()
        session.blur_regions[0].x = -0.1
        session.blur_regions[0].end_time = 11
        normalized = validate_session(session)
        self.assertEqual(normalized.blur_regions[0].x, 0)
        self.assertAlmostEqual(normalized.blur_regions[0].w, 0.2)
        self.assertEqual(normalized.blur_regions[0].end_time, 10)
        self.assertEqual(session.blur_regions[0].x, -0.1)

    def test_history_snapshots_branching_and_bound(self):
        session = self.session()
        history = SessionHistory(session, limit=3)
        self.assertFalse(history.record(session))
        for speed in (2, 3, 4):
            session.speed = speed
            history.record(session)
        self.assertEqual(history.undo().speed, 3)
        restored = history.undo()
        self.assertEqual(restored.speed, 2)
        restored.speed = 7
        self.assertIsNone(history.undo())
        self.assertEqual(history.redo().speed, 3)
        history.record(restored)
        self.assertFalse(history.can_redo)
        self.assertEqual(history.undo().speed, 3)

    def test_trim_speed_duration(self):
        session = self.session()
        session.trim = TrimRange(2, 8)
        session.speed = 2
        self.assertEqual(session.effective_duration(), 3)


if __name__ == "__main__":
    unittest.main()
