"""Real FFmpeg regression tests; fixtures are generated and removed automatically."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys
from types import SimpleNamespace
import unittest

from screencut.processing import ProcessController, build_filter, export_command


def session(path="", speed=1, regions=None):
    return SimpleNamespace(
        video_path=str(path),
        width=96,
        height=64,
        duration=2.0,
        trim=SimpleNamespace(start=0.25, end=1.75),
        speed=speed,
        blur_regions=regions or [],
    )


def region(mode="blur", **overrides):
    values = dict(id=1, x=0.2, y=0.2, w=0.5, h=0.5, start_time=0.25, end_time=1.5, mode=mode)
    values.update(overrides)
    return SimpleNamespace(**values)


class FilterValidationTests(unittest.TestCase):
    def test_invalid_region_time_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "start must precede end"):
            build_filter(session(regions=[region(start_time=1.5, end_time=1)]))

    def test_nonfinite_speed_is_rejected(self):
        with self.assertRaises(ValueError):
            build_filter(session(speed=float("nan")))

    def test_trim_speed_changes_redaction_times(self):
        graph, duration = build_filter(session(speed=2, regions=[region()]), False)
        self.assertEqual(duration, 0.75)
        self.assertIn("lt(t,0.625000000)", graph)
        self.assertNotIn("[0:a]", graph)


class CancellationTests(unittest.TestCase):
    def test_cancel_reaps_running_child(self):
        controller = ProcessController()
        proc = controller._start([sys.executable, "-c", "import time; time.sleep(30)"])
        controller.cancel()
        self.assertIsNotNone(proc.wait(timeout=4))
        with self.assertRaises(InterruptedError):
            controller._start([sys.executable, "-c", "pass"])

    def test_cancel_before_start_never_launches(self):
        controller = ProcessController()
        controller.cancel()
        with self.assertRaises(InterruptedError):
            controller._start(["this-command-must-never-launch"])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class ExportIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.source = Path(cls.directory.name) / "source.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=96x64:rate=24:duration=2",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=2",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-shortest",
                str(cls.source),
            ],
            check=True,
            capture_output=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def export(self, state, audio):
        output = Path(self.directory.name) / "output.mp4"
        command, duration = export_command(state, output, audio, crf=18)
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(output)],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(probe.stdout)
        self.assertAlmostEqual(float(data["format"]["duration"]), duration, delta=0.15)
        self.assertEqual(any(s["codec_type"] == "audio" for s in data["streams"]), audio)
        return output

    def test_overlapping_blur_and_redaction_fast_audio(self):
        self.export(session(self.source, 2, [region(), region("redact", id=2, x=0.4)]), True)

    def test_slow_export_without_audio(self):
        self.export(session(self.source, 0.5), False)

    def test_opaque_redaction_pixels_are_black(self):
        output = self.export(session(self.source, regions=[region("redact")]), False)
        raw = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                "0.25",
                "-i",
                str(output),
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ],
            capture_output=True,
            check=True,
        ).stdout
        offset = (25 * 96 + 35) * 3
        self.assertLess(max(raw[offset : offset + 3]), 12)

    def test_tiny_edge_blur_does_not_fail(self):
        self.export(
            session(self.source, regions=[region(x=0.999, y=0.999, w=0.001, h=0.001)]), False
        )


if __name__ == "__main__":
    unittest.main()
