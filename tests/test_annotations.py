"""Annotation persistence, UI placement, and real exported-pixel regressions."""

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QColor
from PyQt6.QtTest import QTest
from screencut.models import EditSession, TrimRange, validate_session
from screencut.projects import save_project, load_project
from screencut.media import ExportWorker
from screencut.annotations import annotation_image
from editor import ScreenCut


class AnnotationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def state(self):
        return EditSession("/tmp/source.mp4", 3, 320, 180, 30, TrimRange(0.5, 2.5), 2)

    def test_round_trip_all_types_and_legacy_project(self):
        state = self.state()
        for kind in ("text", "arrow", "highlight"):
            state.add_annotation(kind, 0.1, 0.2, 0.8, 0.6, 0.5, 2)
        state.annotations[0].text = "Click 'Save': 100% [ready]\\\nمرحبا"
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "project.screencut"
            save_project(path, state)
            loaded = load_project(path)
            self.assertEqual(loaded.annotations, state.annotations)
            self.assertEqual(loaded.add_annotation("text", 0.1, 0.1, 0.5, 0.5, 0, 1).id, 3)
            data = json.loads(path.read_text())
            data["version"] = 1
            del data["session"]["annotations"]
            path.write_text(json.dumps(data))
            self.assertEqual(load_project(path).annotations, [])

    def test_validation_rejects_bad_geometry_color_and_times(self):
        state = self.state()
        item = state.add_annotation("text", 0.1, 0.1, 0.5, 0.5, 0, 1)
        for attr, value in [
            ("x", float("nan")),
            ("x2", 0.05),
            ("color", "red:evil"),
            ("end_time", 0),
            ("opacity", 0),
            ("size", 1),
            ("kind", "unknown"),
        ]:
            with self.subTest(attr=attr):
                bad = copy.deepcopy(state)
                setattr(bad.annotations[0], attr, value)
                with self.assertRaises(ValueError):
                    validate_session(bad)
        item.kind = "arrow"
        item.x, item.y, item.x2, item.y2 = 0.8, 0.5, 0.1, 0.5
        self.assertEqual(validate_session(state).annotations[0].x, 0.8)

    def test_draw_edit_undo_and_text_spaces(self):
        window = ScreenCut()
        try:
            window.session = self.state()
            window.history.reset(window.session)
            window._sync_session()
            window.show()
            frame = QImage(320, 180, QImage.Format.Format_RGB32)
            frame.fill(QColor("#111b29"))
            window._on_frame(frame)
            self.app.processEvents()
            window._start_annotation("arrow")
            start = window.canvas._to_screen(0.8, 0.6)
            end = window.canvas._to_screen(0.2, 0.6)
            QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(window.canvas, end)
            QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=end)
            self.assertEqual(len(window.session.annotations), 1)
            self.assertGreater(window.session.annotations[0].x, window.session.annotations[0].x2)
            window._undo()
            self.assertEqual(window.session.annotations, [])
            window._redo()
            self.assertEqual(len(window.session.annotations), 1)
            window._on_annotation_drawn("text", 0.1, 0.1, 0.8, 0.3)
            QTest.keyClicks(window.annotation_panel.text, "Save your work")
            self.assertEqual(window.session.annotations[-1].text, "Save your work")
            self.assertFalse(window.playing)
            window.annotation_panel.spins["opacity"].setValue(50)
            self.assertEqual(window.session.annotations[-1].opacity, 0.5)
            window.resize(960, 640)
            self.app.processEvents()
            self.assertEqual(window.width(), 960)
            window.grab().save("/tmp/screencut-annotations.png")
        finally:
            window._saved_session = copy.deepcopy(window.session)
            window.close()

    def test_text_arrow_highlight_render_nonempty_transparent_images(self):
        state = self.state()
        for kind in ("text", "arrow", "highlight"):
            item = state.add_annotation(kind, 0.1, 0.1, 0.8, 0.8, 0, 2)
            image = annotation_image(item, 320, 180)
            self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
            self.assertTrue(
                any(image.pixelColor(x, y).alpha() for y in range(18, 144) for x in range(32, 256))
            )

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_worker_export_annotation_pixels_and_trim_speed_timing(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source.mp4"
            target = Path(d) / "export.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=black:size=320x180:rate=30:duration=3",
                    "-c:v",
                    "libx264",
                    str(source),
                ],
                check=True,
                capture_output=True,
            )
            state = self.state()
            state.video_path = str(source)
            # Source .9–1.7 seconds maps to output .2–.6 after trim+2x speed.
            item = state.add_annotation("highlight", 0.1, 0.1, 0.3, 0.3, 0.9, 1.7)
            item.color = "#ff0000"
            item.opacity = 1
            state.add_annotation("text", 0.4, 0.1, 0.9, 0.4, 0.5, 2.5).text = "100%: 'Save' [OK]"
            state.add_annotation("arrow", 0.8, 0.8, 0.4, 0.8, 0.5, 2.5)
            worker = ExportWorker(state, str(target))
            worker.has_audio = False
            worker.start()
            self.assertTrue(worker.wait(15000))
            self.assertTrue(target.exists(), "Annotation export failed")
            for t, red in ((0.1, False), (0.4, True), (0.8, False)):
                raw = subprocess.run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-ss",
                        str(t),
                        "-i",
                        str(target),
                        "-frames:v",
                        "1",
                        "-f",
                        "rawvideo",
                        "-pix_fmt",
                        "rgb24",
                        "pipe:1",
                    ],
                    check=True,
                    capture_output=True,
                ).stdout
                offset = (36 * 320 + 64) * 3
                self.assertGreater(len(raw), offset + 3)
                self.assertEqual(raw[offset] > 180, red)
                # Arrow head stays visible and points left in the export.
                arrow = (144 * 320 + 132) * 3
                self.assertGreater(raw[arrow], 100)
            self.assertEqual(list(Path(d).glob(".screencut-*")), [])
