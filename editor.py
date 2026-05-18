#!/usr/bin/env python3
"""
ScreenCut — macOS Screen Recording Editor
Features: Trim, Blur regions (time-ranged), Speed control, Export
"""

import sys
import os
import json
import subprocess
import tempfile
import threading
import time
import math
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSlider, QFileDialog, QScrollArea,
    QFrame, QSizePolicy, QSpinBox, QDoubleSpinBox, QMessageBox,
    QProgressDialog, QGroupBox, QGridLayout, QSplitter, QToolButton,
    QStatusBar, QComboBox, QDialog, QDialogButtonBox, QCheckBox
)
from PyQt6.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QRect, QPoint, QSize,
    QRectF, QPointF, QMimeData, pyqtSlot
)
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPixmap, QImage,
    QFontDatabase, QPalette, QLinearGradient, QKeySequence,
    QShortcut, QIcon, QCursor, QDrag
)

# ─── Data Models ──────────────────────────────────────────────────────────────

@dataclass
class BlurRegion:
    id: int
    x: float          # 0.0–1.0 (normalized)
    y: float
    w: float
    h: float
    start_time: float  # seconds
    end_time: float
    label: str = ""

    def to_ffmpeg_filter(self, video_w: int, video_h: int, idx: int) -> str:
        px = int(self.x * video_w)
        py = int(self.y * video_h)
        pw = int(self.w * video_w)
        ph = int(self.h * video_h)
        # Make dimensions even for boxblur
        pw = max(pw, 2)
        ph = max(ph, 2)
        tag_in = f"[blur_in_{idx}]" if idx > 0 else "[0:v]"
        tag_out = f"[blur_out_{idx}]"
        return (
            f"{tag_in}split=2[base_{idx}][over_{idx}];"
            f"[over_{idx}]crop={pw}:{ph}:{px}:{py},"
            f"boxblur=20:5[blurred_{idx}];"
            f"[base_{idx}][blurred_{idx}]overlay={px}:{py}"
            f":enable='between(t,{self.start_time},{self.end_time})'{tag_out}"
        )

@dataclass
class TrimRange:
    start: float = 0.0
    end: float = 0.0   # 0 means use full duration

@dataclass
class EditSession:
    video_path: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 30.0
    trim: TrimRange = field(default_factory=TrimRange)
    speed: float = 1.0
    blur_regions: List[BlurRegion] = field(default_factory=list)
    _next_blur_id: int = 0

    def add_blur(self, x, y, w, h, start, end) -> BlurRegion:
        b = BlurRegion(self._next_blur_id, x, y, w, h, start, end)
        self._next_blur_id += 1
        self.blur_regions.append(b)
        return b

    def remove_blur(self, bid: int):
        self.blur_regions = [b for b in self.blur_regions if b.id != bid]

    def effective_duration(self) -> float:
        end = self.trim.end if self.trim.end > 0 else self.duration
        raw = end - self.trim.start
        return raw / self.speed if self.speed > 0 else raw

# ─── FFmpeg Worker ────────────────────────────────────────────────────────────

class ExportWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, session: EditSession, output_path: str):
        super().__init__()
        self.session = session
        self.output_path = output_path

    def run(self):
        try:
            self.progress.emit(5, "Preparing export…")
            s = self.session
            vw, vh = s.width, s.height

            trim_start = s.trim.start
            trim_end = s.trim.end if s.trim.end > 0 else s.duration
            trim_dur = trim_end - trim_start
            duration_out = trim_dur / max(s.speed, 0.01)

            # ── Video filter chain ──────────────────────────────────────────
            # Strategy: trim → reset PTS → speed → chain blur regions
            # Each blur uses crop+boxblur+overlay (no split needed — overlay
            # with enable only activates during the time window).
            # We keep a running label [vN] flowing through each blur step.

            pts_factor = 1.0 / s.speed

            # Step 1: trim the raw stream and reset PTS to 0
            # setpts=PTS-STARTPTS resets after trim; then scale PTS for speed
            v_chain = (
                f"[0:v]trim=start={trim_start:.6f}:duration={trim_dur:.6f},"
                f"setpts={pts_factor:.8f}*(PTS-STARTPTS)[v_base]"
            )

            filter_parts = [v_chain]
            prev_label = "[v_base]"

            for i, br in enumerate(s.blur_regions):
                # Times relative to trim start, then adjusted for speed
                t0 = max(0.0, (br.start_time - trim_start)) / s.speed
                t1 = max(0.0, (br.end_time   - trim_start)) / s.speed
                t1 = min(t1, duration_out)

                if t1 <= t0:
                    continue  # skip degenerate regions

                # Pixel coords — ensure positive and within frame
                px = max(0, int(br.x * vw))
                py = max(0, int(br.y * vh))
                pw = max(4, int(br.w * vw))
                ph = max(4, int(br.h * vh))
                # Clamp to frame bounds
                pw = min(pw, vw - px)
                ph = min(ph, vh - py)
                # Make even (required by libx264 subsampling)
                pw = pw - (pw % 2)
                ph = ph - (ph % 2)

                out_label = f"[v{i}]"

                # Approach: crop the region → apply gaussian blur → overlay back
                # The overlay enable expression gates it to [t0, t1]
                blur_part = (
                    f"{prev_label}split=2[pass{i}][crop_in{i}];"
                    f"[crop_in{i}]crop={pw}:{ph}:{px}:{py},"
                    f"gblur=sigma=20[blurred{i}];"
                    f"[pass{i}][blurred{i}]overlay={px}:{py}"
                    f":enable='between(t,{t0:.6f},{t1:.6f})'{out_label}"
                )
                filter_parts.append(blur_part)
                prev_label = out_label

            # Rename final label to [vout]
            if prev_label == "[v_base]":
                # No blurs — just rename
                filter_parts[0] = filter_parts[0].replace("[v_base]", "[vout]")
            else:
                # Replace last label with [vout]
                filter_parts[-1] = filter_parts[-1].rsplit(prev_label, 1)[0] + "[vout]"

            # ── Audio filter chain ──────────────────────────────────────────
            # atempo range is 0.5–2.0; chain multiple for speeds outside range
            speed = s.speed
            atempo_filters = []
            if speed < 0.5:
                # chain downward: each step halves at 0.5 minimum
                tmp = speed
                while tmp < 0.5:
                    atempo_filters.append("atempo=0.5")
                    tmp /= 0.5
                atempo_filters.append(f"atempo={tmp:.6f}")
            elif speed > 2.0:
                tmp = speed
                while tmp > 2.0:
                    atempo_filters.append("atempo=2.0")
                    tmp /= 2.0
                atempo_filters.append(f"atempo={tmp:.6f}")
            else:
                atempo_filters.append(f"atempo={speed:.6f}")

            has_audio = getattr(self, 'has_audio', True)
            if has_audio:
                a_chain = (
                    f"[0:a]atrim=start={trim_start:.6f}:duration={trim_dur:.6f},"
                    f"asetpts=PTS-STARTPTS,"
                    + ",".join(atempo_filters) +
                    "[aout]"
                )
                filter_parts.append(a_chain)

            full_filter = ";".join(filter_parts)

            self.progress.emit(15, "Building filter graph…")

            # Print filter for debugging
            print("=== FFmpeg filter_complex ===")
            print(full_filter)
            print("=============================")

            crf = getattr(self, 'crf', 18)

            cmd = [
                "ffmpeg", "-y",
                "-i", s.video_path,
                "-filter_complex", full_filter,
                "-map", "[vout]",
            ]
            if has_audio:
                cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
            else:
                cmd += ["-an"]
            cmd += [
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", str(crf),
                "-movflags", "+faststart",
                self.output_path
            ]

            self.progress.emit(20, "Running FFmpeg…")

            proc = subprocess.Popen(
                cmd,
                stderr=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                text=True,
                bufsize=1
            )

            stderr_lines = []
            for line in proc.stderr:
                stderr_lines.append(line)
                if "time=" in line:
                    try:
                        t_str = line.split("time=")[1].split(" ")[0]
                        parts = t_str.split(":")
                        t_sec = float(parts[0])*3600 + float(parts[1])*60 + float(parts[2])
                        pct = min(95, int(20 + 75 * (t_sec / max(duration_out, 0.1))))
                        self.progress.emit(pct, f"Encoding… {t_sec:.1f}s / {duration_out:.1f}s")
                    except:
                        pass

            proc.wait()
            if proc.returncode == 0:
                self.progress.emit(100, "Done!")
                self.finished.emit(True, self.output_path)
            else:
                # Surface the actual ffmpeg error message
                error_lines = [l for l in stderr_lines if "Error" in l or "Invalid" in l or "error" in l]
                error_msg = "\n".join(error_lines[-5:]) if error_lines else "\n".join(stderr_lines[-8:])
                print("=== FFmpeg stderr ===")
                print("".join(stderr_lines[-20:]))
                print("====================")
                self.finished.emit(False, f"FFmpeg error (code {proc.returncode}):\n{error_msg}")
        except Exception as e:
            import traceback
            self.finished.emit(False, f"{e}\n{traceback.format_exc()}")


