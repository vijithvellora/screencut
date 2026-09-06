"""Controller regressions with a real offscreen Qt application."""

import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage, QColor
from editor import ScreenCut
from screencut.models import EditSession, TrimRange
from screencut.media import ExportWorker


class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = ScreenCut()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window._saved_session = copy.deepcopy(self.window.session)
        self.window.close()
        self.app.processEvents()

    def loaded_session(self):
        self.window.session = EditSession("/tmp/example.mp4", 10, 640, 360, 30, TrimRange(0, 10))
        self.window.history.reset(self.window.session)
        self.window._saved_session = copy.deepcopy(self.window.session)
        self.window._sync_session()

    def test_undo_redo_trim_speed_and_region(self):
        self.loaded_session()
        self.window._set_speed(2)
        self.window._on_blur_drawn(0.1, 0.1, 0.3, 0.3)
        self.window._undo()
        self.assertEqual(len(self.window.session.blur_regions), 0)
        self.window._undo()
        self.assertEqual(self.window.session.speed, 1)
        self.window._redo()
        self.assertEqual(self.window.session.speed, 2)
        self.window._on_trim_changed(1, 9)
        self.assertEqual(self.window.session.effective_duration(), 4)

    def test_stale_thumbnail_and_probe_results_ignored(self):
        self.loaded_session()
        self.window._generation = 3
        self.window._on_thumbnails([(0, QImage(10, 10, QImage.Format.Format_RGB32))], 2)
        self.assertEqual(self.window.timeline.thumbnails, [])
        self.window._finish_load("wrong.mp4", {}, 2, None, None)
        self.assertEqual(self.window.session.video_path, "/tmp/example.mp4")

    def test_loaded_video_clears_frame_and_edit_state(self):
        self.loaded_session()
        self.window._on_blur_drawn(0.1, 0.1, 0.3, 0.3)
        frame = QImage(20, 20, QImage.Format.Format_RGB32)
        frame.fill(QColor("red"))
        self.window._on_frame(frame)
        self.window._generation = 2
        with patch("editor.ThumbnailWorker.start"), patch.object(self.window.preview, "open"):
            self.window._finish_load(
                "/tmp/new.mp4",
                dict(duration=5, width=320, height=180, fps=24, has_audio=False),
                2,
                None,
                None,
            )
        self.assertIsNone(self.window.canvas.frame_pixmap)
        self.assertEqual(self.window.session.blur_regions, [])
        self.assertIsNone(self.window.canvas.selected_blur_id)
        self.assertFalse(self.window.history.can_undo)
        self.assertEqual(self.window.timeline.duration, 5)

    def test_region_timing_cannot_be_reversed(self):
        self.loaded_session()
        self.window._on_blur_drawn(0.1, 0.1, 0.3, 0.3)
        region = self.window.session.blur_regions[0]
        self.window.canvas.current_time = 9
        self.window._mark_blur_start()
        self.assertLess(region.start_time, region.end_time)
        self.window.canvas.current_time = 0
        self.window._mark_blur_end()
        self.assertLess(region.start_time, region.end_time)

    def test_minimum_window_layout_and_redaction_paint(self):
        self.loaded_session()
        self.window._on_blur_drawn(0.2, 0.2, 0.4, 0.4)
        self.window.session.blur_regions[0].mode = "redact"
        frame = QImage(640, 360, QImage.Format.Format_RGB32)
        frame.fill(QColor("white"))
        self.window._on_frame(frame)
        self.window.resize(960, 640)
        self.app.processEvents()
        self.assertEqual(self.window.width(), 960)
        self.assertLessEqual(
            self.window.blur_panel.minimumSizeHint().width(), self.window.blur_panel.width()
        )
        image = self.window.canvas.grab().toImage()
        center = self.window.canvas._to_screen(0.4, 0.4)
        self.assertEqual(image.pixelColor(center).name(), "#000000")

    def test_failed_and_canceled_export_preserve_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "output.mp4"
            target.write_bytes(b"existing export")
            for cancel in (False, True):
                state = EditSession("/missing/source.mp4", 2, 96, 64, 24, TrimRange(0, 2))
                worker = ExportWorker(state, str(target))
                if cancel:
                    worker.cancel()
                worker.start()
                self.assertTrue(worker.wait(10000))
                self.assertEqual(target.read_bytes(), b"existing export")
                self.assertEqual(list(Path(directory).glob(".screencut-*")), [])

    def test_real_preview_initial_frame_seek_and_playback(self):
        import shutil
        import subprocess
        import time

        if not shutil.which("ffmpeg"):
            self.skipTest("FFmpeg required")

        def wait_for(predicate):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                self.app.processEvents()
                if predicate():
                    return True
                time.sleep(0.01)
            return False

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=96x64:rate=24:duration=2",
                    "-c:v",
                    "libx264",
                    str(source),
                ],
                check=True,
                capture_output=True,
            )
            self.window._load_video(str(source))
            self.assertTrue(wait_for(lambda: self.window.canvas.frame_pixmap is not None))
            self.window._toggle_play()
            self.assertTrue(wait_for(lambda: self.window.canvas.current_time > 0.2))
            self.window._on_seek(1)
            self.assertTrue(
                wait_for(lambda: abs(self.window.preview.player.position() - 1000) < 100)
            )
            self.window.preview.close()
