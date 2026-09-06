#!/usr/bin/env python3
"""Build a standalone macOS app with Python, Qt, and FFmpeg included.

Install build tools with ``python -m pip install '.[bundle]'`` first.
The bundle is generated in dist/ScreenCut.app; no Desktop app is replaced.
"""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

APP_NAME = "ScreenCut"
BUNDLE_ID = "com.screencut.editor"


def create_app() -> int:
    root = Path(__file__).resolve().parent
    if sys.platform != "darwin":
        print("A macOS .app must be built on macOS.", file=sys.stderr)
        return 1
    if sys.version_info < (3, 10):
        print("Python 3.10 or newer is required.", file=sys.stderr)
        return 1
    for module in ("PyInstaller", "PyQt6"):
        if importlib.util.find_spec(module) is None:
            print(
                f"Missing {module}. Activate your virtual environment, then run:\n"
                "  python -m pip install '.[bundle]'",
                file=sys.stderr,
            )
            return 1
    binaries = []
    for executable in ("ffmpeg", "ffprobe"):
        path = shutil.which(executable)
        if path is None:
            print(f"Missing {executable}. Install with: brew install ffmpeg", file=sys.stderr)
            return 1
        binaries.extend(("--add-binary", f"{path}:bin"))
    destination = root / "dist"
    for output in (destination / f"{APP_NAME}.app", destination / APP_NAME):
        if output.exists():
            print(
                f"Build output already exists: {output}\n"
                "Move or remove the previous output before rebuilding.",
                file=sys.stderr,
            )
            return 1
    with tempfile.TemporaryDirectory(prefix="screencut-build-") as temporary:
        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--onedir",
            "--windowed",
            "--name",
            APP_NAME,
            "--osx-bundle-identifier",
            BUNDLE_ID,
            "--distpath",
            str(destination),
            "--workpath",
            str(Path(temporary) / "work"),
            "--specpath",
            temporary,
            "--paths",
            str(root),
            "--collect-submodules",
            "screencut",
            *binaries,
            str(root / "editor.py"),
        ]
        try:
            subprocess.run(command, cwd=root, check=True)
        except subprocess.CalledProcessError as error:
            print(
                f"App build failed (exit {error.returncode}). See build output above.",
                file=sys.stderr,
            )
            return error.returncode
    print(
        f"Created: {destination / (APP_NAME + '.app')}\n"
        "Python, Qt, FFmpeg, and FFprobe are included.\n"
        "Test on a clean Mac before distribution; signing/notarization is a separate step."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(create_app())
