# Contributor Notes

Follow [AGENTS.md](AGENTS.md) for repository structure, commands, coding conventions, and validation requirements. See [README.md](README.md) for setup and editing workflows.

Editing state lives in `screencut/models.py`; source-time coordinates and ranges are converted for output by `screencut/processing.py`. Qt widgets are in `screencut/widgets.py`, layout in `screencut/ui.py`, and workflow integration in `editor.py`. Preview decoding uses QtMultimedia; FFmpeg subprocesses are owned by cancelable workers in `screencut/media.py`.
