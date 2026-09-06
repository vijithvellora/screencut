"""Editing state shared by the UI, project files, and media workers."""

from copy import deepcopy
from dataclasses import dataclass, field
import math
import re


@dataclass
class BlurRegion:
    id: int
    x: float
    y: float
    w: float
    h: float
    start_time: float
    end_time: float
    label: str = ""
    mode: str = "blur"

    def to_ffmpeg_filter(self, video_w: int, video_h: int, idx: int) -> str:
        px, py = int(self.x * video_w), int(self.y * video_h)
        pw = max(1, min(int(self.w * video_w), video_w - px))
        ph = max(1, min(int(self.h * video_h), video_h - py))
        tag_in = f"[blur_out_{idx - 1}]" if idx else "[0:v]"
        tag_out = f"[blur_out_{idx}]"
        enable = f"between(t,{self.start_time},{self.end_time})"
        if self.mode == "redact":
            return (
                f"{tag_in}drawbox=x={px}:y={py}:w={pw}:h={ph}:"
                f"color=black:t=fill:enable='{enable}'{tag_out}"
            )
        return (
            f"{tag_in}split=2[base_{idx}][over_{idx}];"
            f"[over_{idx}]crop={pw}:{ph}:{px}:{py},gblur=sigma=20[blurred_{idx}];"
            f"[base_{idx}][blurred_{idx}]overlay={px}:{py}"
            f":enable='{enable}'{tag_out}"
        )


@dataclass
class Annotation:
    id: int
    kind: str
    x: float
    y: float
    x2: float
    y2: float
    start_time: float
    end_time: float
    text: str = "Your text"
    color: str = "#facc15"
    size: float = 0.045  # Text height relative to source height
    opacity: float = 1.0


@dataclass
class TrimRange:
    start: float = 0.0
    end: float = 0.0


@dataclass
class EditSession:
    video_path: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 30.0
    trim: TrimRange = field(default_factory=TrimRange)
    speed: float = 1.0
    blur_regions: list[BlurRegion] = field(default_factory=list)
    _next_blur_id: int = 0

    annotations: list[Annotation] = field(default_factory=list)
    _next_annotation_id: int = 0

    def add_annotation(self, kind, x, y, x2, y2, start, end):
        item = Annotation(self._next_annotation_id, kind, x, y, x2, y2, start, end)
        if kind == "highlight":
            item.opacity = 0.3
        self._next_annotation_id += 1
        self.annotations.append(item)
        return item

    def add_blur(self, x, y, w, h, start, end, mode="blur") -> BlurRegion:
        region = BlurRegion(self._next_blur_id, x, y, w, h, start, end, mode=mode)
        self._next_blur_id += 1
        self.blur_regions.append(region)
        return region

    def remove_blur(self, bid: int):
        self.blur_regions = [region for region in self.blur_regions if region.id != bid]

    def effective_duration(self) -> float:
        end = self.trim.end if self.trim.end > 0 else self.duration
        return max(0.0, end - self.trim.start) / self.speed if self.speed > 0 else 0.0


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def validate_session(session: EditSession) -> EditSession:
    """Return a validated copy; clamp harmless coordinate/time overshoot.

    Reversed or empty regions and trims are rejected rather than silently
    dropping a privacy mask. A zero trim end retains its full-duration meaning.
    """
    result = deepcopy(session)
    if not isinstance(result.video_path, str):
        raise ValueError("Video path must be text")
    result.duration = _number(result.duration, "Duration")
    if result.duration < 0:
        raise ValueError("Duration cannot be negative")
    for name in ("width", "height"):
        value = getattr(result, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
    result.fps = _number(result.fps, "Frame rate")
    if result.fps <= 0:
        raise ValueError("Frame rate must be positive")
    result.speed = _number(result.speed, "Speed")
    if not 0.25 <= result.speed <= 8:
        raise ValueError("Speed must be between 0.25 and 8")
    start = _number(result.trim.start, "Trim start")
    end = _number(result.trim.end, "Trim end")
    if start < 0 or end < 0:
        raise ValueError("Trim times cannot be negative")
    result.trim.start = min(start, result.duration)
    result.trim.end = min(end, result.duration)
    if result.duration and result.trim.start >= (result.trim.end or result.duration):
        raise ValueError("Trim start must precede trim end")
    identifiers = set()
    for region in result.blur_regions:
        if (
            isinstance(region.id, bool)
            or not isinstance(region.id, int)
            or region.id < 0
            or region.id in identifiers
        ):
            raise ValueError("Region IDs must be unique nonnegative integers")
        identifiers.add(region.id)
        if region.mode not in ("blur", "redact") or not isinstance(region.label, str):
            raise ValueError("Invalid region mode or label")
        x, y, w, h = (
            _number(getattr(region, name), f"Region {name}") for name in ("x", "y", "w", "h")
        )
        region.x, region.y = max(0.0, min(x, 1.0)), max(0.0, min(y, 1.0))
        region.w = min(w + min(x, 0.0), 1.0 - region.x)
        region.h = min(h + min(y, 0.0), 1.0 - region.y)
        if region.w <= 0 or region.h <= 0:
            raise ValueError("Region must have positive size inside the video")
        region.start_time = max(
            0.0, min(_number(region.start_time, "Region start"), result.duration)
        )
        region.end_time = max(0.0, min(_number(region.end_time, "Region end"), result.duration))
        if region.start_time >= region.end_time:
            raise ValueError("Region start must precede region end")
    result._next_blur_id = max(identifiers, default=-1) + 1
    ids = set()
    for item in result.annotations:
        if type(item.id) is not int or item.id < 0 or item.id in ids:
            raise ValueError("Annotation IDs must be unique nonnegative integers")
        ids.add(item.id)
        if item.kind not in ("text", "arrow", "highlight"):
            raise ValueError("Unknown annotation type")
        for name in ("x", "y", "x2", "y2"):
            value = _number(getattr(item, name), "Annotation coordinate")
            if not 0 <= value <= 1:
                raise ValueError("Annotation coordinates must be inside the video")
        if item.kind == "arrow":
            valid = math.hypot(item.x2 - item.x, item.y2 - item.y) > 0.001
        else:
            valid = item.x2 > item.x and item.y2 > item.y
        if not valid:
            raise ValueError("Annotation must have positive size")
        start = _number(item.start_time, "Annotation start")
        end = _number(item.end_time, "Annotation end")
        if not 0 <= start < end <= result.duration:
            raise ValueError("Annotation start must precede end within the recording")
        if not isinstance(item.text, str) or len(item.text) > 2000:
            raise ValueError("Annotation text must be at most 2000 characters")
        if not isinstance(item.color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", item.color):
            raise ValueError("Invalid annotation color")
        if not 0.01 <= _number(item.size, "Annotation size") <= 0.25:
            raise ValueError("Annotation size must be between 1% and 25%")
        if not 0.05 <= _number(item.opacity, "Annotation opacity") <= 1:
            raise ValueError("Annotation opacity must be between 5% and 100%")
    result._next_annotation_id = max(ids, default=-1) + 1
    return result
