"""FFmpeg graph construction, independent of the GUI."""

import math
import subprocess
import threading


def build_filter(session, has_audio=True, annotation_paths=()):
    """Build trim/speed and sequential redaction filters in source time units."""
    s = session
    end = s.trim.end or s.duration
    if not math.isfinite(s.speed) or not 0.05 <= s.speed <= 100:
        raise ValueError("Playback speed must be between 0.05 and 100.")
    if not (0 <= s.trim.start < end <= s.duration):
        raise ValueError("Trim start must precede end within the source video.")
    if s.width <= 0 or s.height <= 0:
        raise ValueError("Video dimensions must be positive.")
    duration = (end - s.trim.start) / s.speed
    parts = [
        f"[0:v]trim=start={s.trim.start:.9f}:end={end:.9f},setpts=(PTS-STARTPTS)/{s.speed:.9f},format=yuv444p[v0]"
    ]
    previous = "[v0]"
    for i, br in enumerate(s.blur_regions, 1):
        if not 0 <= br.start_time < br.end_time <= s.duration:
            raise ValueError(f"Region {br.id}: start must precede end within the video.")
        values = (br.x, br.y, br.w, br.h)
        if not all(math.isfinite(v) for v in values) or not (
            0 <= br.x < 1 and 0 <= br.y < 1 and 0 < br.w <= 1 and 0 < br.h <= 1
        ):
            raise ValueError(f"Region {br.id}: invalid rectangle.")
        t0 = max(0, (br.start_time - s.trim.start) / s.speed)
        t1 = min(duration, (br.end_time - s.trim.start) / s.speed)
        if t1 <= t0:
            continue
        x, y = int(br.x * s.width), int(br.y * s.height)
        w = min(s.width - x, max(1, math.ceil(br.w * s.width)))
        h = min(s.height - y, max(1, math.ceil(br.h * s.height)))
        output = f"[v{i}]"
        enabled = f"gte(t,{t0:.9f})*lt(t,{t1:.9f})"
        mode = getattr(br, "mode", "blur")
        if mode in ("redact", "opaque"):
            parts.append(
                f"{previous}drawbox=x={x}:y={y}:w={w}:h={h}:color=black:t=fill:enable='{enabled}'{output}"
            )
        elif mode == "blur":
            parts.append(
                f"{previous}split[pass{i}][crop{i}];[crop{i}]crop={w}:{h}:{x}:{y}:exact=1,gblur=sigma=20[blur{i}];[pass{i}][blur{i}]overlay={x}:{y}:format=yuv444:enable='{enabled}'{output}"
            )
        else:
            raise ValueError(f"Unknown region mode: {mode}")
        previous = output
    annotations = getattr(s, "annotations", [])
    if len(annotations) != len(annotation_paths):
        raise ValueError("Annotation artwork must be rendered before export")
    for i, item in enumerate(annotations, 1):
        t0 = max(0, (item.start_time - s.trim.start) / s.speed)
        t1 = min(duration, (item.end_time - s.trim.start) / s.speed)
        output = f"[annotation{i}]"
        parts.append(
            f"{previous}[{i}:v]overlay=0:0:format=auto:eof_action=repeat:enable='gte(t,{t0:.9f})*lt(t,{t1:.9f})'{output}"
        )
        previous = output
    parts.append(f"{previous}pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p[vout]")
    if has_audio:
        speed = s.speed
        tempo = []
        while speed < 0.5:
            tempo.append("atempo=0.5")
            speed /= 0.5
        while speed > 2:
            tempo.append("atempo=2")
            speed /= 2
        tempo.append(f"atempo={speed:.9f}")
        parts.append(
            f"[0:a]atrim=start={s.trim.start:.9f}:end={end:.9f},asetpts=PTS-STARTPTS,{','.join(tempo)}[aout]"
        )
    return ";".join(parts), duration


def export_command(session, output_path, has_audio=True, crf=18, annotation_paths=()):
    graph, duration = build_filter(session, has_audio, annotation_paths)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        session.video_path,
    ]
    for path in annotation_paths:
        cmd += ["-i", str(path)]
    cmd += [
        "-filter_complex",
        graph,
        "-map",
        "[vout]",
    ]
    if has_audio:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        str(crf),
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        "-nostats",
        str(output_path),
    ]
    return cmd, duration


class ProcessController:
    """Own a subprocess and stop it cooperatively without blocking a GUI."""

    def __init__(self):
        self._cancelled = threading.Event()
        self._process = None
        self._lock = threading.Lock()

    def cancel(self):
        self._cancelled.set()
        with self._lock:
            proc = self._process
            if proc is not None and proc.poll() is None:
                proc.terminate()
                threading.Thread(target=self._reap, args=(proc,), daemon=True).start()

    @staticmethod
    def _reap(proc):
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def _start(self, cmd, **kwargs):
        with self._lock:
            if self._cancelled.is_set():
                raise InterruptedError("Canceled")
            self._process = subprocess.Popen(cmd, **kwargs)
            return self._process