class ThumbnailWorker(QThread):
    ready = pyqtSignal(list)  # list of (time, QPixmap)

    def __init__(self, video_path: str, duration: float, count: int = 20):
        super().__init__()
        self.video_path = video_path
        self.duration = duration
        self.count = count

    def run(self):
        results = []
        step = self.duration / self.count
        for i in range(self.count):
            t = i * step
            try:
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                    tmp = f.name
                cmd = [
                    "ffmpeg", "-y", "-ss", str(t),
                    "-i", self.video_path,
                    "-vframes", "1",
                    "-vf", "scale=120:-1",
                    tmp
                ]
                subprocess.run(cmd, capture_output=True, timeout=5)
                pix = QPixmap(tmp)
                if not pix.isNull():
                    results.append((t, pix))
                os.unlink(tmp)
            except:
                pass
        self.ready.emit(results)


# ─── Video Preview Widget ─────────────────────────────────────────────────────

class VideoCanvas(QWidget):
    """Displays video frame + overlay for drawing blur regions."""
    blur_added = pyqtSignal(float, float, float, float)  # x,y,w,h normalized

    def __init__(self):
        super().__init__()
        self.frame_pixmap: Optional[QPixmap] = None
        self.blur_regions: List[BlurRegion] = []
        self.current_time: float = 0.0
        self.selected_blur_id: Optional[int] = None
        self.draw_mode = False  # True = drawing new blur
        self._drag_start: Optional[QPoint] = None
        self._drag_rect: Optional[QRect] = None
        self.video_rect = QRect()
        self.setMinimumSize(640, 360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    def set_frame(self, pixmap: QPixmap):
        self.frame_pixmap = pixmap
        self.update()

    def set_blurs(self, blurs: List[BlurRegion]):
        self.blur_regions = blurs
        self.update()

    def _compute_video_rect(self) -> QRect:
        if not self.frame_pixmap:
            return QRect()
        w, h = self.width(), self.height()
        vw, vh = self.frame_pixmap.width(), self.frame_pixmap.height()
        scale = min(w / vw, h / vh)
        sw, sh = int(vw * scale), int(vh * scale)
        ox, oy = (w - sw) // 2, (h - sh) // 2
        return QRect(ox, oy, sw, sh)

    def _to_normalized(self, screen_pt: QPoint) -> Tuple[float, float]:
        r = self.video_rect
        if r.isEmpty():
            return (0, 0)
        nx = (screen_pt.x() - r.x()) / r.width()
        ny = (screen_pt.y() - r.y()) / r.height()
        return (max(0, min(1, nx)), max(0, min(1, ny)))

    def _to_screen(self, nx: float, ny: float) -> QPoint:
        r = self.video_rect
        return QPoint(int(r.x() + nx * r.width()), int(r.y() + ny * r.height()))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background
        p.fillRect(self.rect(), QColor("#0a0a0f"))

        if self.frame_pixmap:
            self.video_rect = self._compute_video_rect()
            p.drawPixmap(self.video_rect, self.frame_pixmap)

            # Draw blur overlays
            for br in self.blur_regions:
                if not (br.start_time <= self.current_time <= br.end_time):
                    continue
                x1 = self._to_screen(br.x, br.y)
                x2 = self._to_screen(br.x + br.w, br.y + br.h)
                rect = QRect(x1, x2)
                is_sel = (br.id == self.selected_blur_id)

                # Semi-transparent fill to indicate blur
                color = QColor(0, 120, 255, 60) if is_sel else QColor(255, 200, 0, 40)
                p.fillRect(rect, color)

                # Border
                pen = QPen(QColor(0, 180, 255) if is_sel else QColor(255, 200, 0), 2)
                pen.setStyle(Qt.PenStyle.DashLine if not is_sel else Qt.PenStyle.SolidLine)
                p.setPen(pen)
                p.drawRect(rect)

                # Label
                p.setPen(QPen(QColor(255, 255, 255, 200)))
                f = QFont("SF Mono", 9)
                f.setBold(True)
                p.setFont(f)
                label = br.label or f"Blur #{br.id+1}"
                p.drawText(rect.adjusted(4, 3, 0, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, label)

            # Active drawing rect
            if self._drag_rect and self.draw_mode:
                p.fillRect(self._drag_rect, QColor(0, 200, 100, 50))
                pen = QPen(QColor(0, 255, 100), 2)
                pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.drawRect(self._drag_rect)

        else:
            # Empty state
            p.setPen(QPen(QColor("#333")))
            p.drawRect(self.rect().adjusted(1,1,-1,-1))
            p.setPen(QPen(QColor("#555")))
            f = QFont("SF Pro Display", 16)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Drop a video file or click Open")

    def mousePressEvent(self, event):
        if not self.frame_pixmap or event.button() != Qt.MouseButton.LeftButton:
            return
        if self.draw_mode:
            self._drag_start = event.pos()
            self._drag_rect = None
        else:
            # Select blur
            nx, ny = self._to_normalized(event.pos())
            for br in reversed(self.blur_regions):
                if (br.start_time <= self.current_time <= br.end_time and
                        br.x <= nx <= br.x + br.w and
                        br.y <= ny <= br.y + br.h):
                    self.selected_blur_id = br.id
                    self.update()
                    return
            self.selected_blur_id = None
            self.update()

    def mouseMoveEvent(self, event):
        if self.draw_mode and self._drag_start:
            p = event.pos()
            self._drag_rect = QRect(self._drag_start, p).normalized()
            self.update()

    def mouseReleaseEvent(self, event):
        if self.draw_mode and self._drag_start and self._drag_rect:
            r = self._drag_rect
            if r.width() > 10 and r.height() > 10:
                nx, ny = self._to_normalized(r.topLeft())
                nw = r.width() / self.video_rect.width()
                nh = r.height() / self.video_rect.height()
                self.blur_added.emit(nx, ny, nw, nh)
            self._drag_start = None
            self._drag_rect = None
            self.update()


# ─── Timeline Widget ──────────────────────────────────────────────────────────

class Timeline(QWidget):
    seek = pyqtSignal(float)
    trim_changed = pyqtSignal(float, float)

    def __init__(self):
        super().__init__()
        self.duration: float = 0.0
        self.current_time: float = 0.0
        self.trim_start: float = 0.0
        self.trim_end: float = 0.0
        self.blur_regions: List[BlurRegion] = []
        self.thumbnails: List[Tuple[float, QPixmap]] = []
        self._drag = None  # 'playhead' | 'trim_start' | 'trim_end'
        self.setMinimumHeight(90)
        self.setMaximumHeight(110)
        self.setMouseTracking(True)

    THUMB_H = 50
    RULER_H = 18
    MARGIN = 12

    def _t_to_x(self, t: float) -> int:
        if self.duration <= 0:
            return self.MARGIN
        w = self.width() - 2 * self.MARGIN
        return int(self.MARGIN + (t / self.duration) * w)

    def _x_to_t(self, x: int) -> float:
        if self.duration <= 0:
            return 0
        w = self.width() - 2 * self.MARGIN
        t = (x - self.MARGIN) / w * self.duration
        return max(0, min(self.duration, t))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#0d0d15"))

        if self.duration <= 0:
            p.setPen(QPen(QColor("#444")))
            p.setFont(QFont("SF Pro Text", 11))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Load a video to see timeline")
            return

        M = self.MARGIN
        TH = self.THUMB_H
        RH = self.RULER_H

        # Thumbnail strip background
        strip_rect = QRect(M, 0, W - 2*M, TH)
        p.fillRect(strip_rect, QColor("#1a1a28"))

        # Draw thumbnails
        for t, pix in self.thumbnails:
            x = self._t_to_x(t)
            scaled = pix.scaledToHeight(TH, Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(x, 0, scaled)

        # Dimmed area outside trim
        trim_end = self.trim_end if self.trim_end > 0 else self.duration
        tx_s = self._t_to_x(self.trim_start)
        tx_e = self._t_to_x(trim_end)
        p.fillRect(QRect(M, 0, tx_s - M, TH), QColor(0, 0, 0, 150))
        p.fillRect(QRect(tx_e, 0, W - M - tx_e, TH), QColor(0, 0, 0, 150))

        # Trim handles
        pen = QPen(QColor("#00d4ff"), 2)
        p.setPen(pen)
        p.drawLine(tx_s, 0, tx_s, TH)
        p.drawLine(tx_e, 0, tx_e, TH)
        # Handle caps
        p.setBrush(QBrush(QColor("#00d4ff")))
        p.drawRect(QRect(tx_s - 5, 0, 10, 16))
        p.drawRect(QRect(tx_e - 5, 0, 10, 16))

        # Blur bands under ruler
        ruler_y = TH + 2
        for br in self.blur_regions:
            bx_s = self._t_to_x(br.start_time)
            bx_e = self._t_to_x(br.end_time)
            p.fillRect(QRect(bx_s, ruler_y, bx_e - bx_s, RH - 4), QColor(255, 200, 0, 160))
            p.setPen(QPen(QColor(255, 220, 50)))
            p.setFont(QFont("SF Mono", 7))
            label = br.label or f"B{br.id+1}"
            p.drawText(QRect(bx_s+2, ruler_y, bx_e - bx_s - 4, RH - 4),
                       Qt.AlignmentFlag.AlignVCenter, label)

        # Ruler ticks
        p.setPen(QPen(QColor("#555")))
        p.setFont(QFont("SF Mono", 8))
        step = max(1, int(self.duration / 10))
        for sec in range(0, int(self.duration) + 1, step):
            tx = self._t_to_x(sec)
            p.drawLine(tx, TH, tx, TH + RH)
            mins = sec // 60
            secs = sec % 60
            label = f"{mins}:{secs:02d}"
            p.drawText(tx + 2, TH + RH - 2, label)

        # Playhead
        cx = self._t_to_x(self.current_time)
        p.setPen(QPen(QColor("#ff4466"), 2))
        p.drawLine(cx, 0, cx, H)
        # Playhead diamond
        p.setBrush(QBrush(QColor("#ff4466")))
        p.setPen(Qt.PenStyle.NoPen)
        diamond = [QPoint(cx, 0), QPoint(cx+6, 8), QPoint(cx, 16), QPoint(cx-6, 8)]
        from PyQt6.QtGui import QPolygon
        p.drawPolygon(QPolygon(diamond))

    def mousePressEvent(self, event):
        if self.duration <= 0:
            return
        x = event.pos().x()
        trim_end = self.trim_end if self.trim_end > 0 else self.duration
        tx_s = self._t_to_x(self.trim_start)
        tx_e = self._t_to_x(trim_end)
        cx = self._t_to_x(self.current_time)

        if abs(x - tx_s) < 10:
            self._drag = 'trim_start'
        elif abs(x - tx_e) < 10:
            self._drag = 'trim_end'
        elif abs(x - cx) < 8:
            self._drag = 'playhead'
        else:
            self._drag = 'playhead'
            t = self._x_to_t(x)
            self.current_time = t
            self.seek.emit(t)
            self.update()

    def mouseMoveEvent(self, event):
        if not self._drag or self.duration <= 0:
            return
        t = self._x_to_t(event.pos().x())
        trim_end = self.trim_end if self.trim_end > 0 else self.duration
        if self._drag == 'playhead':
            self.current_time = t
            self.seek.emit(t)
        elif self._drag == 'trim_start':
            self.trim_start = max(0, min(t, trim_end - 0.5))
            self.trim_changed.emit(self.trim_start, self.trim_end)
        elif self._drag == 'trim_end':
            self.trim_end = max(self.trim_start + 0.5, min(t, self.duration))
            self.trim_changed.emit(self.trim_start, self.trim_end)
        self.update()

    def mouseReleaseEvent(self, event):
        self._drag = None


# ─── Blur Panel ───────────────────────────────────────────────────────────────

class BlurPanel(QWidget):
    region_selected = pyqtSignal(int)
    region_removed = pyqtSignal(int)
    region_updated = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.session: Optional[EditSession] = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("BLUR REGIONS")
        title.setStyleSheet("color: #888; font: 9px 'SF Mono'; letter-spacing: 2px;")
        layout.addWidget(title)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("background: transparent;")
        layout.addWidget(self.scroll)

        self.container = QWidget()
        self.container.setStyleSheet("background: transparent;")
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setSpacing(4)
        self.vbox.addStretch()
        self.scroll.setWidget(self.container)

    def refresh(self, session: EditSession, current_time: float):
        self.session = session
        # Clear
        while self.vbox.count() > 1:
            item = self.vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for br in session.blur_regions:
            card = self._make_card(br, session.duration, current_time)
            self.vbox.insertWidget(self.vbox.count() - 1, card)

    def _make_card(self, br: BlurRegion, duration: float, current_time: float) -> QWidget:
        card = QFrame()
        card.setObjectName(f"blur_card_{br.id}")
        active = br.start_time <= current_time <= br.end_time
        card.setStyleSheet(f"""
            QFrame {{
                background: {'#1a2535' if active else '#141420'};
                border: 1px solid {'#00d4ff' if active else '#2a2a3a'};
                border-radius: 6px;
                padding: 4px;
            }}
        """)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # Header
        hdr = QHBoxLayout()
        lbl = QLabel(br.label or f"Blur #{br.id + 1}")
        lbl.setStyleSheet("color: #eee; font: bold 11px 'SF Pro Text';")
        hdr.addWidget(lbl)

        if active:
            dot = QLabel("●")
            dot.setStyleSheet("color: #00d4ff; font-size: 8px;")
            hdr.addWidget(dot)
        hdr.addStretch()

        del_btn = QToolButton()
        del_btn.setText("✕")
        del_btn.setStyleSheet("""
            QToolButton { color: #ff4466; background: transparent; border: none; font: 13px; }
            QToolButton:hover { color: #ff6688; }
        """)
        del_btn.clicked.connect(lambda _, bid=br.id: self.region_removed.emit(bid))
        hdr.addWidget(del_btn)
        layout.addLayout(hdr)

        # Time range
        time_row = QHBoxLayout()
        time_row.setSpacing(4)

        def make_spin(val, max_val, attr, region):
            sp = QDoubleSpinBox()
            sp.setRange(0, max_val)
            sp.setSingleStep(0.1)
            sp.setDecimals(2)
            sp.setValue(val)
            sp.setSuffix("s")
            sp.setStyleSheet("""
                QDoubleSpinBox {
                    background: #0d0d18; color: #ccc;
                    border: 1px solid #333; border-radius: 4px;
                    padding: 2px 4px; font: 10px 'SF Mono';
                }
            """)
            sp.setFixedWidth(72)
            def on_change(v, a=attr, r=region):
                setattr(r, a, v)
                self.region_updated.emit()
            sp.valueChanged.connect(on_change)
            return sp

        time_row.addWidget(QLabel("<span style='color:#666;font:9px SF Mono'>START</span>"))
        time_row.addWidget(make_spin(br.start_time, duration, 'start_time', br))
        time_row.addWidget(QLabel("<span style='color:#666;font:9px SF Mono'>END</span>"))
        time_row.addWidget(make_spin(br.end_time, duration, 'end_time', br))
        time_row.addStretch()
        layout.addLayout(time_row)

        # Pos info
        pos_lbl = QLabel(
            f"<span style='color:#555;font:9px SF Mono'>"
            f"x:{br.x:.2f} y:{br.y:.2f} w:{br.w:.2f} h:{br.h:.2f}"
            f"</span>"
        )
        pos_lbl.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(pos_lbl)

        card.mousePressEvent = lambda e, bid=br.id: self.region_selected.emit(bid)
        return card


# ─── Main Window ──────────────────────────────────────────────────────────────

class ScreenCut(QMainWindow):
    def __init__(self):
        super().__init__()
        self.session = EditSession()
        self.playing = False
        self._playback_timer = QTimer()
        self._playback_timer.setInterval(33)  # ~30fps
        self._playback_timer.timeout.connect(self._advance_playback)
        self._frame_cache = {}
        self._thumb_worker: Optional[ThumbnailWorker] = None
        self._export_worker: Optional[ExportWorker] = None
        self._setup_style()
        self._setup_ui()
        self._setup_shortcuts()

    def _setup_style(self):
        self.setStyleSheet("""
            QMainWindow { background: #08080f; }
            QWidget { background: #08080f; color: #ddd; font-family: 'SF Pro Text', system-ui; }
            QPushButton {
                background: #1c1c2e; color: #ccc;
                border: 1px solid #2a2a40; border-radius: 6px;
                padding: 6px 14px; font-size: 12px;
            }
            QPushButton:hover { background: #252538; border-color: #3a3a55; }
            QPushButton:pressed { background: #141422; }
            QPushButton:disabled { color: #444; border-color: #1a1a28; }
            QPushButton#primary {
                background: #0066ff; color: white;
                border: 1px solid #0055dd;
            }
            QPushButton#primary:hover { background: #0077ff; }
            QPushButton#danger {
                background: #2d1018; color: #ff4466;
                border: 1px solid #3d1828;
            }
            QPushButton#danger:hover { background: #3d1828; }
            QPushButton#accent {
                background: #002a1a; color: #00d4aa;
                border: 1px solid #004030;
            }
            QPushButton#accent:hover { background: #003320; }
            QLabel { color: #bbb; background: transparent; }
            QGroupBox {
                border: 1px solid #1e1e30; border-radius: 8px;
                margin-top: 10px; padding-top: 8px;
                font-size: 10px; color: #555;
            }
            QGroupBox::title {
                subcontrol-origin: margin; subcontrol-position: top left;
                left: 10px; color: #555; letter-spacing: 1.5px;
            }
            QSlider::groove:horizontal {
                background: #1a1a2a; height: 4px; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #00d4ff; width: 14px; height: 14px;
                border-radius: 7px; margin: -5px 0;
            }
            QSlider::sub-page:horizontal { background: #00d4ff; border-radius: 2px; }
            QDoubleSpinBox, QSpinBox, QComboBox {
                background: #0d0d18; color: #ccc;
                border: 1px solid #2a2a3a; border-radius: 5px;
                padding: 4px 8px;
            }
            QStatusBar { background: #050508; color: #555; font: 10px 'SF Mono'; }
            QSplitter::handle { background: #1a1a28; }
        """)

    def _setup_ui(self):
        self.setWindowTitle("ScreenCut — Screen Recording Editor")
        self.resize(1280, 820)
        self.setMinimumSize(960, 640)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top toolbar
        root.addWidget(self._build_toolbar())

        # Main split
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(self.splitter, stretch=1)

        # Left: canvas + timeline
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(8)

        self.canvas = VideoCanvas()
        self.canvas.blur_added.connect(self._on_blur_drawn)
        left_layout.addWidget(self.canvas, stretch=1)

        # Playback controls
        left_layout.addWidget(self._build_playback_controls())

        # Timeline
        self.timeline = Timeline()
        self.timeline.seek.connect(self._on_seek)
        self.timeline.trim_changed.connect(self._on_trim_changed)
        left_layout.addWidget(self.timeline)

        # Time display
        self.time_label = QLabel("0:00.00  /  0:00.00")
        self.time_label.setStyleSheet("color: #555; font: 11px 'SF Mono'; padding: 2px 0;")
        left_layout.addWidget(self.time_label)

        self.splitter.addWidget(left_panel)

        # Right: controls
        right_panel = self._build_right_panel()
        right_panel.setFixedWidth(300)
        self.splitter.addWidget(right_panel)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready — open a screen recording to begin")

    def _build_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet("background: #0d0d18; border-bottom: 1px solid #1a1a28;")
        bar.setFixedHeight(52)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(10)

        # Logo
        logo = QLabel("✦ ScreenCut")
        logo.setStyleSheet("color: #00d4ff; font: bold 17px 'SF Pro Display'; letter-spacing: -0.5px;")
        layout.addWidget(logo)

        layout.addStretch()

        self.open_btn = QPushButton("⌘ Open Video")
        self.open_btn.setObjectName("primary")
        self.open_btn.setFixedHeight(34)
        self.open_btn.clicked.connect(self._open_video)
        layout.addWidget(self.open_btn)

        self.blur_draw_btn = QPushButton("✥ Draw Blur")
        self.blur_draw_btn.setCheckable(True)
        self.blur_draw_btn.setFixedHeight(34)
        self.blur_draw_btn.setStyleSheet("""
            QPushButton { background: #0d1a10; color: #00cc88; border: 1px solid #1a3020; border-radius: 6px; padding: 6px 14px; }
            QPushButton:checked { background: #00cc88; color: #000; border-color: #00cc88; }
            QPushButton:hover:!checked { background: #122018; }
        """)
        self.blur_draw_btn.toggled.connect(self._toggle_draw_mode)
        self.blur_draw_btn.setEnabled(False)
        layout.addWidget(self.blur_draw_btn)

        self.export_btn = QPushButton("⬇ Export")
        self.export_btn.setObjectName("accent")
        self.export_btn.setFixedHeight(34)
        self.export_btn.clicked.connect(self._export)
        self.export_btn.setEnabled(False)
        layout.addWidget(self.export_btn)

        return bar

    def _build_playback_controls(self) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        btn_style = """
            QPushButton {
                background: #141420; color: #aaa;
                border: 1px solid #222235; border-radius: 6px;
                padding: 4px 10px; font-size: 15px; min-width: 36px;
            }
            QPushButton:hover { background: #1c1c2e; color: #eee; }
            QPushButton:disabled { color: #333; }
        """

        self.prev_frame_btn = QPushButton("⏮")
        self.prev_frame_btn.setStyleSheet(btn_style)
        self.prev_frame_btn.clicked.connect(lambda: self._step_frame(-1))
        self.prev_frame_btn.setEnabled(False)
        layout.addWidget(self.prev_frame_btn)

        self.play_btn = QPushButton("▶")
        self.play_btn.setStyleSheet(btn_style + "QPushButton { font-size: 16px; min-width: 44px; }")
        self.play_btn.clicked.connect(self._toggle_play)
        self.play_btn.setEnabled(False)
        layout.addWidget(self.play_btn)

        self.next_frame_btn = QPushButton("⏭")
        self.next_frame_btn.setStyleSheet(btn_style)
        self.next_frame_btn.clicked.connect(lambda: self._step_frame(1))
        self.next_frame_btn.setEnabled(False)
        layout.addWidget(self.next_frame_btn)

        layout.addSpacing(12)

        # Set blur start/end at current time
        self.mark_start_btn = QPushButton("[ Mark Start")
        self.mark_start_btn.setStyleSheet(btn_style)
        self.mark_start_btn.setEnabled(False)
        self.mark_start_btn.clicked.connect(self._mark_blur_start)
        layout.addWidget(self.mark_start_btn)

        self.mark_end_btn = QPushButton("Mark End ]")
        self.mark_end_btn.setStyleSheet(btn_style)
        self.mark_end_btn.setEnabled(False)
        self.mark_end_btn.clicked.connect(self._mark_blur_end)
        layout.addWidget(self.mark_end_btn)

        layout.addStretch()
        return w

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet("background: #0b0b15; border-left: 1px solid #181828;")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Video info
        self.info_group = QGroupBox("VIDEO INFO")
        info_layout = QGridLayout(self.info_group)
        info_layout.setSpacing(4)
        self.info_labels = {}
        for i, (k, v) in enumerate([("File", "—"), ("Duration", "—"), ("Resolution", "—"), ("FPS", "—")]):
            lbl = QLabel(k + ":")
            lbl.setStyleSheet("color: #555; font: 9px 'SF Mono'; letter-spacing: 1px;")
            val = QLabel(v)
            val.setStyleSheet("color: #bbb; font: 10px 'SF Mono';")
            val.setWordWrap(True)
            info_layout.addWidget(lbl, i, 0)
            info_layout.addWidget(val, i, 1)
            self.info_labels[k] = val
        layout.addWidget(self.info_group)

        # Trim
        trim_group = QGroupBox("TRIM")
        trim_layout = QGridLayout(trim_group)
        trim_layout.setSpacing(6)

        trim_layout.addWidget(QLabel("In:"), 0, 0)
        self.trim_start_lbl = QLabel("0.00s")
        self.trim_start_lbl.setStyleSheet("color: #00d4ff; font: 11px 'SF Mono';")
        trim_layout.addWidget(self.trim_start_lbl, 0, 1)

        trim_layout.addWidget(QLabel("Out:"), 1, 0)
        self.trim_end_lbl = QLabel("—")
        self.trim_end_lbl.setStyleSheet("color: #00d4ff; font: 11px 'SF Mono';")
        trim_layout.addWidget(self.trim_end_lbl, 1, 1)

        reset_trim = QPushButton("Reset Trim")
        reset_trim.clicked.connect(self._reset_trim)
        trim_layout.addWidget(reset_trim, 2, 0, 1, 2)
        layout.addWidget(trim_group)

        # Speed
        speed_group = QGroupBox("SPEED")
        speed_layout = QVBoxLayout(speed_group)

        speed_row = QHBoxLayout()
        self.speed_label = QLabel("1.0×")
        self.speed_label.setStyleSheet("color: #ffb340; font: bold 18px 'SF Mono'; min-width: 52px;")
        speed_row.addWidget(self.speed_label)

        presets = QHBoxLayout()
        for sp in [0.5, 1.0, 1.5, 2.0, 3.0, 4.0]:
            btn = QPushButton(f"{sp}×")
            btn.setFixedSize(40, 26)
            btn.setStyleSheet("""
                QPushButton { background: #141425; color: #888; border: 1px solid #222236;
                              border-radius: 4px; font: 10px 'SF Mono'; }
                QPushButton:hover { background: #1e1e35; color: #eee; }
            """)
            btn.clicked.connect(lambda _, s=sp: self._set_speed(s))
            presets.addWidget(btn)
        speed_layout.addLayout(speed_row)
        speed_layout.addLayout(presets)

        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(25, 800)  # 0.25x to 8x
        self.speed_slider.setValue(100)
        self.speed_slider.valueChanged.connect(lambda v: self._set_speed(v / 100))
        speed_layout.addWidget(self.speed_slider)
        layout.addWidget(speed_group)

        # Blur list
        blur_group = QGroupBox("BLUR REGIONS")
        blur_layout = QVBoxLayout(blur_group)
        blur_layout.setContentsMargins(0, 0, 0, 0)

        hint = QLabel("Draw blur: enable Draw Blur, then drag on video.\nAdjust timing in the list below.")
        hint.setStyleSheet("color: #444; font: 9px 'SF Pro Text'; padding: 4px 8px;")
        hint.setWordWrap(True)
        blur_layout.addWidget(hint)

        self.blur_panel = BlurPanel()
        self.blur_panel.region_selected.connect(self._on_blur_selected)
        self.blur_panel.region_removed.connect(self._remove_blur)
        self.blur_panel.region_updated.connect(self._refresh_canvas)
        blur_layout.addWidget(self.blur_panel)
        layout.addWidget(blur_group, stretch=1)

        # Export settings
        exp_group = QGroupBox("EXPORT")
        exp_layout = QGridLayout(exp_group)

        exp_layout.addWidget(QLabel("Quality:"), 0, 0)
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["High (CRF 18)", "Medium (CRF 23)", "Low (CRF 28)"])
        exp_layout.addWidget(self.quality_combo, 0, 1)

        layout.addWidget(exp_group)

        return panel

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Space"), self, self._toggle_play)
        QShortcut(QKeySequence("Left"), self, lambda: self._step_frame(-1))
        QShortcut(QKeySequence("Right"), self, lambda: self._step_frame(1))
        QShortcut(QKeySequence("Ctrl+O"), self, self._open_video)
        QShortcut(QKeySequence("Ctrl+E"), self, self._export)
        QShortcut(QKeySequence("B"), self, lambda: self.blur_draw_btn.setChecked(not self.blur_draw_btn.isChecked()))

    # ─── Video Loading ────────────────────────────────────────────────────────

    def _open_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Screen Recording", os.path.expanduser("~/Movies"),
            "Video Files (*.mp4 *.mov *.mkv *.avi *.m4v *.webm);;All Files (*)"
        )
        if path:
            self._load_video(path)

    def _load_video(self, path: str):
        self.status.showMessage(f"Loading {os.path.basename(path)}…")
        info = self._probe_video(path)
        if not info:
            QMessageBox.critical(self, "Error", "Could not read video file.")
            return

        self.session = EditSession(
            video_path=path,
            duration=info['duration'],
            width=info['width'],
            height=info['height'],
            fps=info['fps']
        )
        self.session.trim.end = info['duration']

        # Update UI
        self.info_labels["File"].setText(os.path.basename(path))
        self.info_labels["Duration"].setText(self._fmt_time(info['duration']))
        self.info_labels["Resolution"].setText(f"{info['width']}×{info['height']}")
        self.info_labels["FPS"].setText(f"{info['fps']:.2f}")

        self.timeline.duration = info['duration']
        self.timeline.trim_start = 0
        self.timeline.trim_end = info['duration']
        self.timeline.current_time = 0
        self.timeline.blur_regions = []
        self.timeline.thumbnails = []
        self.timeline.update()

        self._seek_to(0)

        # Enable buttons
        for btn in [self.play_btn, self.prev_frame_btn, self.next_frame_btn,
                    self.blur_draw_btn, self.export_btn,
                    self.mark_start_btn, self.mark_end_btn]:
            btn.setEnabled(True)

        self.trim_end_lbl.setText(self._fmt_time(info['duration']))
        self.status.showMessage(f"Loaded: {os.path.basename(path)} — {self._fmt_time(info['duration'])}")

        # Load thumbnails in background
        self._thumb_worker = ThumbnailWorker(path, info['duration'], 24)
        self._thumb_worker.ready.connect(self._on_thumbnails)
        self._thumb_worker.start()

    def _probe_video(self, path: str) -> Optional[dict]:
        try:
            cmd = [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate,duration",
                "-of", "json", path
            ]
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode()
            data = json.loads(out)
            stream = data['streams'][0]
            fps_parts = stream['r_frame_rate'].split('/')
            fps = float(fps_parts[0]) / float(fps_parts[1])
            # Try container duration
            dur = float(stream.get('duration', 0))
            if not dur:
                cmd2 = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", path]
                out2 = subprocess.check_output(cmd2, stderr=subprocess.DEVNULL).decode()
                dur = float(json.loads(out2)['format']['duration'])
            return {
                'width': int(stream['width']),
                'height': int(stream['height']),
                'fps': fps,
                'duration': dur
            }
        except Exception as e:
            print(f"Probe error: {e}")
            return None

    @pyqtSlot(list)

    def _probe_has_audio(self, path: str) -> bool:
        try:
            cmd = ["ffprobe", "-v", "error", "-select_streams", "a:0",
                   "-show_entries", "stream=codec_type", "-of", "json", path]
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode()
            data = json.loads(out)
            return len(data.get("streams", [])) > 0
        except:
            return False

    def _on_thumbnails(self, thumbs):
        self.timeline.thumbnails = thumbs
        self.timeline.update()

    # ─── Playback ─────────────────────────────────────────────────────────────

    def _extract_frame(self, t: float) -> Optional[QPixmap]:
        key = round(t, 2)
        if key in self._frame_cache:
            return self._frame_cache[key]
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                tmp = f.name
            cmd = [
                "ffmpeg", "-y", "-ss", f"{t:.4f}",
                "-i", self.session.video_path,
                "-vframes", "1",
                "-vf", "scale=1280:-1",
                "-q:v", "3", tmp
            ]
            subprocess.run(cmd, capture_output=True, timeout=4)
            pix = QPixmap(tmp)
            os.unlink(tmp)
            if not pix.isNull():
                # Keep cache small
                if len(self._frame_cache) > 60:
                    oldest = next(iter(self._frame_cache))
                    del self._frame_cache[oldest]
                self._frame_cache[key] = pix
                return pix
        except:
            pass
        return None

    def _seek_to(self, t: float):
        self.session.trim.start if hasattr(self.session, 'trim') else 0
        pix = self._extract_frame(t)
        if pix:
            self.canvas.frame_pixmap = pix
        self.canvas.current_time = t
        self.canvas.blur_regions = self.session.blur_regions
        self.canvas.update()
        self.timeline.current_time = t
        self.timeline.update()
        self._update_time_label(t)

    def _update_time_label(self, t: float):
        self.time_label.setText(
            f"{self._fmt_time(t)}  /  {self._fmt_time(self.session.duration)}"
        )

    def _toggle_play(self):
        if not self.session.video_path:
            return
        if self.playing:
            self._playback_timer.stop()
            self.playing = False
            self.play_btn.setText("▶")
        else:
            trim_end = self.session.trim.end if self.session.trim.end > 0 else self.session.duration
            if self.canvas.current_time >= trim_end:
                self.canvas.current_time = self.session.trim.start
            self.playing = True
            self.play_btn.setText("⏸")
            self._last_play_time = time.time()
            self._play_t = self.canvas.current_time
            self._playback_timer.start()

    def _advance_playback(self):
        now = time.time()
        dt = (now - self._last_play_time) * self.session.speed
        self._last_play_time = now
        self._play_t += dt
        trim_end = self.session.trim.end if self.session.trim.end > 0 else self.session.duration
        if self._play_t >= trim_end:
            self._play_t = trim_end
            self._playback_timer.stop()
            self.playing = False
            self.play_btn.setText("▶")
        self._seek_to(self._play_t)

    def _step_frame(self, direction: int):
        if not self.session.video_path:
            return
        step = 1.0 / max(1, self.session.fps)
        new_t = max(0, min(self.session.duration, self.canvas.current_time + direction * step))
        self._seek_to(new_t)

    @pyqtSlot(float)
    def _on_seek(self, t: float):
        self._seek_to(t)

    @pyqtSlot(float, float)
    def _on_trim_changed(self, start: float, end: float):
        self.session.trim.start = start
        self.session.trim.end = end
        self.trim_start_lbl.setText(f"{start:.2f}s")
        self.trim_end_lbl.setText(f"{end:.2f}s")

    def _reset_trim(self):
        self.session.trim.start = 0
        self.session.trim.end = self.session.duration
        self.timeline.trim_start = 0
        self.timeline.trim_end = self.session.duration
        self.timeline.update()
        self.trim_start_lbl.setText("0.00s")
        self.trim_end_lbl.setText(self._fmt_time(self.session.duration))

    # ─── Blur ─────────────────────────────────────────────────────────────────

    def _toggle_draw_mode(self, checked: bool):
        self.canvas.draw_mode = checked
        if checked:
            self.canvas.setCursor(Qt.CursorShape.CrossCursor)
            self.status.showMessage("Draw mode: drag on the video to place a blur region")
        else:
            self.canvas.setCursor(Qt.CursorShape.ArrowCursor)
            self.status.showMessage("Draw mode off")

    @pyqtSlot(float, float, float, float)
    def _on_blur_drawn(self, x: float, y: float, w: float, h: float):
        t = self.canvas.current_time
        br = self.session.add_blur(x, y, w, h, t, min(t + 5.0, self.session.duration))
        self._refresh_canvas()
        self.blur_draw_btn.setChecked(False)
        self.status.showMessage(f"Blur #{br.id+1} added — adjust start/end in the panel →")

    def _mark_blur_start(self):
        if self.canvas.selected_blur_id is not None:
            for br in self.session.blur_regions:
                if br.id == self.canvas.selected_blur_id:
                    br.start_time = self.canvas.current_time
                    self._refresh_canvas()
                    break

    def _mark_blur_end(self):
        if self.canvas.selected_blur_id is not None:
            for br in self.session.blur_regions:
                if br.id == self.canvas.selected_blur_id:
                    br.end_time = self.canvas.current_time
                    self._refresh_canvas()
                    break

    def _remove_blur(self, bid: int):
        self.session.remove_blur(bid)
        self._refresh_canvas()

    @pyqtSlot(int)
    def _on_blur_selected(self, bid: int):
        self.canvas.selected_blur_id = bid
        self.canvas.update()

    def _refresh_canvas(self):
        self.canvas.blur_regions = self.session.blur_regions
        self.canvas.update()
        self.timeline.blur_regions = self.session.blur_regions
        self.timeline.update()
        self.blur_panel.refresh(self.session, self.canvas.current_time)

    # ─── Speed ────────────────────────────────────────────────────────────────

    def _set_speed(self, speed: float):
        self.session.speed = speed
        self.speed_label.setText(f"{speed:.2f}×")
        self.speed_slider.blockSignals(True)
        self.speed_slider.setValue(int(speed * 100))
        self.speed_slider.blockSignals(False)

    # ─── Export ───────────────────────────────────────────────────────────────

    def _export(self):
        if not self.session.video_path:
            return
        src = Path(self.session.video_path)
        default = str(src.parent / (src.stem + "_edited.mp4"))
        out, _ = QFileDialog.getSaveFileName(
            self, "Export Video", default, "MP4 Video (*.mp4)"
        )
        if not out:
            return

        # CRF from combo
        crf_map = {"High (CRF 18)": 18, "Medium (CRF 23)": 23, "Low (CRF 28)": 28}
        crf = crf_map.get(self.quality_combo.currentText(), 18)

        self.progress_dlg = QProgressDialog("Preparing…", "Cancel", 0, 100, self)
        self.progress_dlg.setWindowTitle("Exporting…")
        self.progress_dlg.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress_dlg.setMinimumDuration(0)
        self.progress_dlg.setValue(0)

        self._export_worker = ExportWorker(self.session, out)
        self._export_worker.has_audio = self._probe_has_audio(self.session.video_path)
        self._export_worker.crf = crf
        self._export_worker.progress.connect(
            lambda pct, msg: (self.progress_dlg.setValue(pct), self.progress_dlg.setLabelText(msg))
        )
        self._export_worker.finished.connect(self._on_export_done)
        self.progress_dlg.canceled.connect(self._export_worker.terminate)
        self._export_worker.start()

    @pyqtSlot(bool, str)
    def _on_export_done(self, success: bool, info: str):
        self.progress_dlg.close()
        if success:
            msg = QMessageBox(self)
            msg.setWindowTitle("Export Complete")
            msg.setText(f"Video exported successfully!")
            msg.setInformativeText(info)
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.exec()
            self.status.showMessage(f"Exported: {info}")
        else:
            QMessageBox.critical(self, "Export Failed", info)

    # ─── Utilities ────────────────────────────────────────────────────────────

    @staticmethod
    def _fmt_time(t: float) -> str:
        mins = int(t) // 60
        secs = int(t) % 60
        cs = int((t % 1) * 100)
        return f"{mins}:{secs:02d}.{cs:02d}"

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                self._load_video(path)
                break

    def closeEvent(self, event):
        if self._export_worker and self._export_worker.isRunning():
            self._export_worker.terminate()
        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.terminate()
        event.accept()


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("ScreenCut")
    app.setOrganizationName("ScreenCut")
    window = ScreenCut()
    window.show()
    sys.exit(app.exec())
