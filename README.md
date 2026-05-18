# ScreenCut — Screen Recording Editor for macOS

A lightweight, fast editor built for screen recordings. Trim, blur sensitive content, change speed, and export — all with a clean dark UI.

---

## Requirements

- **macOS** 12+ (Apple Silicon or Intel)
- **Python 3.10+** — comes pre-installed on macOS
- **ffmpeg** — for video processing
- **PyQt6** — for the UI (auto-installed)

---

## Quick Setup (2 minutes)

### 1. Install ffmpeg (if not already installed)
```bash
# Homebrew (recommended)
brew install ffmpeg

# Or download from https://ffmpeg.org/download.html
```

### 2. Install PyQt6
```bash
pip3 install PyQt6
```

### 3. Run ScreenCut
```bash
# Option A: Shell script
bash run_screencut.sh

# Option B: Direct
python3 editor.py

# Option C: Create a macOS .app bundle (drag to Applications)
python3 create_app.py
```

---

## Features & Usage

### Opening a Video
- Click **⌘ Open Video** in the toolbar, or
- **Drag & drop** a video file onto the window

Supports: `.mp4`, `.mov`, `.mkv`, `.avi`, `.m4v`, `.webm`

---

### Trimming
- **Drag the cyan handles** on the timeline to set In/Out points
- The dimmed areas outside the handles are cut from the export
- "Reset Trim" in the right panel restores full length
- Keyboard shortcuts: `Space` = play/pause, `←/→` = step frame

---

### Adding Blur Regions

1. Navigate to the frame where you want the blur to start (use timeline or playback)
2. Click **✥ Draw Blur** in the toolbar (or press `B`)
3. **Drag a rectangle** over the area you want blurred (passwords, faces, URLs)
4. The blur appears in the right panel — **adjust Start/End times** to control exactly when it's active
5. Add as many blur regions as needed — they can overlap in time or cover different areas

**Tips:**
- Click a blur card in the right panel to select it, then use **[ Mark Start** / **Mark End ]** to snap its time to the current playhead
- Blur regions show as yellow bands on the timeline
- The active blur (at current time) glows cyan on the canvas

---

### Speed Control
- Use the **preset buttons** (0.5×, 1.0×, 1.5×, 2.0×, 3.0×, 4.0×) in the right panel
- Or drag the **speed slider** for precise values (0.25× to 8×)
- Speed affects both video and audio

---

### Exporting
1. Click **⬇ Export** (or `Ctrl+E`)
2. Choose output filename and location
3. Select quality: **High** (CRF 18, large file), **Medium** (CRF 23), **Low** (CRF 28, small file)
4. Progress bar shows encoding status

Output is H.264/AAC MP4 — compatible everywhere.

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Space` | Play / Pause |
| `←` / `→` | Step one frame |
| `B` | Toggle Draw Blur mode |
| `Ctrl+O` | Open video |
| `Ctrl+E` | Export |

---

## Troubleshooting

**"ffmpeg not found"** → Install with `brew install ffmpeg`  
**Slow preview** → Frame extraction uses ffmpeg; performance depends on codec. H.264 files are fastest.  
**PyQt6 error** → Run `pip3 install PyQt6 --upgrade`  
**Gatekeeper blocks .app** → Right-click → Open on first launch
