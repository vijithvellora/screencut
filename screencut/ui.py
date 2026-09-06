"""Layout and presentation for the ScreenCut editor."""

from PyQt6.QtWidgets import (
    QWidget,
    QTabWidget,
    QApplication,
    QLineEdit,
    QTextEdit,
    QAbstractSpinBox,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QFrame,
    QPushButton,
    QLabel,
    QSplitter,
    QStatusBar,
    QGroupBox,
    QComboBox,
    QSlider,
    QSizePolicy,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QShortcut, QKeySequence
from .widgets import VideoCanvas, Timeline, BlurPanel
from .annotation_panel import AnnotationPanel


class EditorUIMixin:
    def _setup_style(self):
        self.setStyleSheet("""
            QWidget { background: #111b29; color: #e2e8f0; font-family: 'SF Pro Text'; font-size: 12px; }
            QMainWindow { background: #111b29; }
            QLabel { background: transparent; }
            QLabel#muted { color: #94a3b8; line-height: 1.5; }
            QLabel#sectionTitle { color: #e2e8f0; font-size: 14px; font-weight: 600; }
            QFrame#toolbar { background: #172334; border-bottom: 1px solid #2c3b50; }
            QFrame#regionCard { background: #182638; border: 1px solid #334155; border-radius: 9px; }
            QPushButton { background: #233247; border: 1px solid #3a4b61; border-radius: 6px; padding: 7px 10px; min-height: 18px; }
            QPushButton:hover { background: #30435c; border-color: #94a3b8; }
            QPushButton:pressed { background: #172334; }
            QPushButton:checked { background: #134e4a; border-color: #5eead4; color: #99f6e4; }
            QPushButton:disabled { color: #64748b; border-color: #29374a; background: #1b293c; }
            QPushButton#primary { background: #5eead4; border-color: #5eead4; color: #082f2b; font-weight: 600; }
            QPushButton#primary:hover { background: #99f6e4; }
            QPushButton#primary:disabled { background: #25433f; border-color: #25433f; color: #87aaa3; }
            QPushButton#regionTitle { text-align: left; border: none; background: transparent; padding-left: 0; font-weight: 600; }
            QGroupBox { border: 1px solid #334155; border-radius: 8px; margin-top: 14px; padding: 12px 8px 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #cbd5e1; }
            QComboBox, QDoubleSpinBox { background: #0f1927; border: 1px solid #45566d; border-radius: 5px; padding: 6px; min-height: 20px; }
            QSlider::groove:horizontal { height: 4px; background: #334155; border-radius: 2px; }
            QSlider::handle:horizontal { background: #5eead4; width: 14px; margin: -5px 0; border-radius: 7px; }
            QSlider::sub-page:horizontal { background: #5eead4; }
            QSplitter::handle { background: #2c3b50; width: 4px; }
            QStatusBar { color: #94a3b8; background: #0e1724; font-size: 11px; }
            QScrollArea { border: none; }
            QTabWidget::pane { border: none; }
            QTabBar::tab { background: #172334; color: #94a3b8; padding: 8px 14px; }
            QTabBar::tab:selected { color: #5eead4; border-bottom: 2px solid #5eead4; }
            QTextEdit { background: #0f1927; border: 1px solid #45566d; border-radius: 5px; padding: 5px; }
            QToolTip { color: #f1f5f9; background: #233247; border: 1px solid #64748b; padding: 5px; }
        """)

    def _button(self, text, callback, tooltip, enabled=True):
        button = QPushButton(text)
        button.clicked.connect(callback)
        button.setToolTip(tooltip)
        button.setAccessibleName(text)
        button.setEnabled(enabled)
        return button

    def _setup_ui(self):
        self.setWindowTitle("ScreenCut")
        self.resize(1280, 820)
        self.setMinimumSize(960, 640)
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_toolbar())
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        left = QWidget()
        layout = QVBoxLayout(left)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(12)
        preview_header = QHBoxLayout()
        label = QLabel("PREVIEW")
        label.setObjectName("muted")
        preview_header.addWidget(label)
        preview_header.addStretch()
        self.project_label = QLabel("Untitled project")
        self.project_label.setObjectName("muted")
        self.project_label.setMaximumWidth(380)
        preview_header.addWidget(self.project_label)
        layout.addLayout(preview_header)
        self.canvas = VideoCanvas()
        self.canvas.blur_added.connect(self._on_blur_drawn)
        self.canvas.annotation_added.connect(self._on_annotation_drawn)
        self.canvas.annotation_selected.connect(self._select_annotation)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(self._build_playback_controls())
        self.timeline = Timeline()
        self.timeline.setToolTip(
            "Drag the teal handles to trim. Click or drag anywhere else to seek."
        )
        self.timeline.setAccessibleName("Video timeline")
        self.timeline.seek.connect(self._on_seek)
        self.timeline.trim_changed.connect(self._on_trim_changed)
        layout.addWidget(self.timeline)
        footer = QHBoxLayout()
        self.time_label = QLabel("0:00.00  /  0:00.00")
        footer.addWidget(self.time_label)
        footer.addStretch()
        help_label = QLabel("Space  Play / pause     ← →  Frame")
        help_label.setObjectName("muted")
        footer.addWidget(help_label)
        layout.addLayout(footer)
        self.splitter.addWidget(left)
        inspector = self._build_right_panel()
        inspector.setMinimumWidth(280)
        self.splitter.addWidget(inspector)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setSizes([930, 330])
        root.addWidget(self.splitter, 1)
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready · Open a recording to begin")

    def _build_toolbar(self):
        bar = QFrame()
        bar.setObjectName("toolbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 14, 20, 14)
        logo = QLabel("ScreenCut")
        logo.setStyleSheet("font-size: 21px; font-weight: 600; color: #5eead4;")
        layout.addWidget(logo)
        layout.addSpacing(14)
        self.open_btn = self._button("Open video", self._open_video, "Open a video · ⌘O")
        layout.addWidget(self.open_btn)
        layout.addWidget(
            self._button(
                "Open project", self._open_project, "Restore a saved editing project · ⌘⇧O"
            )
        )
        self.save_project_btn = self._button(
            "Save", self._save_project, "Save your editable project · ⌘S", False
        )
        layout.addWidget(self.save_project_btn)
        self.undo_btn = self._button("Undo", self._undo, "Undo last edit · ⌘Z", False)
        self.redo_btn = self._button("Redo", self._redo, "Redo edit · ⌘⇧Z", False)
        layout.addWidget(self.undo_btn)
        layout.addWidget(self.redo_btn)
        layout.addStretch()
        self.export_btn = self._button(
            "Export video", self._export, "Render trim, speed, and privacy regions · ⌘E", False
        )
        self.export_btn.setObjectName("primary")
        layout.addWidget(self.export_btn)
        return bar

    def _build_playback_controls(self):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        self.prev_frame_btn = self._button(
            "‹", lambda: self._step_frame(-1), "Previous frame · Left arrow", False
        )
        self.play_btn = self._button("▶", self._toggle_play, "Play / pause · Space", False)
        self.next_frame_btn = self._button(
            "›", lambda: self._step_frame(1), "Next frame · Right arrow", False
        )
        for button in (self.prev_frame_btn, self.play_btn, self.next_frame_btn):
            button.setMinimumWidth(42)
            row.addWidget(button)
        row.addStretch()
        self.mark_start_btn = self._button(
            "Set start [", self._mark_blur_start, "Set selected effect start to the playhead", False
        )
        self.mark_end_btn = self._button(
            "Set end ]", self._mark_blur_end, "Set selected effect end to the playhead", False
        )
        row.addWidget(self.mark_start_btn)
        row.addWidget(self.mark_end_btn)
        return widget

    def _build_right_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 18, 16, 14)
        layout.setSpacing(12)
        title = QLabel("Edit recording")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.info_group = QGroupBox("Source")
        info = QGridLayout(self.info_group)
        self.info_labels = {}
        for i, key in enumerate(("File", "Duration", "Resolution", "FPS")):
            label = QLabel(key)
            label.setObjectName("muted")
            value = QLabel("—")
            value.setWordWrap(True)
            value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            info.addWidget(label, i, 0)
            info.addWidget(value, i, 1)
            self.info_labels[key] = value
        source_toggle = QPushButton("Source details ▸")
        source_toggle.setCheckable(True)
        source_toggle.setToolTip("Show recording filename, duration, resolution, and frame rate")
        source_toggle.toggled.connect(self.info_group.setVisible)
        source_toggle.toggled.connect(
            lambda expanded: source_toggle.setText(
                "Source details ▾" if expanded else "Source details ▸"
            )
        )
        layout.addWidget(source_toggle)
        self.info_group.setVisible(False)
        layout.addWidget(self.info_group)
        edits = QGroupBox("Trim & speed")
        grid = QGridLayout(edits)
        self.trim_start_lbl = QLabel("0.00s")
        self.trim_end_lbl = QLabel("—")
        grid.addWidget(self.trim_start_lbl, 0, 0)
        grid.addWidget(QLabel("→"), 0, 1)
        grid.addWidget(self.trim_end_lbl, 0, 2)
        grid.addWidget(self._button("Reset", self._reset_trim, "Restore full video length"), 0, 3)
        self.speed_label = QLabel("1.0×")
        grid.addWidget(self.speed_label, 1, 0)
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0.25×", "0.5×", "1.0×", "1.5×", "2.0×", "3.0×", "4.0×", "8.0×"])
        self.speed_combo.setCurrentText("1.0×")
        self.speed_combo.setAccessibleName("Playback and export speed")
        self.speed_combo.currentTextChanged.connect(lambda text: self._set_speed(float(text[:-1])))
        grid.addWidget(self.speed_combo, 1, 2, 1, 2)
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(25, 800)
        self.speed_slider.setValue(100)
        self.speed_slider.setAccessibleName("Playback and export speed")
        self.speed_slider.valueChanged.connect(lambda value: self._set_speed(value / 100))
        grid.addWidget(self.speed_slider, 2, 0, 1, 4)
        layout.addWidget(edits)
        self.effect_tabs = QTabWidget()
        privacy = QWidget()
        privacy_layout = QVBoxLayout(privacy)
        privacy_layout.setContentsMargins(0, 10, 0, 0)
        heading = QHBoxLayout()
        label = QLabel("Privacy regions")
        label.setObjectName("sectionTitle")
        heading.addWidget(label)
        heading.addStretch()
        self.blur_draw_btn = QPushButton("Draw region")
        self.blur_draw_btn.setCheckable(True)
        self.blur_draw_btn.setEnabled(False)
        self.blur_draw_btn.setToolTip("Draw a privacy region on the preview · B")
        self.blur_draw_btn.toggled.connect(self._toggle_draw_mode)
        heading.addWidget(self.blur_draw_btn)
        privacy_layout.addLayout(heading)
        self.blur_panel = BlurPanel()
        self.blur_panel.region_selected.connect(self._on_blur_selected)
        self.blur_panel.region_removed.connect(self._remove_blur)
        self.blur_panel.region_updated.connect(
            getattr(self, "_on_region_updated", self._refresh_canvas)
        )
        self.blur_panel.refresh(self.session, 0)
        privacy_layout.addWidget(self.blur_panel, 1)
        self.effect_tabs.addTab(privacy, "Privacy")
        self.annotation_panel = AnnotationPanel()
        self.annotation_panel.add_requested.connect(self._start_annotation)
        self.annotation_panel.selected.connect(self._select_annotation)
        self.annotation_panel.changed.connect(self._on_region_updated)
        self.annotation_panel.removed.connect(self._remove_annotation)
        self.annotation_panel.refresh(self.session)
        self.effect_tabs.addTab(self.annotation_panel, "Annotations")
        layout.addWidget(self.effect_tabs, 1)
        export = QHBoxLayout()
        export.addWidget(QLabel("Export quality"))
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["High", "Medium", "Low"])
        self.quality_combo.setCurrentText("Medium")
        self.quality_combo.setAccessibleName("Export quality")
        export.addWidget(self.quality_combo, 1)
        layout.addLayout(export)
        self.export_duration_label = QLabel("Output duration · —")
        self.export_duration_label.setObjectName("muted")
        layout.addWidget(self.export_duration_label)
        return panel

    def _update_export_summary(self):
        duration = self.session.effective_duration()
        self.export_duration_label.setText(f"Output duration · {duration:.2f}s")

    def _setup_shortcuts(self):
        self._editing_shortcuts = []
        QShortcut(QKeySequence("Escape"), self, self._cancel_draw)
        for key, callback in (
            ("Space", self._toggle_play),
            ("Left", lambda: self._step_frame(-1)),
            ("Right", lambda: self._step_frame(1)),
            ("Ctrl+E", self._export),
            ("Ctrl+Shift+O", self._open_project),
            (
                "B",
                lambda: (
                    self.blur_draw_btn.setChecked(not self.blur_draw_btn.isChecked())
                    if self.blur_draw_btn.isEnabled()
                    else None
                ),
            ),
        ):
            self._editing_shortcuts.append(QShortcut(QKeySequence(key), self, callback))
        QApplication.instance().focusChanged.connect(self._editing_focus_changed)
        for key, callback in (
            (QKeySequence.StandardKey.Open, self._open_video),
            (QKeySequence.StandardKey.Save, self._save_project),
            (QKeySequence.StandardKey.Undo, self._undo),
            (QKeySequence.StandardKey.Redo, self._redo),
        ):
            QShortcut(QKeySequence(key), self, callback)

    def _editing_focus_changed(self, _old, new):
        for shortcut in self._editing_shortcuts:
            shortcut.setEnabled(not isinstance(new, (QLineEdit, QTextEdit, QAbstractSpinBox)))
