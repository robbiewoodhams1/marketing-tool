"""FFmpeg-backed concatenation engine: the V1 concrete media-assembly step.

Takes already-downloaded video clip bytes (in production scene order) and
produces one finished MP4. Uses the `ffmpeg`/`ffprobe` CLI binaries directly
(no Python video-processing library, no vendor SDK - this is a local tool
dependency, not an API): the same "injectable, no hidden I/O" approach as
`research.gemini`/`research.veo`'s injectable transports, so tests never
invoke a real subprocess or need ffmpeg installed to run.

FFmpeg is a REQUIRED external dependency, not bundled with this service. If
it is not installed, `FFmpegNotFoundError` is raised with an actionable
message (e.g. `brew install ffmpeg` on macOS) rather than a bare
FileNotFoundError - see `research.assembly`'s module docstring.

Normalisation, deliberately V1-simple: input clips may come from different
media-generation runs (different Media Direction, a retried scene, or a
future different provider) and are not guaranteed to already share a
resolution/frame-rate/pixel-format, so each is scaled and padded to a common
target (the first clip's own resolution) before concatenation - "as
necessary for reliable concatenation" is exactly this: without it, ffmpeg's
concat filter refuses to join mismatched streams. The output has NO audio
track, even when a source clip has one: reliably joining heterogeneous
(or absent) audio streams is a separate problem this chapter does not need
to solve to produce one correctly-ordered, playable picture track - a future
chapter can add audio handling once voiceover/music/captions are in scope.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from research.config import Settings

DEFAULT_FFMPEG_PATH = "ffmpeg"
DEFAULT_FFPROBE_PATH = "ffprobe"
DEFAULT_FPS = 30
DEFAULT_CRF = 20
DEFAULT_TIMEOUT = 300.0  # seconds; generous for a handful of short vertical clips


class FFmpegError(Exception):
    """Base class for all assembly-engine failures."""


class FFmpegNotFoundError(FFmpegError):
    """The `ffmpeg`/`ffprobe` binary is not installed or not on PATH."""


class FFmpegTimeoutError(FFmpegError):
    """The process did not finish within the configured timeout."""


class FFmpegProcessError(FFmpegError):
    """ffmpeg/ffprobe ran and exited non-zero."""

    def __init__(self, message: str, *, returncode: int | None = None, stderr_tail: str | None = None):
        super().__init__(message)
        self.returncode = returncode
        self.stderr_tail = stderr_tail


class FFmpegProbeError(FFmpegError):
    """ffprobe's output could not be parsed into the value we needed."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


CommandRunner = Callable[[list[str]], CommandResult]


@dataclass(frozen=True)
class AssembledOutput:
    """One finished assembly, exactly as `research.assembly` needs it to
    persist and upload it."""

    data: bytes
    mime_type: str
    output_duration_seconds: float
    metadata: Mapping[str, Any] = field(default_factory=dict)


# --- pure command construction / output parsing (no I/O; fully unit-testable) ----------------------


def build_scale_pad_filter(index: int, width: int, height: int, fps: int = DEFAULT_FPS) -> str:
    """One input's normalisation chain: fit within width x height preserving
    aspect ratio, pad to exactly fill it (letterbox/pillarbox as needed,
    centred), fix the sample aspect ratio, resample to a common frame rate,
    and force a broadly-compatible pixel format - the standard recipe for
    making otherwise-mismatched clips concat-safe."""
    return (
        f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p[v{index}]"
    )


def build_concat_filter_complex(count: int, width: int, height: int, fps: int = DEFAULT_FPS) -> str:
    if count < 1:
        raise ValueError("count must be at least 1")
    per_input = [build_scale_pad_filter(i, width, height, fps) for i in range(count)]
    labels = "".join(f"[v{i}]" for i in range(count))
    # v=1:a=0 - video only, no audio track (see the module docstring).
    concat = f"{labels}concat=n={count}:v=1:a=0[outv]"
    return ";".join([*per_input, concat])


def build_concat_command(
    ffmpeg_path: str,
    input_paths: Sequence[str],
    output_path: str,
    *,
    width: int,
    height: int,
    fps: int = DEFAULT_FPS,
    crf: int = DEFAULT_CRF,
) -> list[str]:
    """The exact argv for one concatenation. Deterministic for the same
    inputs/target/output path - no randomness, no wall-clock-dependent flags."""
    if not input_paths:
        raise ValueError("build_concat_command requires at least one input path")
    args: list[str] = [ffmpeg_path, "-y"]
    for path in input_paths:
        args += ["-i", path]
    args += [
        "-filter_complex", build_concat_filter_complex(len(input_paths), width, height, fps),
        "-map", "[outv]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",  # metadata at the front - playable while still downloading/serving
        output_path,
    ]
    return args


def build_probe_resolution_command(ffprobe_path: str, path: str) -> list[str]:
    return [
        ffprobe_path, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", path,
    ]


