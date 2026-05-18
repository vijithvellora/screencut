#!/usr/bin/env python3
"""
Creates a proper macOS .app bundle for ScreenCut.
Run once to install: python3 create_app.py
Then drag ScreenCut.app to your Applications folder or Dock.
"""

import os
import sys
import stat
import shutil
from pathlib import Path

APP_NAME = "ScreenCut"
BUNDLE_ID = "com.screencut.editor"

def create_app():
    script_dir = Path(__file__).parent.resolve()
    editor_py = script_dir / "editor.py"

    if not editor_py.exists():
        print(f"❌  editor.py not found at {editor_py}")
        sys.exit(1)

    # Output: ~/Desktop/ScreenCut.app
    desktop = Path.home() / "Desktop"
    app_path = desktop / f"{APP_NAME}.app"

    if app_path.exists():
        shutil.rmtree(app_path)

    # Build structure
    contents = app_path / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)

    # Info.plist
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>{APP_NAME}</string>
  <key>CFBundleIdentifier</key>
  <string>{BUNDLE_ID}</string>
  <key>CFBundleName</key>
  <string>{APP_NAME}</string>
  <key>CFBundleDisplayName</key>
  <string>ScreenCut</string>
  <key>CFBundleVersion</key>
  <string>1.0</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSRequiresAquaSystemAppearance</key>
  <false/>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>CFBundleSupportedPlatforms</key>
  <array><string>MacOSX</string></array>
  <key>NSAppleEventsUsageDescription</key>
  <string>ScreenCut uses AppleEvents for video processing.</string>
</dict>
</plist>"""
    (contents / "Info.plist").write_text(plist)

    # Copy editor.py into Resources
    dest_py = resources / "editor.py"
    shutil.copy(editor_py, dest_py)

    # Executable launcher
    py3 = shutil.which("python3") or "/usr/bin/python3"
    launcher = f"""#!/bin/bash
# ScreenCut macOS launcher
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

# Check ffmpeg
if ! command -v ffmpeg &>/dev/null; then
    osascript -e 'display alert "ffmpeg not found" message "Install via Homebrew: brew install ffmpeg" as warning'
    exit 1
fi

# Install PyQt6 if needed
{py3} -c "import PyQt6" 2>/dev/null || pip3 install PyQt6 --quiet

SCRIPT="$(dirname "$0")/../Resources/editor.py"
exec {py3} "$SCRIPT"
"""
    launcher_path = macos / APP_NAME
    launcher_path.write_text(launcher)
    launcher_path.chmod(launcher_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    print(f"✅  Created: {app_path}")
    print(f"   Drag ScreenCut.app to your Applications folder or Dock.")
    print(f"   First launch may require: right-click → Open (to bypass Gatekeeper)")

if __name__ == "__main__":
    create_app()
