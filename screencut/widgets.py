"""Preview, timeline, and privacy-region widgets."""

from typing import Optional, List, Tuple
from PyQt6.QtWidgets import (
    QWidget,
    QSizePolicy,
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QScrollArea,
    QPushButton,
    QDoubleSpinBox,
    QComboBox,
    QGraphicsScene,
    QGraphicsPixmapItem,
    QGraphicsBlurEffect,
)
from PyQt6.QtCore import Qt, QRect, QRectF, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QFont, QPixmap
from .models import BlurRegion, EditSession
from .annotations import paint_annotation


class VideoCanvas(QWidget):
    """Displays video frame + overlay for drawing blur regions."""

    annotation_added = pyqtSignal(str, float, float, float, float)
    annotation_selected = pyqtSignal(int)
    blur_added = pyqtSignal(float, float, float, float)  # x,y,w,h normalized

    def __init__(self):
        super().__init__()
        self.frame_pixmap: Optional[QPixmap] = None
        self.blur_regions: List[BlurRegion] = []
        self.current_time: float = 0.0
        self.selected_blur_id: Optional[int] = None
        self.annotation_tool = None
        self.annotations = []
        self.selected_annotation_id = None
        self.draw_mode = False  # True = drawing new blur
        self._drag_start: Optional[QPoint] = None
        self._drag_rect: Optional[QRect] = None
        self.video_rect = QRect()
        self.setMinimumSize(320, 180)
        self._blur_cache = {}
        self.setAccessibleName("Video preview")
        self.setToolTip("Enable Draw region, then drag inside the video to obscure an area.")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    def set_frame(self, pixmap: QPixmap):
        self.frame_pixmap = pixmap
        self._blur_cache.clear()
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
        p.fillRect(self.rect(), QColor("#0b111b"))

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
                is_sel = br.id == self.selected_blur_id

                if getattr(br, "mode", "blur") == "redact":
                    p.fillRect(rect, QColor("#000000"))
                else:
                    # Qt's blur effect is rendered into an offscreen pixmap. Scale
                    # the radius with the preview so it follows source resolution.
                    crop = self.frame_pixmap.copy(
                        int(br.x * self.frame_pixmap.width()),
                        int(br.y * self.frame_pixmap.height()),
                        max(1, int(br.w * self.frame_pixmap.width())),
                        max(1, int(br.h * self.frame_pixmap.height())),
                    )
                    key = (
                        self.frame_pixmap.cacheKey(),
                        br.x,
                        br.y,
                        br.w,
                        br.h,
                        rect.size().width(),
                        rect.size().height(),
                    )
                    blurred = self._blur_cache.get(key)
                    if blurred is None and not crop.isNull():
                        source = crop.scaled(
                            rect.size(),
                            Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        scene = QGraphicsScene()
                        item = QGraphicsPixmapItem(source)
                        effect = QGraphicsBlurEffect()
                        effect.setBlurRadius(
                            max(1.0, 40.0 * self.video_rect.width() / self.frame_pixmap.width())
                        )
                        effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
                        item.setGraphicsEffect(effect)
                        scene.addItem(item)
                        blurred = QPixmap(source.size())
                        blurred.fill(Qt.GlobalColor.black)
                        painter = QPainter(blurred)
                        scene.render(painter, QRectF(blurred.rect()), QRectF(source.rect()))
                        painter.end()
                        if len(self._blur_cache) > 32:
                            self._blur_cache.clear()
                        self._blur_cache[key] = blurred
                    if blurred is not None:
                        p.drawPixmap(rect, blurred)

                # Border
                pen = QPen(QColor("#5eead4") if is_sel else QColor("#94a3b8"), 2)
                pen.setStyle(Qt.PenStyle.DashLine if not is_sel else Qt.PenStyle.SolidLine)
                p.setPen(pen)
                p.drawRect(rect)

                # Label
                p.setPen(QPen(QColor(255, 255, 255, 200)))
                f = QFont("Menlo", 11)
                f.setBold(True)
                p.setFont(f)
                label = br.label or f"Blur #{br.id + 1}"
                p.drawText(
                    rect.adjusted(4, 3, 0, 0),
                    Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                    label,
                )

            p.save()
            p.translate(self.video_rect.topLeft())
            p.scale(
                self.video_rect.width() / self.frame_pixmap.width(),
                self.video_rect.height() / self.frame_pixmap.height(),
            )
            for item in self.annotations:
                if item.start_time <= self.current_time < item.end_time:
                    paint_annotation(p, item, self.frame_pixmap.width(), self.frame_pixmap.height())
            p.restore()
            if self.selected_annotation_id is not None:
                for item in self.annotations:
                    if (
                        item.id == self.selected_annotation_id
                        and item.start_time <= self.current_time < item.end_time
                    ):
                        p.setPen(QPen(QColor("#5eead4"), 1, Qt.PenStyle.DashLine))
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawRect(
                            QRect(
                                self._to_screen(item.x, item.y), self._to_screen(item.x2, item.y2)
                            )
                            .normalized()
                            .adjusted(-3, -3, 3, 3)
                        )

            # Active drawing rect
            if self._drag_rect and (self.draw_mode or self.annotation_tool):
                p.fillRect(self._drag_rect, QColor(0, 200, 100, 50))
                pen = QPen(QColor(0, 255, 100), 2)
                pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.drawRect(self._drag_rect)

        else:
            # Empty state
            p.setPen(QPen(QColor("#333")))
            p.drawRect(self.rect().adjusted(1, 1, -1, -1))
            p.setPen(QPen(QColor("#94a3b8")))
            f = QFont("SF Pro Display", 16)
            p.setFont(f)
            p.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Your next cut starts here\n\nOpen or drop a video to begin\nTrim · Adjust speed · Protect private details",
            )

    def mousePressEvent(self, event):
        if not self.frame_pixmap or event.button() != Qt.MouseButton.LeftButton:
            return
        if self.draw_mode or self.annotation_tool:
            if not self.video_rect.contains(event.pos()):
                return
            self._drag_start = event.pos()
            self._drag_rect = None
        else:
            nx, ny = self._to_normalized(event.pos())
            for item in reversed(self.annotations):
                if (
                    item.start_time <= self.current_time < item.end_time
                    and min(item.x, item.x2) - 0.015 <= nx <= max(item.x, item.x2) + 0.015
                    and min(item.y, item.y2) - 0.015 <= ny <= max(item.y, item.y2) + 0.015
                ):
                    self.selected_annotation_id = item.id
                    self.selected_blur_id = None
                    self.annotation_selected.emit(item.id)
                    self.update()
                    return
            self.selected_annotation_id = None
            # Select blur
            nx, ny = self._to_normalized(event.pos())
            for br in reversed(self.blur_regions):
                if (
                    br.start_time <= self.current_time <= br.end_time
                    and br.x <= nx <= br.x + br.w
                    and br.y <= ny <= br.y + br.h
                ):
                    self.selected_blur_id = br.id
                    self.update()
                    return
            self.selected_blur_id = None
            self.update()

    def mouseMoveEvent(self, event):
        if (self.draw_mode or self.annotation_tool) and self._drag_start:
            p = event.pos()
            self._drag_rect = QRect(self._drag_start, p).normalized()
            self.update()

    def mouseReleaseEvent(self, event):
        if self.annotation_tool and self._drag_start:
            x, y = self._to_normalized(self._drag_start)
            x2, y2 = self._to_normalized(event.pos())
            kind = self.annotation_tool
            if kind != "arrow":
                x, x2 = sorted((x, x2))
                y, y2 = sorted((y, y2))
            valid = (
                (abs(x2 - x) + abs(y2 - y) > 0.01)
                if kind == "arrow"
                else (x2 - x > 0.01 and y2 - y > 0.01)
            )
            self._drag_start = None
            self._drag_rect = None
            if valid:
                self.annotation_added.emit(kind, x, y, x2, y2)
            self.update()
            return
        if self.draw_mode and self._drag_start and self._drag_rect:
            r = self._drag_rect.intersected(self.video_rect)
            if r.width() > 10 and r.height() > 10:
                nx, ny = self._to_normalized(r.topLeft())
                nw = r.width() / self.video_rect.width()
                nh = r.height() / self.video_rect.height()
                self.blur_added.emit(nx, ny, nw, nh)
            self._drag_start = None
            self._drag_rect = None
            self.update()
        else:
            self._drag_start = None
            self._drag_rect = None


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
        self.annotations = []
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
        p.fillRect(self.rect(), QColor("#111b29"))

        if self.duration <= 0:
            p.setPen(QPen(QColor("#444")))
            p.setFont(QFont("SF Pro Text", 11))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Load a video to see timeline")
            return

        M = self.MARGIN
        TH = self.THUMB_H
        RH = self.RULER_H

        # Thumbnail strip background
        strip_rect = QRect(M, 0, W - 2 * M, TH)
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
        pen = QPen(QColor("#5eead4"), 2)
        p.setPen(pen)
        p.drawLine(tx_s, 0, tx_s, TH)
        p.drawLine(tx_e, 0, tx_e, TH)
        # Handle caps
        p.setBrush(QBrush(QColor("#5eead4")))
        p.drawRect(QRect(tx_s - 5, 0, 10, 16))
        p.drawRect(QRect(tx_e - 5, 0, 10, 16))

        # Blur bands under ruler
        ruler_y = TH + 2
        for br in self.blur_regions:
            bx_s = self._t_to_x(br.start_time)
            bx_e = self._t_to_x(br.end_time)
            p.fillRect(QRect(bx_s, ruler_y, bx_e - bx_s, RH - 4), QColor(255, 200, 0, 160))
            p.setPen(QPen(QColor(255, 220, 50)))
            p.setFont(QFont("Menlo", 11))
            label = br.label or f"B{br.id + 1}"
            p.drawText(
                QRect(bx_s + 2, ruler_y, bx_e - bx_s - 4, RH - 4),
                Qt.AlignmentFlag.AlignVCenter,
                label,
            )

        for item in self.annotations:
            x1, x2 = self._t_to_x(item.start_time), self._t_to_x(item.end_time)
            color = QColor(item.color)
            color.setAlpha(180)
            p.fillRect(QRect(x1, TH + RH + 3, max(1, x2 - x1), 8), color)

        # Ruler ticks
        p.setPen(QPen(QColor("#94a3b8")))
        p.setFont(QFont("Menlo", 11))
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
        diamond = [QPoint(cx, 0), QPoint(cx + 6, 8), QPoint(cx, 16), QPoint(cx - 6, 8)]
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
            self._drag = "trim_start"
        elif abs(x - tx_e) < 10:
            self._drag = "trim_end"
        elif abs(x - cx) < 8:
            self._drag = "playhead"
        else:
            self._drag = "playhead"
            t = self._x_to_t(x)
            self.current_time = t
            self.seek.emit(t)
            self.update()

    def mouseMoveEvent(self, event):
        if not self._drag or self.duration <= 0:
            return
        t = self._x_to_t(event.pos().x())
        trim_end = self.trim_end if self.trim_end > 0 else self.duration
        if self._drag == "playhead":
            self.current_time = t
            self.seek.emit(t)
        elif self._drag == "trim_start":
            self.trim_start = max(0, min(t, trim_end - min(0.01, self.duration)))
            self.trim_changed.emit(self.trim_start, self.trim_end)
        elif self._drag == "trim_end":
            self.trim_end = max(self.trim_start + min(0.01, self.duration), min(t, self.duration))
            self.trim_changed.emit(self.trim_start, self.trim_end)
        self.update()

    def mouseReleaseEvent(self, event):
        self._drag = None


