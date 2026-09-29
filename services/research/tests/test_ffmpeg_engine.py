"""FFmpeg concatenation engine: pure command construction/parsing, the
orchestration in `FFmpegAssembler.assemble()` (via a fake, injected
`CommandRunner` - never a real subprocess), plus one real-ffmpeg integration
test (skipped if ffmpeg is not installed) using tiny synthetically-generated
clips - never the user's real Supabase-stored assets.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from research.ffmpeg_engine import (
    DEFAULT_CRF,
    DEFAULT_FPS,
    AssembledOutput,
    CommandResult,
    FFmpegAssembler,
    FFmpegNotFoundError,
    FFmpegProbeError,
    FFmpegProcessError,
    build_concat_command,
    build_concat_filter_complex,
    build_probe_duration_command,
    build_probe_resolution_command,
    build_scale_pad_filter,
    parse_probe_duration,
    parse_probe_resolution,
)

FFMPEG_INSTALLED = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# --- pure command construction ---------------------------------------------------------------------


def test_build_scale_pad_filter_produces_the_expected_chain():
    assert build_scale_pad_filter(0, 1280, 720) == (
        "[0:v]scale=1280:720:force_original_aspect_ratio=decrease,"
        "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p[v0]"
    )


def test_build_scale_pad_filter_uses_the_given_index_and_fps():
    assert "[2:v]" in build_scale_pad_filter(2, 100, 200, fps=24)
    assert "fps=24" in build_scale_pad_filter(2, 100, 200, fps=24)
    assert "[v2]" in build_scale_pad_filter(2, 100, 200, fps=24)


def test_build_concat_filter_complex_joins_every_input_in_order():
    result = build_concat_filter_complex(3, 1280, 720)
    assert result.count(";") == 3  # 3 per-input chains + 1 concat line, joined by 3 semicolons
    assert result.endswith("[v0][v1][v2]concat=n=3:v=1:a=0[outv]")


def test_build_concat_filter_complex_rejects_zero_inputs():
    with pytest.raises(ValueError):
        build_concat_filter_complex(0, 1280, 720)


def test_build_concat_command_shape():
    command = build_concat_command(
        "ffmpeg", ["a.mp4", "b.mp4"], "out.mp4", width=1280, height=720,
    )
    assert command[0] == "ffmpeg"
    assert command[1] == "-y"
    assert command.count("-i") == 2
    assert "a.mp4" in command and "b.mp4" in command
    assert "-filter_complex" in command
    assert command[command.index("-filter_complex") + 1] == build_concat_filter_complex(2, 1280, 720)
    assert command[command.index("-map") + 1] == "[outv]"
    assert command[-1] == "out.mp4"
    assert "-c:v" in command and command[command.index("-c:v") + 1] == "libx264"
    assert "-crf" in command and command[command.index("-crf") + 1] == str(DEFAULT_CRF)
    assert "-movflags" in command and command[command.index("-movflags") + 1] == "+faststart"


def test_build_concat_command_is_deterministic():
    a = build_concat_command("ffmpeg", ["a.mp4", "b.mp4"], "out.mp4", width=1280, height=720)
    b = build_concat_command("ffmpeg", ["a.mp4", "b.mp4"], "out.mp4", width=1280, height=720)
    assert a == b


def test_build_concat_command_rejects_no_inputs():
    with pytest.raises(ValueError):
        build_concat_command("ffmpeg", [], "out.mp4", width=1280, height=720)


def test_build_probe_commands_target_the_given_binary_and_file():
    assert build_probe_resolution_command("ffprobe", "clip.mp4")[0] == "ffprobe"
    assert build_probe_resolution_command("ffprobe", "clip.mp4")[-1] == "clip.mp4"
    assert build_probe_duration_command("ffprobe", "clip.mp4")[-1] == "clip.mp4"


def test_parse_probe_resolution():
    assert parse_probe_resolution(b"1280x720\n") == (1280, 720)


def test_parse_probe_resolution_rejects_unparseable_output():
    with pytest.raises(FFmpegProbeError, match="could not parse a resolution"):
        parse_probe_resolution(b"not a resolution")


def test_parse_probe_duration():
    assert parse_probe_duration(b"23.977000\n") == pytest.approx(23.977)


def test_parse_probe_duration_rejects_unparseable_output():
    with pytest.raises(FFmpegProbeError, match="could not parse a duration"):
        parse_probe_duration(b"N/A\n")


# --- FFmpegAssembler.assemble() orchestration, via a fake CommandRunner ----------------------------


class ScriptedRunner:
    """Simulates ffmpeg/ffprobe well enough for `assemble()`'s orchestration
    to be tested without a real subprocess: recognises which command it was
    given by its first argument, and - for the concat command - actually
    writes bytes to the expected output path, exactly as real ffmpeg would."""

    def __init__(self, *, resolution=(1280, 720), duration=12.0, ffmpeg_returncode=0, ffmpeg_stderr=b"",
                 skip_output_write=False):
        self.resolution = resolution
        self.duration = duration
        self.ffmpeg_returncode = ffmpeg_returncode
        self.ffmpeg_stderr = ffmpeg_stderr
        self.skip_output_write = skip_output_write
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> CommandResult:
        self.calls.append(args)
        if "-show_entries" in args and "stream=width,height" in args:
            w, h = self.resolution
            return CommandResult(0, f"{w}x{h}\n".encode(), b"")
        if "-show_entries" in args and "format=duration" in args:
            return CommandResult(0, f"{self.duration}\n".encode(), b"")
        # the concat (ffmpeg) command: the output path is always the last argument
        if self.ffmpeg_returncode == 0 and not self.skip_output_write:
            Path(args[-1]).write_bytes(b"fake-assembled-mp4-bytes")
        return CommandResult(self.ffmpeg_returncode, b"", self.ffmpeg_stderr)


def test_a_successful_assembly_returns_the_output_with_measured_duration():
    runner = ScriptedRunner(resolution=(1280, 720), duration=18.5)
    assembler = FFmpegAssembler(run=runner)
    result = assembler.assemble([b"clip-a", b"clip-b", b"clip-c"])
    assert isinstance(result, AssembledOutput)
    assert result.data == b"fake-assembled-mp4-bytes"
    assert result.mime_type == "video/mp4"
    assert result.output_duration_seconds == pytest.approx(18.5)
    assert result.metadata["width"] == 1280 and result.metadata["height"] == 720
    assert result.metadata["input_count"] == 3


def test_the_first_clips_resolution_is_used_as_the_common_target():
    runner = ScriptedRunner(resolution=(720, 1280))  # vertical, e.g. a real Veo 9:16 clip
    FFmpegAssembler(run=runner).assemble([b"clip-a", b"clip-b"])
    ffmpeg_call = next(c for c in runner.calls if "-filter_complex" in c)
    assert "scale=720:1280" in ffmpeg_call[ffmpeg_call.index("-filter_complex") + 1]


def test_assemble_requires_at_least_one_clip():
    with pytest.raises(ValueError):
        FFmpegAssembler(run=ScriptedRunner()).assemble([])


def test_ffmpeg_process_failure_is_a_process_error_with_stderr_tail():
    runner = ScriptedRunner(ffmpeg_returncode=1, ffmpeg_stderr=b"Unknown encoder 'libx264'")
    with pytest.raises(FFmpegProcessError) as info:
        FFmpegAssembler(run=runner).assemble([b"clip-a", b"clip-b"])
    assert info.value.returncode == 1
    assert "Unknown encoder" in info.value.stderr_tail


def test_ffmpeg_success_but_no_output_file_is_still_a_process_error():
    runner = ScriptedRunner(skip_output_write=True)
    with pytest.raises(FFmpegProcessError, match="produced no output file"):
        FFmpegAssembler(run=runner).assemble([b"clip-a"])


def test_a_probe_failure_on_the_first_clip_is_a_probe_error():
    def failing_probe(args: list[str]) -> CommandResult:
        if "-show_entries" in args and "stream=width,height" in args:
            return CommandResult(1, b"", b"ffprobe: no such file")
        return CommandResult(0, b"12.0\n", b"")

    with pytest.raises(FFmpegProbeError):
        FFmpegAssembler(run=failing_probe).assemble([b"clip-a"])


def test_from_settings_uses_the_configured_binaries():
    from research.config import Settings

    settings = Settings.from_env({"FFMPEG_PATH": "/custom/ffmpeg", "FFPROBE_PATH": "/custom/ffprobe"})
    runner = ScriptedRunner()
    FFmpegAssembler.from_settings(settings, run=runner).assemble([b"clip-a"])
    assert all(c[0] in ("/custom/ffmpeg", "/custom/ffprobe") for c in runner.calls)


def test_a_missing_binary_is_a_clear_not_found_error_not_a_bare_os_error():
    # ffprobe is called first (to detect the target resolution), so it must
    # also be pointed at a nonexistent path to isolate "the binary itself is
    # missing" from "ffprobe ran but the input wasn't a real video".
    assembler = FFmpegAssembler(
        ffmpeg_path="definitely-not-a-real-binary-xyz", ffprobe_path="definitely-not-a-real-binary-xyz",
    )
    with pytest.raises(FFmpegNotFoundError, match="install ffmpeg"):
        assembler.assemble([b"clip-a"])


# --- real ffmpeg integration test (skipped if ffmpeg is not installed) -----------------------------


@pytest.mark.skipif(not FFMPEG_INSTALLED, reason="ffmpeg/ffprobe not installed on PATH")
def test_real_ffmpeg_concatenates_two_synthetic_clips_into_one_playable_mp4(tmp_path):
    """The one test that exercises the REAL ffmpeg binary, confirming the
    command syntax this module builds is actually valid - not just
    plausible. Uses ffmpeg's own `lavfi` test-pattern generator for the two
    source clips (deliberately different sizes, to also exercise the
    scale/pad normalisation), never a real Veo/Gemini asset."""

    def make_clip(path: Path, *, size: str, seconds: int) -> bytes:
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate={DEFAULT_FPS}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(seconds), str(path),
            ],
            check=True, capture_output=True,
        )
        return path.read_bytes()

    clip_a = make_clip(tmp_path / "a.mp4", size="640x480", seconds=1)
    clip_b = make_clip(tmp_path / "b.mp4", size="320x240", seconds=1)  # different size than clip_a

    result = FFmpegAssembler().assemble([clip_a, clip_b])

    assert result.mime_type == "video/mp4"
    assert len(result.data) > 0
    # ~2 seconds total; generous tolerance for encoder/container overhead.
    assert 1.5 <= result.output_duration_seconds <= 3.0

    # And the output is genuinely a valid, playable video file: probe it for real.
    output_path = tmp_path / "final.mp4"
    output_path.write_bytes(result.data)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
         "default=noprint_wrappers=1:nokey=1", str(output_path)],
        check=True, capture_output=True,
    )
    assert float(probe.stdout.strip()) > 0
