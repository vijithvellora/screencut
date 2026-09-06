"""Shared source-resolution painting for preview and exported annotations."""

import math
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF


def paint_annotation(painter, item, width, height):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setOpacity(item.opacity)
    color = QColor(item.color)
    start = QPointF(item.x * width, item.y * height)
    end = QPointF(item.x2 * width, item.y2 * height)
    rect = QRectF(start, end).normalized()
    if item.kind == "highlight":
        painter.fillRect(rect, color)
    elif item.kind == "text":
        font = QFont("Arial")
        font.setPixelSize(max(1, round(item.size * height)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(color)
        painter.setClipRect(rect)
        painter.drawText(
            rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
            item.text,
        )
    else:
        stroke = max(2, item.size * height / 7)
        painter.setPen(QPen(color, stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        length = math.hypot(end.x() - start.x(), end.y() - start.y())
        head = min(length * 0.4, max(stroke * 4, height * 0.025))
        base = QPointF(end.x() - head * math.cos(angle), end.y() - head * math.sin(angle))
        painter.drawLine(start, base)
        points = [
            end,
            QPointF(
                base.x() + head * 0.45 * math.sin(angle), base.y() - head * 0.45 * math.cos(angle)
            ),
            QPointF(
                base.x() - head * 0.45 * math.sin(angle), base.y() + head * 0.45 * math.cos(angle)
            ),
        ]
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygonF(points))
    painter.restore()


def annotation_image(item, width, height):
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    paint_annotation(painter, item, width, height)
    painter.end()
    return image
