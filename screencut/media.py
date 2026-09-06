"""Cancelable media workers and a persistent, audio-enabled preview player."""

import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile

from PyQt6.QtCore import QObject, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink

from screencut.processing import ProcessController, export_command


class ProcessWorker(QThread, ProcessController):
    """A Qt thread whose cancellation also stops its owned subprocess."""

    def __init__(self):
        QThread.__init__(self)
        ProcessController.__init__(self)

    def cancel(self):
        self.requestInterruption()
        ProcessController.cancel(self)


class ExportWorker(ProcessWorker):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, session, output_path):
        super().__init__()
        self.session = copy.deepcopy(session)
        self.output_path = output_path
        self.has_audio = True
        self.crf = 18

    def run(self):
        temporary = None
        artwork = tempfile.TemporaryDirectory(prefix="screencut-artwork-")
        try:
            target = Path(self.output_path).expanduser().resolve()
            if target == Path(self.session.video_path).expanduser().resolve():
                raise ValueError("Choose an output file different from the source video.")
            fd, temporary = tempfile.mkstemp(
                prefix=".screencut-", suffix=target.suffix or ".mp4", dir=target.parent
            )
            os.close(fd)
            from screencut.annotations import annotation_image

            paths = []
            for item in self.session.annotations:
                if self._cancelled.is_set():
                    raise InterruptedError("Export canceled.")
                path = Path(artwork.name) / f"annotation-{item.id}.png"
                if not annotation_image(item, self.session.width, self.session.height).save(
                    str(path)
                ):
                    raise RuntimeError("Could not render annotation artwork")
                paths.append(path)
            cmd, duration = export_command(self.session, temporary, self.has_audio, self.crf, paths)
            self.progress.emit(0, "Preparing export…")
            # Disk-backed stderr cannot deadlock or grow application memory.
            with tempfile.TemporaryFile(mode="w+t") as errors:
                proc = self._start(cmd, stdout=subprocess.PIPE, stderr=errors, text=True, bufsize=1)
                for line in proc.stdout:
                    if line.startswith("out_time_us="):
                        try:
                            seconds = int(line.partition("=")[2]) / 1_000_000
                            self.progress.emit(
                                min(99, int(seconds / duration * 100)),
                                f"Encoding {seconds:.1f}s / {duration:.1f}s",
                            )
                        except ValueError:
                            pass
                proc.stdout.close()
                proc.wait()
                if self._cancelled.is_set():
                    raise InterruptedError("Export canceled.")
                if proc.returncode:
                    errors.seek(0, os.SEEK_END)
                    errors.seek(max(0, errors.tell() - 4000))
                    raise RuntimeError(f"FFmpeg failed:\n{errors.read()}")
            # Commit and cancellation are serialized so canceled work never publishes.
            with self._lock:
                if self._cancelled.is_set():
                    raise InterruptedError("Export canceled.")
                os.replace(temporary, target)
                temporary = None
            self.progress.emit(100, "Export complete")
            self.finished.emit(True, str(target))
        except Exception as exc:
            self.finished.emit(False, str(exc))
        finally:
            artwork.cleanup()
            if self._process is not None and self._process.poll() is None:
                self._process.kill()
                self._process.wait()
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)


class ProbeWorker(ProcessWorker):
    ready = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, path):
        super().__init__()
        self.path = str(path)

    def run(self):
        try:
            proc = self._start(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_format",
                    "-show_streams",
                    "-of",
                    "json",
                    self.path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                output, error = proc.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise ValueError("Video inspection timed out.")
            if self._cancelled.is_set():
                return
            if proc.returncode:
                raise ValueError(error or "Cannot inspect this video.")
            data = json.loads(output)
            video = next(s for s in data["streams"] if s["codec_type"] == "video")
            numerator, denominator = video.get("avg_frame_rate", "30/1").split("/")
            fps = float(numerator) / float(denominator) if float(denominator) else 30.0
            self.ready.emit(
                dict(
                    duration=float(
                        data.get("format", {}).get("duration") or video.get("duration", 0)
                    ),
                    width=int(video["width"]),
                    height=int(video["height"]),
                    fps=fps or 30.0,
                    has_audio=any(s["codec_type"] == "audio" for s in data["streams"]),
                )
            )
        except Exception as exc:
            if not self._cancelled.is_set():
                self.failed.emit(str(exc))


class ThumbnailWorker(ProcessWorker):
    ready = pyqtSignal(list)

    def __init__(self, video_path, duration, count=20):
        super().__init__()
        self.video_path, self.duration, self.count = video_path, duration, max(1, count)

    def run(self):
        results = []
        for i in range(self.count):
            if self._cancelled.is_set():
                return
            timestamp = i * self.duration / self.count
            try:
                proc = self._start(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-nostdin",
                        "-ss",
                        str(timestamp),
                        "-i",
                        self.video_path,
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=120:-1",
                        "-f",
                        "image2pipe",
                        "-vcodec",
                        "mjpeg",
                        "pipe:1",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    data, _ = proc.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                    continue
                frame = QImage.fromData(data)
                if not frame.isNull():
                    results.append((timestamp, frame))
            except InterruptedError:
                return
            except OSError:
                break
        if not self._cancelled.is_set():
            self.ready.emit(results)


class PreviewController(QObject):
    """Main-thread controller. Times are seconds; decoded images arrive asynchronously."""

    frame_ready = pyqtSignal(QImage)
    position_changed = pyqtSignal(float)
    playback_changed = pyqtSignal(bool)
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._priming = False
        self._pending_position = 0
        self._wants_play = False
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.sink = QVideoSink(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self._frame)
        self.player.positionChanged.connect(lambda ms: self.position_changed.emit(ms / 1000))
        self.player.playbackStateChanged.connect(
            lambda state: self.playback_changed.emit(
                state == QMediaPlayer.PlaybackState.PlayingState and not self._priming
            )
        )
        self.player.errorOccurred.connect(lambda _code, message: self.error.emit(message))
        self.player.mediaStatusChanged.connect(self._media_status)

    def _media_status(self, status):
        if status == QMediaPlayer.MediaStatus.LoadedMedia and self._loading:
            self._loading = False
            self.player.setPosition(self._pending_position)
            # Decode an initial frame even when the editor starts paused.
            self._priming = not self._wants_play
            self.audio.setMuted(self._priming)
            self.player.play()

    def _frame(self, frame):
        if frame.isValid() and not self._loading:
            image = frame.toImage()
            if not image.isNull():
                self.frame_ready.emit(image)
                if self._priming:
                    self.player.pause()
                    self._priming = False
                    self.audio.setMuted(False)

    def open(self, path):
        self._loading = True
        self._priming = False
        self._wants_play = False
        self._pending_position = 0
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))

    def seek(self, seconds):
        self._pending_position = max(0, round(seconds * 1000))
        if not self._loading:
            self.player.setPosition(self._pending_position)

    def play(self):
        self._wants_play = True
        self._priming = False
        self.audio.setMuted(False)
        if not self._loading:
            self.player.play()

    def pause(self):
        self._wants_play = False
        self.player.pause()

    def set_rate(self, rate):
        self.player.setPlaybackRate(rate)

    def close(self):
        self._loading = True
        self.player.stop()
        self.player.setSource(QUrl())
