# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick Start

**Run the editor:**
```bash
python3 editor.py
# Or: bash run_screencut.sh
# Or: python3 create_app.py (creates macOS .app bundle on Desktop)
```

**Prerequisites:**
- Python 3.10+ (comes pre-installed on macOS)
- ffmpeg: `brew install ffmpeg`
- PyQt6: `pip3 install PyQt6`

## Architecture Overview

ScreenCut is a lightweight video editor for macOS with three main concerns:

### 1. **Data Models** (editor.py:38–97)
- `BlurRegion`: Represents a blurred area with normalized coords (0–1) and time range
- `TrimRange`: Start/end times for video trimming
- `EditSession`: Central state containing video metadata, trim settings, speed, and all blur regions

### 2. **Video Processing** (editor.py:99–310)
- **ExportWorker**: Builds FFmpeg filter chains for trim, blur, and speed in a background thread
  - **Trim**: Uses `trim=start:duration` + `setpts` to reset timestamps
  - **Blur**: Chains `split` → `crop` → `gblur` → `overlay` with time-gated `enable` expressions
  - **Speed**: For video, scales PTS; for audio, chains `atempo` filters (0.5–2.0 range limits)
  - Handles variable audio (checks if file has audio stream before building audio chain)
- **ThumbnailWorker**: Extracts frame samples at regular intervals via ffmpeg

### 3. **UI** (editor.py:311–1424)
- **Canvas** (editor.py:311–455): Displays current frame, renders active blur overlays, handles blur drawing (mouse drag)
- **Timeline** (editor.py:457–600): Shows thumbnails, trim handles (draggable), blur bands (colored yellow), playhead
- **BlurPanel** (editor.py:602–750): Scrollable list of blur regions with start/end spinboxes
- **Playback Controls** (editor.py:912–962): Play/pause, frame stepping, mark blur start/end buttons
- **Right Panel** (editor.py:964–1050): Video info, trim controls, speed presets and slider, blur list
- **MainWindow** (editor.py:750–1424): Integrates all widgets, manages video loading, playback, seeking, and state sync

## Key Design Patterns

### FFmpeg Filter Chains
The export process builds a complex filter graph by chaining operations:
1. **Video chain**: `trim` → `setpts` (reset PTS) → one `[split]` → `[crop + gblur]` per blur → `[overlay]` (time-gated) → final `[vout]`
2. **Audio chain**: `atrim` → `asetpts` → `atempo` filters (repeated if speed < 0.5 or > 2.0) → `[aout]`
3. Blur regions' times are adjusted for trim offset and speed scaling

### Normalized Coordinates
Blur regions use normalized (0–1) x, y, w, h for video dimensions so they scale with resolution. Canvas converts screen pixels ↔ normalized coords via `_to_normalized()` and `_from_normalized()`.

### Synchronized Playback
- `QTimer` ticks at ~30ms intervals, computing `current_time` from elapsed wall-clock time
- Canvas, Timeline, BlurPanel, and controls all read `current_time` to display consistent state
- Seeking pauses playback and updates UI without restarting the timer

### State Isolation
- `EditSession` holds all editable state (trim, speed, blur regions)
- UI widgets read from `self.session` but emit signals for changes
- Main window updates `self.session` and broadcasts updates to canvas/timeline/panel

## Common Tasks

### Adding a new video effect (e.g., saturation, hue shift)
1. Add a field to `EditSession` (e.g., `saturation: float = 1.0`)
2. Build a UI control in `_build_right_panel()` (slider or preset buttons)
3. In `ExportWorker.run()`, append a new filter step to `filter_parts` (e.g., `hue=s={saturation}`)
4. Connect the control to update `self.session` and redraw

### Debugging FFmpeg filter graphs
FFmpeg filter strings are printed to stdout before encoding starts (see `ExportWorker.run()` line ~215). Copy the full `-filter_complex` value into `ffmpeg -filter_complex "..."` on the command line to test independently.

### Fixing trim or blur timing issues
Check:
1. `TrimRange.start` / `EditSession.trim.end` are in source video time (before speed scaling)
2. `BlurRegion.start_time` / `end_time` are absolute times in source video
3. In export, times are adjusted: `(blur_time - trim_start) / speed` to map to output timeline
4. `setpts` is crucial to reset timestamps after trim

### Adding new keyboard shortcuts
- `QShortcut` in `MainWindow.__init__()` (e.g., line ~1080)
- Connect to a slot like `_toggle_play()` or `_export()`

## File Structure

- **editor.py** (1424 lines): Entire application (data models, video workers, UI, main window)
- **create_app.py** (102 lines): Builds macOS .app bundle with embedded Python launcher
- **run_screencut.sh** (33 lines): Shell wrapper; checks ffmpeg + PyQt6, then runs editor.py
- **README.md**: User-facing docs (features, keyboard shortcuts, troubleshooting)

## Performance Notes

- **Frame extraction**: Slower codecs (HEVC) or high resolution files will slow the thumbnail preview. H.264 is fast.
- **Export**: Uses `libx264` with preset `fast` for speed; CRF 18/23/28 controls quality/file size.
- **Blur complexity**: Many overlapping blurs increase filter chain length. Each blur is an independent `split` → `crop` → `blur` → `overlay` step.
