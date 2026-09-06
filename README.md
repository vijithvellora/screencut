# ScreenCut

A focused macOS screen recording editor. Trim recordings, preview with audio, adjust speed, protect private details, and export H.264/AAC MP4.

## Run locally

Install Python 3.10+ and FFmpeg, then run from this repository:

```bash
brew install ffmpeg
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python editor.py
```

After setup, `bash run_screencut.sh` selects the repository virtual environment and checks dependencies. The launcher does not install packages into system Python.

## Editing

1. **Open video** or drop an MP4, MOV, MKV, AVI, M4V, or WebM recording onto the window.
2. Use **Space** to play/pause with audio, arrow keys to step, and the timeline to seek. Drag the teal timeline handles to trim.
3. Choose **Draw region**, then drag over private content. In its card, choose Gaussian blur or opaque redaction and adjust the start/end times. Select a region before using **Set start/end**.
4. Adjust speed from 0.25× to 8×. The inspector shows the resulting output duration.
5. **Export video** applies trim, speed, and privacy effects. Cancel stops encoding and removes the unfinished temporary file. Existing destination files are replaced only after successful encoding.

The Qt blur preview approximates the FFmpeg export; inspect the exported result when blur strength matters. Opaque redaction covers content completely. Preview codec/audio support depends on Qt's media backend, and frame stepping is timestamp-based rather than guaranteed frame-accurate for variable-frame-rate sources.

## Text, arrows, and highlights

Open the **Annotations** tab in the inspector and choose **Text**, **Arrow**, or **Highlight**, then drag on the preview. Text and highlights use the rectangle you draw; arrows point from the start of your drag to its end. Press **Esc** to cancel placement.

Select an annotation on the canvas or in the annotation dropdown. Edit its text, color, size, opacity, and start/end times; scroll the inspector to reach all controls. **Start X/Y** and **End X/Y** adjust its placement as percentages of the recording. **Set start/end** snaps the selected annotation's timing to the playhead. Highlights default to 30% opacity. Text wraps inside its box and clips when the box is too small; enlarge the box or reduce its size to fit longer text.

Annotations appear in both preview and exported MP4, above privacy regions. They follow trimming and playback speed, support Undo/Redo and deletion, and are saved in project files. New project files use version 2; the editor also opens version 1 projects. Older ScreenCut versions cannot open version 2 projects.

## Projects and history

**Save** writes editing settings to a `.screencut` JSON project, with a relative source path. **Open project** restores settings and lets you locate missing media. Projects reference recordings; they do not embed them. Undo/Redo covers trim, speed, and region edits. Unsaved changes prompt before switching recordings or closing.

On macOS: **⌘O** opens video, **⌘⇧O** opens a project, **⌘S** saves, **⌘Z / ⌘⇧Z** undo/redo, **⌘E** exports, and **B** toggles region drawing.

## Check changes

```bash
source .venv/bin/activate
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
ruff check .
ruff format --check .
bash -n run_screencut.sh
```

Tests generate small FFmpeg fixtures, verify output duration/audio/redaction, exercise cancellation and atomic saves, and check editor history, stale results, and minimum window layout. FFmpeg integration cases skip when FFmpeg is unavailable.

For a manual check, open two different recordings in succession, play and seek, add overlapping regions, switch one to opaque redaction, undo/redo, save/reopen a project, and export at slow/fast speeds. Check the resulting audio, duration, privacy coverage, and cancellation.

## Standalone macOS bundle

```bash
python -m pip install '.[bundle]'
python create_app.py
open dist/ScreenCut.app
```

PyInstaller packages Python, Qt, FFmpeg, and FFprobe. Existing build outputs must be moved or removed before rebuilding. Test the bundle on a clean Mac before distribution; signing and notarization are separate steps.

## Code layout

- `editor.py`: workflow controller and application entry point.
- `screencut/models.py`, `projects.py`: validated editing state, persistence, and undo history.
- `screencut/media.py`, `processing.py`: asynchronous preview, cancelable workers, and FFmpeg graphs.
- `screencut/ui.py`, `widgets.py`: layout, preview canvas, timeline, and region controls.
- `tests/`: unit, offscreen UI, and generated-media regression tests.