class BlurPanel(QWidget):
    region_selected = pyqtSignal(int)
    region_removed = pyqtSignal(int)
    region_updated = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.session = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.container = QWidget()
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setContentsMargins(0, 0, 4, 0)
        self.vbox.setSpacing(10)
        self.vbox.addStretch()
        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll)

    def refresh(self, session: EditSession, current_time: float):
        self.session = session
        while self.vbox.count() > 1:
            item = self.vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not session.blur_regions:
            empty = QLabel(
                "No regions yet\n\nChoose Draw region and drag over\nanything you want to hide."
            )
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            self.vbox.insertWidget(0, empty)
        for region in session.blur_regions:
            self.vbox.insertWidget(
                self.vbox.count() - 1, self._make_card(region, session.duration, current_time)
            )

    def _make_card(self, br, duration, current_time):
        card = QFrame()
        card.setObjectName("regionCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        header = QHBoxLayout()
        select = QPushButton(br.label or f"Region {br.id + 1}")
        select.setObjectName("regionTitle")
        select.setToolTip("Select this region in the preview")
        select.clicked.connect(lambda: self.region_selected.emit(br.id))
        header.addWidget(select, 1)
        remove = QPushButton("×")
        remove.setFixedWidth(32)
        remove.setAccessibleName(f"Remove region {br.id + 1}")
        remove.setToolTip("Remove region")
        remove.clicked.connect(lambda: self.region_removed.emit(br.id))
        header.addWidget(remove)
        layout.addLayout(header)
        mode = QComboBox()
        mode.addItem("Gaussian blur", "blur")
        mode.addItem("Opaque redaction", "redact")
        mode.setCurrentIndex(1 if getattr(br, "mode", "blur") == "redact" else 0)
        mode.setAccessibleName(f"Region {br.id + 1} effect")
        mode.setToolTip(
            "Opaque redaction completely covers the region. Blur preview is approximate."
        )

        def change_mode():
            br.mode = mode.currentData()
            self.region_updated.emit()

        mode.currentIndexChanged.connect(change_mode)
        layout.addWidget(mode)
        grid = QGridLayout()
        start = QDoubleSpinBox()
        end = QDoubleSpinBox()
        for spin, label, value in ((start, "Start", br.start_time), (end, "End", br.end_time)):
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setRange(0, duration)
            spin.setValue(value)
            spin.setSuffix(" s")
            spin.setKeyboardTracking(False)
            spin.setAccessibleName(f"Region {br.id + 1} {label.lower()} time")
        gap = min(0.001, duration)
        start.setMaximum(max(0, br.end_time - gap))
        end.setMinimum(min(duration, br.start_time + gap))

        def change_start(value):
            br.start_time = value
            end.setMinimum(min(duration, value + gap))
            self.region_updated.emit()

        def change_end(value):
            br.end_time = value
            start.setMaximum(max(0, value - gap))
            self.region_updated.emit()

        start.valueChanged.connect(change_start)
        end.valueChanged.connect(change_end)
        grid.addWidget(QLabel("Start"), 0, 0)
        grid.addWidget(start, 0, 1)
        grid.addWidget(QLabel("End"), 1, 0)
        grid.addWidget(end, 1, 1)
        layout.addLayout(grid)
        return card
