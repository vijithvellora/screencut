#!/usr/bin/env bash
# ScreenCut launcher
# Run from Terminal: bash run_screencut.sh
# Or double-click if your terminal is set up for .sh files.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EDITOR="$SCRIPT_DIR/editor.py"

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "❌  Python 3 is required. Install from https://python.org" >&2
    exit 1
fi

# Check ffmpeg
if ! command -v ffmpeg &>/dev/null; then
    echo "❌  ffmpeg is required."
    echo "   Install via Homebrew: brew install ffmpeg"
    echo "   Or download from: https://ffmpeg.org/download.html"
    exit 1
fi

# Install/check PyQt6
python3 -c "import PyQt6" 2>/dev/null || {
    echo "📦  Installing PyQt6..."
    pip3 install PyQt6 --break-system-packages --quiet || pip3 install PyQt6 --quiet
}

echo "🎬  Starting ScreenCut..."
python3 "$EDITOR"
