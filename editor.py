#!/usr/bin/env python3
"""ScreenCut desktop entry point and editor workflow controller."""

import sys
import os
import copy
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox, QProgressDialog

from screencut.models import EditSession, validate_session
from screencut.projects import SessionHistory, load_project, save_project
from screencut.media import ExportWorker, ThumbnailWorker, PreviewController, ProbeWorker
from screencut.ui import EditorUIMixin


class ScreenCut(EditorUIMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.session = EditSession()
        self.history = SessionHistory(self.session)
        self.project_path = None
        self._saved_session = None
        self._restoring = False
        self.playing = False
        self._generation = 0
        self._workers = []
        self._thumb_worker = None
        self._export_worker = None
        self._probe_worker = None
        self._setup_style()
        self._setup_ui()
        self.setAcceptDrops(True)
        self._setup_shortcuts()
        self.preview = PreviewController(self)
        self.preview.frame_ready.connect(self._on_frame)
        self.preview.position_changed.connect(self._on_position)
        self.preview.playback_changed.connect(self._on_playback)
        self.preview.error.connect(lambda message: self.status.showMessage(message, 10000))
        self._update_history_ui()

    def _track_worker(self, worker):
        self._workers = [job for job in self._workers if job.isRunning()]
        self._workers.append(worker)
        # QThread.finished can be shadowed by a worker's result signal.
        # Keep references until shutdown so in-flight jobs cannot be destroyed.
        return worker

    def _confirm_discard(self):
        if not self.session.video_path or self.session == self._saved_session:
            return True
        choice = QMessageBox.question(
            self,
            "Save project?",
            "Save your current editing settings before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Save:
            return self._save_project()
        return choice == QMessageBox.StandardButton.Discard

    def _open_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open recording",
            str(Path.home() / "Movies"),
            "Video files (*.mp4 *.mov *.mkv *.avi *.m4v *.webm);;All files (*)",
        )
        if path:
            self._load_video(path)

    def _load_video(self, path, restored=None, project_path=None):
        if not self._confirm_discard():
            return
        if not Path(path).is_file():
            QMessageBox.warning(self, "Missing recording", f"Recording not found: {path}")
            return
        self.preview.pause()
        self._generation += 1
        generation = self._generation
        for worker in (self._probe_worker, self._thumb_worker):
            if worker and worker.isRunning():
                worker.cancel()
        self.status.showMessage("Reading recording…")
        worker = self._track_worker(ProbeWorker(str(Path(path).resolve())))
        self._probe_worker = worker
        worker.ready.connect(
            lambda info: self._finish_load(path, info, generation, restored, project_path)
        )
        worker.failed.connect(lambda error: self._load_failed(error, generation))
        worker.start()

    def _load_failed(self, error, generation):
        if generation == self._generation:
            QMessageBox.warning(self, "Could not open recording", error)
            self.status.showMessage("Could not open recording")

    def _finish_load(self, path, info, generation, restored, project_path):
        if generation != self._generation:
            return
        session = restored or EditSession()
        session.video_path = str(Path(path).resolve())
        for name in ("duration", "width", "height", "fps"):
            setattr(session, name, info[name])
        if restored is None:
            session.trim.end = session.duration
        try:
            session = validate_session(session)
        except ValueError as error:
            self._load_failed(str(error), generation)
            return
        self.session = session
        self._has_audio = info.get("has_audio", False)
        self.project_path = project_path
        self.history.reset(session)

        self._saved_session = copy.deepcopy(session)
        self.canvas.frame_pixmap = None
        self.canvas.selected_blur_id = None
        self.canvas.selected_annotation_id = None
        self.canvas.annotation_tool = None
        self.canvas.current_time = 0
        self.blur_draw_btn.setChecked(False)
        self._cancel_draw()
        self.timeline.thumbnails = []
        self.preview.open(session.video_path)
        self._sync_session()
        for btn in (
            self.play_btn,
            self.prev_frame_btn,
            self.next_frame_btn,
            self.blur_draw_btn,
            self.export_btn,
            self.mark_start_btn,
            self.mark_end_btn,
        ):
            btn.setEnabled(True)
        self.status.showMessage(f"Loaded {Path(path).name}")
        worker = self._track_worker(ThumbnailWorker(session.video_path, session.duration, 24))
        self._thumb_worker = worker
        worker.ready.connect(lambda thumbs: self._on_thumbnails(thumbs, generation))
        worker.start()

    def _sync_session(self):
        self._restoring = True
        s = self.session
        self.info_labels["File"].setText(Path(s.video_path).name)
        self.info_labels["Duration"].setText(self._fmt_time(s.duration))
        self.info_labels["Resolution"].setText(f"{s.width} × {s.height}")
        self.info_labels["FPS"].setText(f"{s.fps:.2f}")
        self.timeline.duration = s.duration
        self.timeline.trim_start = s.trim.start
        self.timeline.trim_end = s.trim.end or s.duration
        self.trim_start_lbl.setText(self._fmt_time(s.trim.start))
        self.trim_end_lbl.setText(self._fmt_time(s.trim.end or s.duration))
        self._set_speed(s.speed)
        self._refresh_canvas()
        self._seek_to(max(s.trim.start, min(self.canvas.current_time, s.trim.end or s.duration)))
        self._restoring = False
        self._update_history_ui()

    def _on_thumbnails(self, thumbs, generation):
        if generation == self._generation:
            self.timeline.thumbnails = [(t, QPixmap.fromImage(image)) for t, image in thumbs]
            self.timeline.update()

    def _on_frame(self, image):
        self.canvas.set_frame(QPixmap.fromImage(image))

    def _on_position(self, t):
        end = self.session.trim.end or self.session.duration
        if self.playing and t >= end:
            self.preview.pause()
            self.preview.seek(end)
            t = end
        self.canvas.current_time = t
        self.timeline.current_time = t
        self.canvas.update()
        self.timeline.update()
        self._update_time_label(t)

    def _on_playback(self, playing):
        self.playing = playing
        self.play_btn.setText("Ⅱ" if playing else "▶")

    def _seek_to(self, t):
        t = max(0, min(self.session.duration, t))
        self.preview.seek(t)
        self._on_position(t)

    def _on_seek(self, t):
        self.preview.pause()
        self._seek_to(t)

    def _toggle_play(self):
        if not self.session.video_path:
            return
        if self.playing:
            self.preview.pause()
        else:
            end = self.session.trim.end or self.session.duration
            if (
                self.canvas.current_time >= end
                or self.canvas.current_time < self.session.trim.start
            ):
                self._seek_to(self.session.trim.start)
            self.preview.play()

    def _step_frame(self, direction):
        if self.session.video_path:
            self.preview.pause()
            self._seek_to(self.canvas.current_time + direction / max(1, self.session.fps))

    def _update_time_label(self, t):
        self.time_label.setText(f"{self._fmt_time(t)}  /  {self._fmt_time(self.session.duration)}")

    def _record_change(self):
        if self._restoring or not self.session.video_path:
            return
        self.history.record(self.session)
        self._update_history_ui()

    def _update_history_ui(self):
        self.undo_btn.setEnabled(self.history.can_undo)
        self.redo_btn.setEnabled(self.history.can_redo)
        self.save_project_btn.setEnabled(bool(self.session.video_path))
        name = (
            Path(self.project_path).stem
            if self.project_path
            else Path(self.session.video_path).name
        )
        dirty = self.session.video_path and self.session != self._saved_session
        self.project_label.setText((name or "Untitled project") + (" •" if dirty else ""))
        self._update_export_summary()

    def _undo(self):
        session = self.history.undo()
        if session is not None:
            self.session = session
            self._sync_session()

    def _redo(self):
        session = self.history.redo()
        if session is not None:
            self.session = session
            self._sync_session()

    def _save_project(self):
        if not self.session.video_path:
            return False
        path = self.project_path
        if not path:
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Save project",
                str(Path(self.session.video_path).with_suffix(".screencut")),
                "ScreenCut project (*.screencut)",
            )
        if not path:
            return False
        try:
            save_project(path, self.session)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Could not save project", str(error))
            return False

        self.project_path = path
        self._saved_session = copy.deepcopy(self.session)
        self._update_history_ui()
        self.status.showMessage("Project saved")
        return True

    def _open_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open project", str(Path.home()), "ScreenCut project (*.screencut *.json)"
        )
        if not path:
            return
        try:
            session = load_project(path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Could not open project", str(error))
            return
        if not Path(session.video_path).is_file():
            replacement, _ = QFileDialog.getOpenFileName(self, "Locate missing source recording")
            if not replacement:
                return
            session.video_path = replacement
        self._load_video(session.video_path, session, path)

    def _on_trim_changed(self, start, end):
        if not self.session.video_path:
            return
        self.session.trim.start = max(0, min(start, end - 0.001))
        self.session.trim.end = min(self.session.duration, max(end, start + 0.001))
        self.trim_start_lbl.setText(self._fmt_time(self.session.trim.start))
        self.trim_end_lbl.setText(self._fmt_time(self.session.trim.end))
        self._record_change()

    def _reset_trim(self):
        if self.session.video_path:
            self._on_trim_changed(0, self.session.duration)
            self.timeline.trim_start = 0
            self.timeline.trim_end = self.session.duration
            self.timeline.update()

    def _toggle_draw_mode(self, checked):
        self.canvas.draw_mode = checked
        self.canvas.setCursor(Qt.CursorShape.CrossCursor if checked else Qt.CursorShape.ArrowCursor)
        if checked:
            for button in self.annotation_panel.add_buttons:
                button.setChecked(False)
            self.canvas.annotation_tool = None
            self.canvas.selected_annotation_id = None
            self.preview.pause()
            self.status.showMessage("Drag over the recording to create a privacy region")

    def _on_blur_drawn(self, x, y, w, h):
        t = min(self.canvas.current_time, max(0, self.session.duration - 0.01))
        br = self.session.add_blur(x, y, w, h, t, min(t + 5, self.session.duration))
        self.canvas.selected_blur_id = br.id
        self.blur_draw_btn.setChecked(False)
        self._on_region_updated()

    def _cancel_draw(self):
        self.blur_draw_btn.setChecked(False)
        self.canvas.annotation_tool = None
        self.canvas._drag_start = None
        self.canvas._drag_rect = None
        for button in self.annotation_panel.add_buttons:
            button.setChecked(False)
        self.canvas.setCursor(Qt.CursorShape.ArrowCursor)
        self.canvas.update()

    def _start_annotation(self, kind):
        if not self.session.video_path:
            return
        self.blur_draw_btn.setChecked(False)
        self.canvas.annotation_tool = kind
        for button in self.annotation_panel.add_buttons:
            button.setChecked(button.text().lower() == kind)
        self.canvas.selected_blur_id = None
        self.canvas.setCursor(Qt.CursorShape.CrossCursor)
        self.preview.pause()
        self.status.showMessage(
            f"Drag on the preview to place {kind}. Use the Annotations tab to edit it."
        )

    def _on_annotation_drawn(self, kind, x, y, x2, y2):
        t = min(self.canvas.current_time, max(0, self.session.duration - 0.01))
        item = self.session.add_annotation(kind, x, y, x2, y2, t, min(t + 5, self.session.duration))
        self._cancel_draw()
        self.canvas.selected_annotation_id = item.id
        self._on_region_updated()
        self.effect_tabs.setCurrentWidget(self.annotation_panel)
        if kind == "text":
            self.annotation_panel.text.setFocus()
            self.annotation_panel.text.selectAll()
        self.status.showMessage(f"{kind.title()} added · edit appearance and timing in Annotations")

    def _select_annotation(self, aid):
        self.canvas.selected_annotation_id = aid
        self.canvas.selected_blur_id = None
        self.annotation_panel.refresh(self.session, aid)
        self.effect_tabs.setCurrentWidget(self.annotation_panel)
        self.canvas.update()

    def _remove_annotation(self, aid):
        self.session.annotations = [item for item in self.session.annotations if item.id != aid]
        self.canvas.selected_annotation_id = None
        self._on_region_updated()

    def _mark_annotation(self, start):
        for item in self.session.annotations:
            if item.id == self.canvas.selected_annotation_id:
                if start:
                    item.start_time = min(self.canvas.current_time, item.end_time - 0.001)
                else:
                    item.end_time = max(self.canvas.current_time, item.start_time + 0.001)
                self._on_region_updated()
                return True
        return False

    def _mark_blur_start(self):
        if self._mark_annotation(True):
            return
        for br in self.session.blur_regions:
            if br.id == self.canvas.selected_blur_id:
                br.start_time = max(0, min(self.canvas.current_time, br.end_time - 0.01))
                self._on_region_updated()
                break

    def _mark_blur_end(self):
        if self._mark_annotation(False):
            return
        for br in self.session.blur_regions:
            if br.id == self.canvas.selected_blur_id:
                br.end_time = min(
                    self.session.duration, max(self.canvas.current_time, br.start_time + 0.01)
                )
                self._on_region_updated()
                break

    def _remove_blur(self, bid):
        self.session.remove_blur(bid)
        if self.canvas.selected_blur_id == bid:
            self.canvas.selected_blur_id = None
        self._on_region_updated()

    def _on_blur_selected(self, bid):
        self.canvas.selected_annotation_id = None
        self.canvas.selected_blur_id = bid
        self.canvas.update()

    def _on_region_updated(self):
        self._record_change()
        self._refresh_canvas()

    def _refresh_canvas(self):
        self.canvas.annotations = self.session.annotations
        self.timeline.annotations = self.session.annotations
        self.annotation_panel.refresh(self.session, self.canvas.selected_annotation_id)
        self.canvas.blur_regions = self.session.blur_regions
        self.canvas.update()
        self.timeline.blur_regions = self.session.blur_regions
        self.timeline.update()
        self.blur_panel.refresh(self.session, self.canvas.current_time)

    def _set_speed(self, speed):
        self.session.speed = max(0.25, min(8, speed))
        self.speed_label.setText(f"{self.session.speed:.2f}×")
        self.speed_slider.blockSignals(True)
        self.speed_slider.setValue(round(self.session.speed * 100))
        self.speed_slider.blockSignals(False)
        self.speed_combo.blockSignals(True)
        index = next(
            (
                i
                for i in range(self.speed_combo.count())
                if abs(float(self.speed_combo.itemText(i)[:-1]) - self.session.speed) < 0.001
            ),
            -1,
        )
        self.speed_combo.setCurrentIndex(index)
        self.speed_combo.blockSignals(False)
        if hasattr(self, "preview"):
            self.preview.set_rate(self.session.speed)
        self._record_change()

    def _export(self):
        if not self.session.video_path or (self._export_worker and self._export_worker.isRunning()):
            return
        try:
            snapshot = validate_session(self.session)
        except ValueError as error:
            QMessageBox.warning(self, "Check editing settings", str(error))
            return
        src = Path(snapshot.video_path)
        out, _ = QFileDialog.getSaveFileName(
            self, "Export video", str(src.with_name(src.stem + "_edited.mp4")), "MP4 video (*.mp4)"
        )
        if not out:
            return
        if Path(out).resolve() == src.resolve():
            QMessageBox.warning(
                self,
                "Choose another filename",
                "Export to a different file to preserve the source recording.",
            )
            return
        self.preview.pause()
        self.progress_dlg = QProgressDialog("Preparing…", "Cancel", 0, 100, self)
        self.progress_dlg.setWindowTitle("Export recording")
        self.progress_dlg.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress_dlg.setMinimumDuration(0)
        worker = self._track_worker(ExportWorker(snapshot, out))
        self._export_worker = worker
        worker.has_audio = self._has_audio
        worker.crf = {"High": 18, "Medium": 23, "Low": 28}.get(self.quality_combo.currentText(), 23)
        worker.progress.connect(
            lambda pct, msg: (self.progress_dlg.setValue(pct), self.progress_dlg.setLabelText(msg))
        )
        worker.finished.connect(self._on_export_done)
        self.progress_dlg.canceled.connect(worker.cancel)
        worker.start()

    def _on_export_done(self, success, info):
        self.progress_dlg.close()
        if success:
            QMessageBox.information(self, "Export complete", f"Saved video to:\n{info}")
        elif "cancel" not in info.lower():
            QMessageBox.warning(self, "Export failed", info)
        self.status.showMessage("Export complete" if success else info, 10000)

    @staticmethod
    def _fmt_time(t):
        mins, seconds = divmod(max(0, t), 60)
        return f"{int(mins)}:{seconds:05.2f}"

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self._load_video(paths[0])

    def closeEvent(self, event):
        if not self._confirm_discard():
            event.ignore()
            return
        self._generation += 1
        self.preview.close()
        for worker in self._workers:
            if worker.isRunning():
                worker.cancel()
        for worker in self._workers:
            worker.wait()
        event.accept()


def main():
    if getattr(sys, "frozen", False):
        os.environ["PATH"] = (
            str(Path(sys._MEIPASS) / "bin") + os.pathsep + os.environ.get("PATH", "")
        )
    app = QApplication(sys.argv)
    app.setApplicationName("ScreenCut")
    app.setOrganizationName("ScreenCut")
    window = ScreenCut()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
