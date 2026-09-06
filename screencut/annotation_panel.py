"""Annotation creation and editing controls."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QComboBox,
    QFormLayout,
    QDoubleSpinBox,
    QTextEdit,
    QColorDialog,
    QLabel,
    QScrollArea,
)


class AnnotationPanel(QWidget):
    add_requested = pyqtSignal(str)
    selected = pyqtSignal(int)
    changed = pyqtSignal()
    removed = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.session = None
        self.item = None
        self._refreshing = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 10, 0, 0)
        row = QHBoxLayout()
        self.add_buttons = []
        for kind in ("text", "arrow", "highlight"):
            button = QPushButton(kind.title())
            button.setCheckable(True)
            button.setToolTip(f"Drag on the preview to place a {kind}")
            button.clicked.connect(lambda _, k=kind: self.add_requested.emit(k))
            row.addWidget(button)
            self.add_buttons.append(button)
        root.addLayout(row)
        hint = QLabel(
            "Choose a tool, then drag on the preview.\nSelect an annotation to edit it below."
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        hint.setMinimumHeight(34)
        root.addWidget(hint)
        self.chooser = QComboBox()
        self.chooser.setAccessibleName("Selected annotation")
        self.chooser.currentIndexChanged.connect(self._select)
        root.addWidget(self.chooser)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(80)
        root.addWidget(scroll, 1)
        self.properties = QWidget()
        scroll.setWidget(self.properties)
        form = QFormLayout(self.properties)
        form.setContentsMargins(4, 4, 8, 4)
        self.text = QTextEdit()
        self.text.setAcceptRichText(False)
        self.text.setFixedHeight(82)
        self.text.setAccessibleName("Annotation text")
        self.text.textChanged.connect(self._text_changed)
        form.addRow("Text", self.text)
        self.color = QPushButton("Color")
        self.color.clicked.connect(self._pick_color)
        form.addRow("Color", self.color)
        self.spins = {}
        for name, label, minimum, maximum, multiplier, suffix in (
            ("start_time", "Start", 0, 1, 1, " s"),
            ("end_time", "End", 0, 1, 1, " s"),
            ("size", "Size", 1, 25, 100, "%"),
            ("opacity", "Opacity", 5, 100, 100, "%"),
            ("x", "Start X", 0, 100, 100, "%"),
            ("y", "Start Y", 0, 100, 100, "%"),
            ("x2", "End X", 0, 100, 100, "%"),
            ("y2", "End Y", 0, 100, 100, "%"),
        ):
            spin = QDoubleSpinBox()
            spin.setRange(minimum, maximum)
            spin.setDecimals(3 if multiplier == 1 else 1)
            spin.setSuffix(suffix)
            spin.setKeyboardTracking(False)
            spin.setAccessibleName(f"Annotation {label.lower()}")
            spin.valueChanged.connect(
                lambda value, n=name, m=multiplier: self._change(n, value / m)
            )
            form.addRow(label, spin)
            self.spins[name] = spin
        delete = QPushButton("Delete annotation")
        delete.clicked.connect(lambda: self.removed.emit(self.item.id) if self.item else None)
        form.addRow(delete)

    def refresh(self, session, selected_id=None):
        self._refreshing = True
        self.session = session
        previous = self.item.id if self.item else None
        wanted = selected_id if selected_id is not None else previous
        self.chooser.clear()
        for item in session.annotations:
            self.chooser.addItem(f"{item.kind.title()} {item.id + 1}", item.id)
        index = self.chooser.findData(wanted)
        self.chooser.setCurrentIndex(max(0, index) if session.annotations else -1)
        self.item = next(
            (a for a in session.annotations if a.id == self.chooser.currentData()), None
        )
        self.properties.setEnabled(self.item is not None)
        for button in self.add_buttons:
            button.setEnabled(bool(session.video_path))
        if self.item:
            a = self.item
            self.text.setEnabled(a.kind == "text")
            self.spins["size"].setEnabled(a.kind != "highlight")
            if self.text.toPlainText() != a.text:
                self.text.setPlainText(a.text)
            self.color.setText(a.color)
            self.color.setStyleSheet(f"border: 2px solid {a.color};")
            self.spins["start_time"].setRange(0, max(0, a.end_time - 0.001))
            self.spins["end_time"].setRange(a.start_time + 0.001, session.duration)
            for name, spin in self.spins.items():
                if name in ("x", "y", "x2", "y2"):
                    spin.setRange(0, 100)
                value = getattr(a, name) * (1 if name.endswith("_time") else 100)
                spin.setValue(value)
            if a.kind != "arrow":
                for low, high in (("x", "x2"), ("y", "y2")):
                    self.spins[low].setMaximum(getattr(a, high) * 100 - 0.1)
                    self.spins[high].setMinimum(getattr(a, low) * 100 + 0.1)
        self._refreshing = False

    def _select(self):
        if not self._refreshing and self.chooser.currentData() is not None:
            self.selected.emit(self.chooser.currentData())
            self.refresh(self.session, self.chooser.currentData())

    def _change(self, name, value):
        if self._refreshing or not self.item:
            return
        old = getattr(self.item, name)
        setattr(self.item, name, value)
        if self.item.x == self.item.x2 and self.item.y == self.item.y2:
            setattr(self.item, name, old)
        self.changed.emit()

    def _text_changed(self):
        if not self._refreshing and self.item:
            self.item.text = self.text.toPlainText()[:2000]
            self.changed.emit()

    def _pick_color(self):
        if self.item:
            color = QColorDialog.getColor(QColor(self.item.color), self, "Annotation color")
            if color.isValid():
                self.item.color = color.name()
                self.changed.emit()
