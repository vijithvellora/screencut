#!/usr/bin/env bash
# Launch with a repository virtual environment when available.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
    SCREENCUT_PYTHON="$SCRIPT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    SCREENCUT_PYTHON="$(command -v python3)"
else
    echo "Python 3.10 or newer is required. Install from https://python.org" >&2
    exit 1
fi

if ! "$SCREENCUT_PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    echo "Python 3.10 or newer is required." >&2
    exit 1
fi

for executable in ffmpeg ffprobe; do
    if ! command -v "$executable" >/dev/null 2>&1; then
        echo "$executable is required. Install with: brew install ffmpeg" >&2
        exit 1
    fi
done

if ! "$SCREENCUT_PYTHON" -c 'from PyQt6 import QtCore, QtGui, QtWidgets' 2>/dev/null; then
    echo "PyQt6 is missing. Set up the project environment:" >&2
    printf '  cd "%s"\n' "$SCRIPT_DIR" >&2
    echo '  python3 -m venv .venv' >&2
    echo '  .venv/bin/python -m pip install -e .' >&2
    exit 1
fi

exec "$SCREENCUT_PYTHON" "$SCRIPT_DIR/editor.py" "$@"
