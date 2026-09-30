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

Assembly V2 audio-sync policy (`assemble_with_voiceover`, driven by
`research.assembly_v2`): now that voiceover exists as its own generated
asset (`research.voice`), each scene's video is synchronised against its own
voiceover clip before concatenation. The source video's OWN generated audio
remains muted (the `[0:a]` stream is never referenced by
`build_scene_sync_filter_complex`) - voiceover is the sole, authoritative
spoken audio; mixing in Veo's own scene audio, ambient audio, music or SFX is
explicitly future scope (see that module's docstring), not attempted here.

Scene and voiceover durations are almost never identical, and spoken
narration must never be cut to fit a scene's original requested duration.
The deterministic policy (`sync_strategy_for`) is: a scene's final duration
is always `max(video_duration, voice_duration)` -

  * voiceover shorter than the video -> the video keeps its full actual
    length; the voiceover plays from the start; silence (`apad`) fills the
    remainder
  * voiceover longer than the video -> the video's final frame is frozen
    (`tpad=stop_mode=clone`) for the extra time the voiceover needs, so
    narration is never truncated
  * equal -> neither adjustment applies

No TTS is ever regenerated to fix a mismatch, and no LLM is involved in this
decision - it is pure, deterministic ffmpeg command construction from two
ffprobe-measured durations. Each synchronised scene is encoded once (H.264 +
AAC, the same target resolution/fps/pixel-format/audio-format for every
scene), then the six scenes are concatenated with a second, simpler
concat filter (`build_audio_video_concat_command`) that carries both the
video and audio streams through - no further per-scene normalisation is
needed there, since `build_scene_sync_command` already guaranteed identical
output properties for every scene.

Captions V1 (`burn_captions`, driven by `research.assembly_v2` via
`research.captions`/`research.caption_timing`): burns a fixed set of timed
text cards onto an already-assembled video. Deliberately NOT built on
ffmpeg's `drawtext`/`subtitles`/`ass` filters - those need an ffmpeg build
with libfreetype/libass, which is NOT guaranteed (in fact, the plain Homebrew
`ffmpeg` formula this project already depends on ships WITHOUT them).
Instead, each caption card is rendered to a full-frame, mostly-transparent
PNG with Pillow (a pure-Python-adjacent, always-available dependency - see
pyproject.toml), and burned in with ffmpeg's `overlay` filter (universally
available), one `overlay=...:enable='between(t,start,end)'` stage per card,
chained in a single `-filter_complex`. The source audio stream is passed
through completely unchanged (`-c:a copy`) - captions are a video-only
overlay and never touch audio.
"""

from __future__ import annotations

import io
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

# Caption card rendering (burn_captions / _render_caption_png), all as a
# fraction of the video's own height/width so it scales to whatever
# resolution the source video actually is (never a hard-coded 1080x1920).
CAPTION_FONT_HEIGHT_RATIO = 0.045  # font size, as a fraction of video height
CAPTION_SIDE_MARGIN_RATIO = 0.08  # left/right safe margin, as a fraction of video width
CAPTION_BOTTOM_MARGIN_RATIO = 0.10  # bottom safe margin, as a fraction of video height
CAPTION_LINE_GAP_RATIO = 0.25  # gap between wrapped lines, as a fraction of font size
CAPTION_MIN_FONT_SIZE = 24
CAPTION_STROKE_WIDTH_DIVISOR = 16  # stroke width = max(2, font_size // this)


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


@dataclass(frozen=True)
class SceneMediaInput:
    """One scene's already-downloaded source bytes, exactly as
    `research.assembly_v2` needs to hand them to the engine - in final scene
    order. `voice_bytes` is the scene's voiceover audio (the only audio this
    engine ever puts in the output - see the module docstring's audio
    policy)."""

    scene_number: int
    video_bytes: bytes
    voice_bytes: bytes


@dataclass(frozen=True)
class SyncedSceneResult:
    """What actually happened when synchronising one scene's video against
    its voiceover - the actual (ffprobe-measured) source durations, the
    actual (ffprobe-measured) duration of the synchronised result, and which
    of the three deterministic strategies applied. Never invented: every
    number here comes from probing a real file."""

    scene_number: int
    video_duration_seconds: float
    voice_duration_seconds: float
    scene_duration_seconds: float
    sync_strategy: str


# The three possible outcomes of `_sync_strategy_for` below - named so
# `research.assembly_v2` can persist a stable string, not a description that
# might be reworded later.
SYNC_STRATEGY_VIDEO_EXTENDED_FROZEN_FRAME = "video_extended_frozen_frame"
SYNC_STRATEGY_AUDIO_PADDED_WITH_SILENCE = "audio_padded_with_silence"
SYNC_STRATEGY_EQUAL_NO_ADJUSTMENT = "equal_no_adjustment"


@dataclass(frozen=True)
class AssembledOutputV2:
    """One finished, voiceover-synchronised assembly (Assembly V2) - the
    concatenation of `scene_results`' individually-synchronised scenes.
    `output_duration_seconds` is independently measured (ffprobe) from the
    final concatenated file, never assumed to equal the sum of
    `scene_results`' own durations."""

    data: bytes
    mime_type: str
    output_duration_seconds: float
    scene_results: tuple[SyncedSceneResult, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CaptionCue:
    """One caption card and the exact FINAL-VIDEO-timeline window (in
    seconds) it should be visible for - deliberately the only thing
    `burn_captions` knows about a caption: it has no idea whether the text
    came from `research.captions`, a test fixture, or anything else."""

    text: str
    start_seconds: float
    end_seconds: float


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


def sync_strategy_for(video_duration: float, voice_duration: float, *, tolerance: float = 0.01) -> str:
    """The deterministic Assembly V2 timing policy, in one place (see the
    module docstring's "Assembly V2 audio-sync policy" section for the full
    rationale): never cut spoken narration, so the scene's final duration is
    always `max(video_duration, voice_duration)` -

      * voice shorter than video -> keep the video's full length, voiceover
        plays from the start, silence fills the remainder
      * voice longer than video -> never truncated; the video's final frame
        is frozen (extended) for the extra time the voiceover needs
      * (near-)equal -> neither adjustment is meaningfully applied

    `tolerance` absorbs float/ffprobe noise so two durations a few
    milliseconds apart are not treated as needing an adjustment."""
    if voice_duration - video_duration > tolerance:
        return SYNC_STRATEGY_VIDEO_EXTENDED_FROZEN_FRAME
    if video_duration - voice_duration > tolerance:
        return SYNC_STRATEGY_AUDIO_PADDED_WITH_SILENCE
    return SYNC_STRATEGY_EQUAL_NO_ADJUSTMENT


def build_scene_sync_filter_complex(
    *, width: int, height: int, fps: int = DEFAULT_FPS, video_extend_seconds: float, audio_pad_seconds: float
) -> str:
    """One scene's synchronisation chain: input 0 is the scene's VIDEO, input
    1 is the scene's VOICEOVER audio - the source video's own audio is never
    referenced (`[0:a]` never appears anywhere in this filter graph), which
    IS the mute-source-audio policy (see the module docstring); the output
    audio comes only from `[1:a]`.

    Video is normalised exactly as V1 does (scale/pad/setsar/fps/format -
    `build_scale_pad_filter`), then, only if `video_extend_seconds` is
    positive, its final frame is frozen (cloned) for that many extra seconds
    via `tpad` - the "extend the visual, never cut the narration" half of the
    policy. Audio is padded with trailing silence via `apad`, only if
    `audio_pad_seconds` is positive - the "keep the video's full length,
    silence fills the remainder" half. Both amounts are computed by the
    caller from real ffprobe-measured durations; at most one of them is ever
    positive for a given scene (see `sync_strategy_for`)."""
    video_chain = build_scale_pad_filter(0, width, height, fps).removesuffix("[v0]")
    if video_extend_seconds > 0:
        video_chain += f",tpad=stop_mode=clone:stop_duration={video_extend_seconds:.3f}"
    video_chain += "[vout]"

    audio_chain = "[1:a]"
    audio_chain += f"apad=pad_dur={audio_pad_seconds:.3f}" if audio_pad_seconds > 0 else "anull"
    audio_chain += "[aout]"

    return f"{video_chain};{audio_chain}"


def build_scene_sync_command(
    ffmpeg_path: str,
    video_path: str,
    voice_path: str,
    output_path: str,
    *,
    width: int,
    height: int,
    fps: int = DEFAULT_FPS,
    scene_duration_seconds: float,
    video_extend_seconds: float,
    audio_pad_seconds: float,
    crf: int = DEFAULT_CRF,
) -> list[str]:
    """The exact argv for synchronising one scene. `-t scene_duration_seconds`
    on the output is a hard cap, not the mechanism that achieves sync (the
    filters above do that) - it only guards against the sub-frame/sub-sample
    rounding `tpad`/`apad` can leave behind, so every synced scene's own
    measured duration lands on exactly the same deterministic target."""
    filter_complex = build_scene_sync_filter_complex(
        width=width, height=height, fps=fps,
        video_extend_seconds=video_extend_seconds, audio_pad_seconds=audio_pad_seconds,
    )
    return [
        ffmpeg_path, "-y",
        "-i", video_path,
        "-i", voice_path,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        "-t", f"{scene_duration_seconds:.3f}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ar", "44100",
        "-ac", "2",
        output_path,
    ]


def build_audio_video_concat_filter_complex(count: int) -> str:
    if count < 1:
        raise ValueError("count must be at least 1")
    labels = "".join(f"[{i}:v][{i}:a]" for i in range(count))
    # v=1:a=1 - unlike V1's picture-only concat, every synced scene now
    # carries the voiceover-only audio stream `build_scene_sync_command`
    # produced for it, and that is what gets carried through here.
    return f"{labels}concat=n={count}:v=1:a=1[outv][outa]"


def build_audio_video_concat_command(
    ffmpeg_path: str, input_paths: Sequence[str], output_path: str, *, crf: int = DEFAULT_CRF
) -> list[str]:
    """Concatenates already-synchronised scene files (same resolution/fps/
    pixel format/audio format, by construction of `build_scene_sync_command`)
    - no further scale/pad normalisation needed here, unlike V1's
    `build_concat_command`."""
    if not input_paths:
        raise ValueError("build_audio_video_concat_command requires at least one input path")
    args: list[str] = [ffmpeg_path, "-y"]
    for path in input_paths:
        args += ["-i", path]
    args += [
        "-filter_complex", build_audio_video_concat_filter_complex(len(input_paths)),
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-movflags", "+faststart",
        output_path,
    ]
    return args


def build_caption_overlay_filter_complex(cues: Sequence[tuple[float, float]]) -> str:
    """One `overlay` stage per caption cue, chained in order: input 0 is the
    base video, inputs 1..N are that cue's own full-frame caption PNG (see
    `_render_caption_png`) - each visible only during its own
    `[start, end)` window via ffmpeg's `enable='between(t,start,end)'`
    expression, and otherwise fully transparent so no other cue's window is
    affected. The final stage's output is `[vout]`."""
    if not cues:
        raise ValueError("cues must not be empty")
    parts = []
    prev_label = "0:v"
    last = len(cues) - 1
    for i, (start, end) in enumerate(cues):
        png_input = i + 1  # input 0 is the base video
        out_label = "vout" if i == last else f"vcap{i}"
        parts.append(
            f"[{prev_label}][{png_input}:v]overlay=enable='between(t,{start:.3f},{end:.3f})'[{out_label}]"
        )
        prev_label = out_label
    return ";".join(parts)


def build_caption_burn_command(
    ffmpeg_path: str,
    video_path: str,
    caption_png_paths: Sequence[str],
    output_path: str,
    *,
    cues: Sequence[tuple[float, float]],
    crf: int = DEFAULT_CRF,
) -> list[str]:
    """The exact argv for burning `caption_png_paths` (one per cue, in the
    same order as `cues`) onto `video_path`. Audio is passed through
    completely unchanged (`-c:a copy`) - captions never touch it."""
    if not caption_png_paths:
        raise ValueError("build_caption_burn_command requires at least one caption image")
    if len(caption_png_paths) != len(cues):
        raise ValueError("caption_png_paths and cues must be the same length")
    args = [ffmpeg_path, "-y", "-i", video_path]
    for path in caption_png_paths:
        args += ["-i", path]
    args += [
        "-filter_complex", build_caption_overlay_filter_complex(cues),
        "-map", "[vout]",
        "-map", "0:a",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    return args


def _wrap_caption_for_pixel_width(text: str, *, draw: Any, font: Any, max_width: float) -> list[str]:
    """Greedy word-wrap using the font's OWN measured pixel width (via the
    same Pillow `draw`/`font` that will actually render it), so a caption
    card never overruns the frame regardless of resolution or font metrics -
    unlike `research.caption_timing`'s character-count wrapping, which only
    decides how much text goes in one timed segment, not how it visually
    breaks into lines."""
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        width = draw.textbbox((0, 0), candidate, font=font)[2]
        if current and width > max_width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines or [text]


def _render_caption_png(text: str, *, width: int, height: int) -> bytes:
    """One full-frame (width x height), mostly-transparent PNG: `text`
    (already word-wrapped to fit) drawn near the bottom, centred, in white
    with a black outline for contrast against any background - see the
    module docstring for why Pillow/`overlay` rather than ffmpeg's own text
    filters. Pure function of its arguments; imports Pillow lazily so
    importing this module has no cost for callers that never burn
    captions."""
    from PIL import Image, ImageDraw, ImageFont

    font_size = max(CAPTION_MIN_FONT_SIZE, round(height * CAPTION_FONT_HEIGHT_RATIO))
    stroke_width = max(2, font_size // CAPTION_STROKE_WIDTH_DIVISOR)
    font = ImageFont.load_default(size=font_size)

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    side_margin = round(width * CAPTION_SIDE_MARGIN_RATIO)
    max_line_width = width - 2 * side_margin
    lines = _wrap_caption_for_pixel_width(text, draw=draw, font=font, max_width=max_line_width)

    line_gap = round(font_size * CAPTION_LINE_GAP_RATIO)
    line_boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width) for line in lines]
    line_heights = [box[3] - box[1] for box in line_boxes]
    block_height = sum(line_heights) + line_gap * (len(lines) - 1)
    bottom_margin = round(height * CAPTION_BOTTOM_MARGIN_RATIO)
    y = height - bottom_margin - block_height

    for line, box, line_height in zip(lines, line_boxes, line_heights):
        line_width = box[2] - box[0]
        x = (width - line_width) / 2
        draw.text(
            (x, y), line, font=font, fill="white", stroke_width=stroke_width, stroke_fill="black",
        )
        y += line_height + line_gap

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


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

    def assemble_with_voiceover(self, scenes: Sequence[SceneMediaInput]) -> AssembledOutputV2:
        """Assembly V2: synchronise each scene's video against its own
        voiceover (see `sync_strategy_for`/`build_scene_sync_command` for the
        deterministic timing policy - never cuts spoken narration), then
        concatenate the synchronised scenes into one finished MP4 with AAC
        audio. `scenes` must already be in final output order. Raises
        `FFmpegError` subclasses on any failure; never partially uploads or
        silently drops a scene."""
        if not scenes:
            raise ValueError("assemble_with_voiceover() requires at least one scene")
        with tempfile.TemporaryDirectory(dir=self._workdir) as workdir:
            video_paths = []
            voice_paths = []
            for i, scene in enumerate(scenes):
                video_path = os.path.join(workdir, f"video-{i}.mp4")
                Path(video_path).write_bytes(scene.video_bytes)
                video_paths.append(video_path)
                voice_path = os.path.join(workdir, f"voice-{i}.audio")
                Path(voice_path).write_bytes(scene.voice_bytes)
                voice_paths.append(voice_path)

            width, height = self._probe_resolution(video_paths[0])

            synced_paths: list[str] = []
            scene_results: list[SyncedSceneResult] = []
            for i, scene in enumerate(scenes):
                video_duration = self._probe_duration(video_paths[i])
                voice_duration = self._probe_duration(voice_paths[i])
                scene_duration = max(video_duration, voice_duration)
                strategy = sync_strategy_for(video_duration, voice_duration)
                video_extend = max(0.0, scene_duration - video_duration)
                audio_pad = max(0.0, scene_duration - voice_duration)

                synced_path = os.path.join(workdir, f"synced-{i}.mp4")
                command = build_scene_sync_command(
                    self._ffmpeg, video_paths[i], voice_paths[i], synced_path,
                    width=width, height=height, scene_duration_seconds=scene_duration,
                    video_extend_seconds=video_extend, audio_pad_seconds=audio_pad,
                )
                result = self._run(command)
                if result.returncode != 0:
                    raise FFmpegProcessError(
                        f"ffmpeg exited with status {result.returncode} syncing scene "
                        f"{scene.scene_number}: {_tail(result.stderr)}",
                        returncode=result.returncode, stderr_tail=_tail(result.stderr),
                    )
                if not os.path.exists(synced_path):
                    raise FFmpegProcessError(
                        f"ffmpeg reported success but produced no synced output for scene {scene.scene_number}"
                    )

                measured_scene_duration = self._probe_duration(synced_path)
                scene_results.append(SyncedSceneResult(
                    scene_number=scene.scene_number, video_duration_seconds=video_duration,
                    voice_duration_seconds=voice_duration, scene_duration_seconds=measured_scene_duration,
                    sync_strategy=strategy,
                ))
                synced_paths.append(synced_path)

            output_path = os.path.join(workdir, "assembled.mp4")
            command = build_audio_video_concat_command(self._ffmpeg, synced_paths, output_path)
            result = self._run(command)
            if result.returncode != 0:
                raise FFmpegProcessError(
                    f"ffmpeg exited with status {result.returncode} concatenating synced scenes: "
                    f"{_tail(result.stderr)}",
                    returncode=result.returncode, stderr_tail=_tail(result.stderr),
                )
            if not os.path.exists(output_path):
                raise FFmpegProcessError("ffmpeg reported success but produced no output file")

            duration = self._probe_duration(output_path)
            data = Path(output_path).read_bytes()
            return AssembledOutputV2(
                data=data, mime_type="video/mp4", output_duration_seconds=duration,
                scene_results=tuple(scene_results),
                metadata={"width": width, "height": height, "input_count": len(scenes), "fps": DEFAULT_FPS},
            )

    def burn_captions(self, video_bytes: bytes, cues: Sequence[CaptionCue], *, width: int, height: int) -> AssembledOutput:
        """Burn `cues` into `video_bytes` (an already-assembled video, e.g.
        `assemble_with_voiceover`'s own output) and return the result.
        `width`/`height` should be the video's own actual resolution (e.g.
        `AssembledOutputV2.metadata["width"/"height"]`) - never assumed. The
        source audio stream is carried through byte-for-byte unchanged
        (`-c:a copy`; see `build_caption_burn_command`). Raises
        `FFmpegError` subclasses on any failure."""
        if not cues:
            raise ValueError("burn_captions() requires at least one caption cue")
        with tempfile.TemporaryDirectory(dir=self._workdir) as workdir:
            video_path = os.path.join(workdir, "input.mp4")
            Path(video_path).write_bytes(video_bytes)

            png_paths = []
            for i, cue in enumerate(cues):
                png_path = os.path.join(workdir, f"caption-{i}.png")
                Path(png_path).write_bytes(_render_caption_png(cue.text, width=width, height=height))
                png_paths.append(png_path)

            output_path = os.path.join(workdir, "captioned.mp4")
            command = build_caption_burn_command(
                self._ffmpeg, video_path, png_paths, output_path,
                cues=[(c.start_seconds, c.end_seconds) for c in cues],
            )
            result = self._run(command)
            if result.returncode != 0:
                raise FFmpegProcessError(
                    f"ffmpeg exited with status {result.returncode} burning captions: {_tail(result.stderr)}",
                    returncode=result.returncode, stderr_tail=_tail(result.stderr),
                )
            if not os.path.exists(output_path):
                raise FFmpegProcessError("ffmpeg reported success but produced no captioned output file")

            duration = self._probe_duration(output_path)
            data = Path(output_path).read_bytes()
            return AssembledOutput(
                data=data, mime_type="video/mp4", output_duration_seconds=duration,
                metadata={"width": width, "height": height, "caption_count": len(cues)},
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
