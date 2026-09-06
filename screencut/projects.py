"""Versioned project persistence and independent edit-history snapshots."""

from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile

from .models import Annotation, BlurRegion, EditSession, TrimRange, validate_session

PROJECT_VERSION = 2


def save_project(path, session: EditSession):
    """Atomically replace a project, preserving the old file on write failure."""
    destination = Path(path).expanduser().resolve()
    data = asdict(validate_session(session))
    data.pop("_next_blur_id", None)
    data.pop("_next_annotation_id", None)
    if data["video_path"]:
        data["video_path"] = os.path.relpath(
            Path(data["video_path"]).expanduser().resolve(), destination.parent
        )
    payload = {"version": PROJECT_VERSION, "session": data}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, suffix=".tmp", delete=False
        ) as stream:
            temporary = stream.name
            json.dump(payload, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def load_project(path) -> EditSession:
    """Read project settings; callers may prompt to relink missing source media."""
    source = Path(path).expanduser().resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or type(payload.get("version")) is not int
            or payload["version"] not in (1, PROJECT_VERSION)
        ):
            raise ValueError("Unsupported ScreenCut project version")
        data = payload["session"].copy()
        data["trim"] = TrimRange(**data["trim"])
        data["blur_regions"] = [BlurRegion(**region) for region in data["blur_regions"]]
        data["annotations"] = [Annotation(**item) for item in data.get("annotations", [])]
        session = validate_session(EditSession(**data))
        if session.video_path:
            session.video_path = str((source.parent / session.video_path).resolve())
        return session
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid ScreenCut project: {error}") from error


class SessionHistory:
    """Record completed edits. Undo and redo never expose stored snapshots."""

    def __init__(self, session: EditSession, limit: int = 100):
        self.limit = max(2, limit)
        self.reset(session)

    def reset(self, session: EditSession):
        self._states = [deepcopy(session)]
        self._index = 0

    @property
    def can_undo(self):
        return self._index > 0

    @property
    def can_redo(self):
        return self._index + 1 < len(self._states)

    def record(self, session: EditSession) -> bool:
        if session == self._states[self._index]:
            return False
        self._states = self._states[: self._index + 1]
        self._states.append(deepcopy(session))
        self._states = self._states[-self.limit :]
        self._index = len(self._states) - 1
        return True

    def undo(self):
        if not self.can_undo:
            return None
        self._index -= 1
        return deepcopy(self._states[self._index])

    def redo(self):
        if not self.can_redo:
            return None
        self._index += 1
        return deepcopy(self._states[self._index])