def build_probe_duration_command(ffprobe_path: str, path: str) -> list[str]:
    return [
        ffprobe_path, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
    ]


_RESOLUTION_RE = re.compile(r"(\d+)x(\d+)")


def parse_probe_resolution(stdout: bytes) -> tuple[int, int]:
    text = stdout.decode("utf-8", errors="replace").strip()
    first_line = text.splitlines()[0] if text else ""
    match = _RESOLUTION_RE.fullmatch(first_line)
    if not match:
        raise FFmpegProbeError(f"could not parse a resolution from ffprobe output: {text!r}")
    return int(match.group(1)), int(match.group(2))


def parse_probe_duration(stdout: bytes) -> float:
    text = stdout.decode("utf-8", errors="replace").strip()
    first_line = text.splitlines()[0] if text else ""
    try:
        return float(first_line)
    except ValueError as exc:
        raise FFmpegProbeError(f"could not parse a duration from ffprobe output: {text!r}") from exc


def _tail(stderr: bytes, limit: int = 2000) -> str:
    return stderr.decode("utf-8", errors="replace")[-limit:]


def _subprocess_runner(timeout: float) -> CommandRunner:
    def run(args: list[str]) -> CommandResult:
        try:
            completed = subprocess.run(args, capture_output=True, timeout=timeout)
        except FileNotFoundError as exc:
            raise FFmpegNotFoundError(
                f"{args[0]!r} was not found on PATH - install ffmpeg (e.g. `brew install ffmpeg` on "
                "macOS, `apt install ffmpeg` on Debian/Ubuntu) or set FFMPEG_PATH/FFPROBE_PATH"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise FFmpegTimeoutError(f"{args[0]!r} did not finish within {timeout:g}s") from exc
        return CommandResult(returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)

    return run


# --- the engine ------------------------------------------------------------------------------------


class FFmpegAssembler:
    """Concatenates clips into one MP4. Errors become `FFmpegError`
    subclasses; never raises a bare subprocess/OS exception."""

    def __init__(
        self,
        *,
        ffmpeg_path: str = DEFAULT_FFMPEG_PATH,
        ffprobe_path: str = DEFAULT_FFPROBE_PATH,
        run: CommandRunner | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        workdir: str | None = None,  # parent dir for the temp workspace; None = OS default (real use)
    ):
        self._ffmpeg = ffmpeg_path
        self._ffprobe = ffprobe_path
        self._run = run or _subprocess_runner(timeout)
        self._workdir = workdir

    @classmethod
    def from_settings(cls, settings: Settings, *, run: CommandRunner | None = None) -> FFmpegAssembler:
        return cls(
            ffmpeg_path=settings.ffmpeg_path or DEFAULT_FFMPEG_PATH,
            ffprobe_path=settings.ffprobe_path or DEFAULT_FFPROBE_PATH,
            run=run,
        )

    def assemble(self, clips: Sequence[bytes]) -> AssembledOutput:
        """`clips` must already be in final output order. Raises
        `FFmpegError` subclasses on any failure; never partially uploads or
        silently drops a clip."""
        if not clips:
            raise ValueError("assemble() requires at least one clip")
        with tempfile.TemporaryDirectory(dir=self._workdir) as workdir:
            input_paths = []
            for i, data in enumerate(clips):
                path = os.path.join(workdir, f"input-{i}.mp4")
                Path(path).write_bytes(data)
                input_paths.append(path)

            width, height = self._probe_resolution(input_paths[0])
            output_path = os.path.join(workdir, "assembled.mp4")
            command = build_concat_command(self._ffmpeg, input_paths, output_path, width=width, height=height)
            result = self._run(command)
            if result.returncode != 0:
                raise FFmpegProcessError(
                    f"ffmpeg exited with status {result.returncode}: {_tail(result.stderr)}",
                    returncode=result.returncode, stderr_tail=_tail(result.stderr),
                )
            if not os.path.exists(output_path):
                raise FFmpegProcessError("ffmpeg reported success but produced no output file")

            duration = self._probe_duration(output_path)
            data = Path(output_path).read_bytes()
            return AssembledOutput(
                data=data, mime_type="video/mp4", output_duration_seconds=duration,
                metadata={"width": width, "height": height, "input_count": len(clips), "fps": DEFAULT_FPS},
            )

    def _probe_resolution(self, path: str) -> tuple[int, int]:
        result = self._run(build_probe_resolution_command(self._ffprobe, path))
        if result.returncode != 0:
            raise FFmpegProbeError(f"ffprobe failed probing resolution: {_tail(result.stderr)}")
        return parse_probe_resolution(result.stdout)

    def _probe_duration(self, path: str) -> float:
        result = self._run(build_probe_duration_command(self._ffprobe, path))
        if result.returncode != 0:
            raise FFmpegProbeError(f"ffprobe failed probing duration: {_tail(result.stderr)}")
        return parse_probe_duration(result.stdout)
